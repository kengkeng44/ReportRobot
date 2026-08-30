"""notion_db 的 schema 建立 / 遷移 / relation 解析測試。

重點行為：
1. 建 DB 分兩階段 —— 先建非 relation 欄位，再 update 補 relation
   （Notion API 要求 relation 的 database_id 必須已存在）
2. 重用既有 DB 時要補上缺少的欄位（schema migration），但不動既有欄位
3. 任何失敗都 graceful，不 raise
"""

import pytest

import notion_db


class FakeDatabases:
    def __init__(self, store):
        self._store = store
        self.create_calls = []
        self.update_calls = []
        self.fail_create_for = set()

    def create(self, **kwargs):
        title = "".join(t["text"]["content"] for t in kwargs["title"])
        if title in self.fail_create_for:
            raise RuntimeError(f"boom: {title}")
        db_id = f"db_{title}"
        self.create_calls.append((title, kwargs["properties"]))
        self._store[db_id] = {
            "id": db_id,
            "title": kwargs["title"],
            "parent": {"type": "page_id", "page_id": kwargs["parent"]["page_id"]},
            "properties": dict(kwargs["properties"]),
        }
        return self._store[db_id]

    def update(self, database_id, **kwargs):
        self.update_calls.append((database_id, kwargs.get("properties", {})))
        self._store[database_id]["properties"].update(kwargs.get("properties", {}))
        return self._store[database_id]

    def retrieve(self, database_id):
        return self._store[database_id]


def _page_title(page):
    blocks = ((page.get("properties", {}) or {}).get("title", {}) or {}).get("title", []) or []
    return "".join(b.get("plain_text", "") for b in blocks)


class FakePages:
    def __init__(self, page_store):
        self._store = page_store
        self.created = []
        self.fail_create_for = set()

    def create(self, **kwargs):
        self.created.append(kwargs)
        props = kwargs.get("properties", {}) or {}
        blocks = (props.get("title", {}) or {}).get("title", []) or []
        title = "".join(t.get("text", {}).get("content", "") for t in blocks)

        if title and title in self.fail_create_for:
            raise RuntimeError(f"boom: {title}")

        page_id = f"page_{title}" if title else f"page_{len(self.created)}"
        if title:
            self._store[page_id] = {
                "id": page_id,
                "object": "page",
                "parent": kwargs.get("parent", {}),
                "properties": {
                    "title": {"title": [{"plain_text": title, "text": {"content": title}}]}
                },
            }
        return {"id": page_id}

    def update(self, page_id, **kwargs):
        return {"id": page_id}


class FakeClient:
    def __init__(self, parent_page, preexisting=None, preexisting_pages=None):
        self._store = dict(preexisting or {})
        self._pages = dict(preexisting_pages or {})
        self.databases = FakeDatabases(self._store)
        self.pages = FakePages(self._pages)
        self._parent_page = parent_page

    def search(self, query, filter=None):
        kind = (filter or {}).get("value", "database")
        out = []
        if kind == "page":
            for page in self._pages.values():
                if _page_title(page) == query:
                    out.append(page)
        else:
            for db in self._store.values():
                title = "".join(b.get("plain_text", "") for b in db.get("title", []))
                if title == query:
                    out.append(db)
        return {"results": out}

    def db_parent_of(self, db_id):
        return (self._store[db_id].get("parent") or {}).get("page_id")


@pytest.fixture
def notion(monkeypatch):
    """把 notion_db 接到 FakeClient，並清掉 module 級 cache。"""
    parent = "parentpage32charhexxxxxxxxxxxxxx"
    client = FakeClient(parent)

    monkeypatch.setattr(notion_db, "_PARENT_PAGE", parent)
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_client", client)
    monkeypatch.setattr(notion_db, "_db_id_cache", {})
    monkeypatch.setattr(notion_db, "_section_page_cache", {})
    return client


def _title_block(name):
    return [{"plain_text": name, "type": "text", "text": {"content": name}}]


# ── 兩階段建立 ────────────────────────────────────────────

def test_create_skips_relation_props_on_first_pass(notion):
    """建立時 properties 不能含 relation —— 目標 DB 當下可能還不存在。"""
    notion_db.get_or_create_db("信用卡帳單")

    created = dict(notion.databases.create_calls)
    props = created["信用卡帳單"]
    assert "卡片" not in props, "relation 欄位不該出現在第一階段 create"
    assert "應繳總額" in props


def test_relation_added_in_second_pass_with_real_db_id(notion):
    """第二階段用 databases.update 補 relation，且目標要被解析成真實 db_id。"""
    notion_db.get_or_create_db("信用卡帳單")

    updates = [u for u in notion.databases.update_calls if "卡片" in u[1]]
    assert updates, "應該有一次 update 補上『卡片』relation"

    _, props = updates[0]
    assert props["卡片"]["relation"]["database_id"] == "db_帳戶"


def test_relation_target_db_gets_created(notion):
    """relation 指向的 DB 若不存在，要一併建出來。"""
    notion_db.get_or_create_db("信用卡帳單")

    created_titles = [t for t, _ in notion.databases.create_calls]
    assert "帳戶" in created_titles


def test_relation_target_failure_does_not_raise(notion):
    """目標 DB 建立失敗時，主 DB 仍要能用，只是少了 relation。"""
    notion.databases.fail_create_for.add("帳戶")

    db_id = notion_db.get_or_create_db("信用卡帳單")

    assert db_id == "db_信用卡帳單"
    assert not [u for u in notion.databases.update_calls if "卡片" in u[1]]


def test_transactions_have_no_account_relation(notion):
    """交易明細不該再有『帳戶』relation。

    2026-08-25 移除：transaction_add 從來沒寫過這欄，帳戶 DB 也是 0 筆，
    留著只是一個永遠空的欄位。卡片辨識用「卡末四碼」文字欄就夠。
    """
    assert "交易明細" not in notion_db._RELATIONS

    notion_db.get_or_create_db("交易明細")

    assert not [u for u in notion.databases.update_calls if "帳戶" in u[1]]
    created_titles = [t for t, _ in notion.databases.create_calls]
    assert "帳戶" not in created_titles, "不該再為了 relation 順手建出帳戶 DB"


# ── schema 遷移（既有 DB 補欄位）──────────────────────────

def test_existing_db_gets_missing_props_added(monkeypatch):
    """Todos DB 早就存在且沒有『分類』欄位 —— 重用時要補上。"""
    parent = "parentpage32charhexxxxxxxxxxxxxx"
    preexisting = {
        "db_Todos": {
            "id": "db_Todos",
            "title": _title_block("Todos"),
            "parent": {"type": "page_id", "page_id": parent},
            "properties": {
                "Name": {"title": {}},
                "UserId": {"rich_text": {}},
                "Done": {"checkbox": {}},
                "LocalId": {"number": {"format": "number"}},
            },
        }
    }
    client = FakeClient(parent, preexisting)
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", parent)
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_client", client)
    monkeypatch.setattr(notion_db, "_db_id_cache", {})

    db_id = notion_db.get_or_create_db("Todos")

    assert db_id == "db_Todos"
    added = [u for u in client.databases.update_calls if "分類" in u[1]]
    assert added, "既有 Todos DB 應該被補上『分類』欄位"

    options = added[0][1]["分類"]["select"]["options"]
    names = {o["name"] for o in options}
    assert names == {"工作", "生活", "我的專案"}


def test_existing_props_are_not_overwritten(monkeypatch):
    """遷移只新增缺少的欄位，既有欄位定義不能被蓋掉。"""
    parent = "parentpage32charhexxxxxxxxxxxxxx"
    preexisting = {
        "db_Todos": {
            "id": "db_Todos",
            "title": _title_block("Todos"),
            "parent": {"type": "page_id", "page_id": parent},
            "properties": {
                "Name": {"title": {}},
                "UserId": {"rich_text": {}},
                "Done": {"checkbox": {}},
                "LocalId": {"number": {"format": "number"}},
                "分類": {"select": {"options": [{"name": "自訂", "color": "red"}]}},
            },
        }
    }
    client = FakeClient(parent, preexisting)
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", parent)
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_client", client)
    monkeypatch.setattr(notion_db, "_db_id_cache", {})

    notion_db.get_or_create_db("Todos")

    touched = [u for u in client.databases.update_calls if "分類" in u[1]]
    assert not touched, "既有的『分類』欄位不該被覆寫"


# ── 兩個模板的 schema 完整性 ──────────────────────────────

@pytest.mark.parametrize("name", [
    "帳戶", "交易明細", "信用卡帳單", "持倉", "淨值快照",
    "食材庫存", "食譜", "本週菜單", "採購清單",
])
def test_all_new_dbs_are_defined(name):
    assert name in notion_db._SCHEMAS, f"{name} 未定義 schema"


def test_transaction_has_fingerprint_for_dedup(notion):
    """去重靠 Fingerprint，缺了就會寫重複資料。"""
    notion_db.get_or_create_db("交易明細")
    created = dict(notion.databases.create_calls)
    assert "Fingerprint" in created["交易明細"]


def test_todos_category_options_are_the_three_agreed_ones():
    options = notion_db._SCHEMAS["Todos"]["分類"]["select"]["options"]
    assert [o["name"] for o in options] == ["工作", "生活", "我的專案"]


# ── 待辦分類 ──────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("工作", "工作"), ("work", "工作"), ("公事", "工作"),
    ("生活", "生活"), ("life", "生活"),
    ("我的專案", "我的專案"), ("專案", "我的專案"), ("project", "我的專案"),
    ("  專案  ", "我的專案"),           # 前後空白
    ("PROJECT", "我的專案"),            # 大小寫
])
def test_normalize_category_accepts_aliases(raw, expected):
    assert notion_db.normalize_todo_category(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "亂打的東西"])
def test_normalize_category_falls_back_to_default(raw):
    """認不出來就歸「生活」—— 留空會讓 Notion 分類檢視漏掉這筆。"""
    assert notion_db.normalize_todo_category(raw) == "生活"


def test_todos_create_writes_category(notion):
    notion_db.todos_create("U123", "買菜", 1, category="專案")

    props = notion.pages.created[0]["properties"]
    assert props["分類"]["select"]["name"] == "我的專案"


def test_todos_create_without_category_still_writes_one(notion):
    notion_db.todos_create("U123", "倒垃圾", 1)

    props = notion.pages.created[0]["properties"]
    assert props["分類"]["select"]["name"] == "生活"


# ── 區塊子頁 ──────────────────────────────────────────────

def test_finance_dbs_live_under_finance_section(notion):
    """財務類 DB 要放進「財務中心」子頁，不能跟核心 DB 混在根頁。"""
    db_id = notion_db.get_or_create_db("交易明細")

    assert notion.db_parent_of(db_id) == "page_財務中心"


def test_kitchen_dbs_live_under_kitchen_section(notion):
    db_id = notion_db.get_or_create_db("食材庫存")

    assert notion.db_parent_of(db_id) == "page_煮飯模板"


def test_core_dbs_stay_on_root_page(notion):
    """Todos / Reminders / LineQuota 已經在線上跑，不能被搬家。"""
    db_id = notion_db.get_or_create_db("Todos")

    assert notion.db_parent_of(db_id) == notion._parent_page


def test_section_page_created_once_for_sibling_dbs(notion):
    notion_db.get_or_create_db("交易明細")
    notion_db.get_or_create_db("持倉")

    made = [p for p in notion.pages.created
            if "財務中心" in str(p.get("properties", {}))]
    assert len(made) == 1, "同一區塊的多個 DB 只該建立一次子頁"


def test_existing_section_page_is_reused(monkeypatch):
    parent = "parentpage32charhexxxxxxxxxxxxxx"
    pages = {
        "page_existing_fin": {
            "id": "page_existing_fin",
            "object": "page",
            "parent": {"type": "page_id", "page_id": parent},
            "properties": {
                "title": {"title": [{"plain_text": "財務中心", "text": {"content": "財務中心"}}]}
            },
        }
    }
    client = FakeClient(parent, preexisting_pages=pages)
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", parent)
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_client", client)
    monkeypatch.setattr(notion_db, "_db_id_cache", {})
    monkeypatch.setattr(notion_db, "_section_page_cache", {})

    db_id = notion_db.get_or_create_db("帳戶")

    assert client.db_parent_of(db_id) == "page_existing_fin"
    assert client.pages.created == [], "已存在的區塊頁不該重建"


def test_section_page_failure_falls_back_to_root(notion):
    """建子頁失敗時，DB 還是要能建出來，只是退回根頁。"""
    notion.pages.fail_create_for.add("財務中心")

    db_id = notion_db.get_or_create_db("帳戶")

    assert db_id is not None
    assert notion.db_parent_of(db_id) == notion._parent_page


def test_read_select_tolerates_missing_property():
    """遷移前建立的待辦沒有『分類』欄位，讀取不能炸。"""
    assert notion_db._read_select({}, "分類") == ""
    assert notion_db._read_select({"分類": {"select": None}}, "分類") == ""
    assert notion_db._read_select({"分類": {"select": {"name": "工作"}}}, "分類") == "工作"


# ── 消費類別白名單 ────────────────────────────────────────
#
# 背景：Notion 對未定義的 select 值不會報錯，而是自動新增選項。國泰因此
# 在 2026-08 悄悄把線上 schema 從 10 個類別撐到 14 個，程式碼卻毫不知情，
# 任何按類別分組的報表都在漏桶。這組測試守住兩件事：白名單與線上一致、
# 未知值收斂成「其他」而不是長出新選項。

def test_spend_categories_cover_what_notion_actually_has():
    """線上實測（2026-08-25）存在的 14 個類別都要在白名單裡。"""
    expected = {
        "餐飲", "超市∕量販", "百貨公司", "服飾∕鞋∕精品", "家電∕３Ｃ通訊",
        "旅遊", "電信服務", "醫療", "訂閱服務", "其他",
        "線上付款", "教育∕學費", "一般購物", "家具家飾裝潢",
    }
    assert set(notion_db.SPEND_CATEGORIES) == expected


def test_transaction_category_options_match_whitelist():
    """schema 的 select 選項要跟白名單同源，不能各寫各的。"""
    options = notion_db._SCHEMAS["交易明細"]["類別"]["select"]["options"]
    assert [o["name"] for o in options] == list(notion_db.SPEND_CATEGORIES)


@pytest.mark.parametrize("raw", ["餐飲", "  餐飲  ", "線上付款", "家具家飾裝潢"])
def test_normalize_spend_category_keeps_known(raw):
    assert notion_db.normalize_spend_category(raw) == raw.strip()


def test_normalize_spend_category_tolerates_ascii_slash():
    """國泰信件的斜線全形半形混用，不該因此判成未知類別。"""
    assert notion_db.normalize_spend_category("超市/量販") == "超市∕量販"


@pytest.mark.parametrize("raw", ["寵物用品", "", None, "   "])
def test_normalize_spend_category_falls_back(raw):
    assert notion_db.normalize_spend_category(raw) == "其他"


def test_transaction_add_normalizes_unknown_category(notion):
    """未知類別必須在寫入前收斂，否則 Notion 會直接長出新選項。"""
    notion_db.transaction_add({
        "date": "2026-08-25", "amount": 100, "category": "寵物用品",
        "shop": "某店", "fingerprint": "fp1",
    })

    props = notion.pages.created[-1]["properties"]
    assert props["類別"]["select"]["name"] == "其他"


def test_transaction_add_omits_category_when_absent(notion):
    """沒帶類別就不要寫這欄 —— 硬填「其他」會把「不知道」偽裝成「已分類」。"""
    notion_db.transaction_add({
        "date": "2026-08-25", "amount": 100, "shop": "某店", "fingerprint": "fp2",
    })

    assert "類別" not in notion.pages.created[-1]["properties"]


# ── ensure_all_dbs ────────────────────────────────────────

def test_ensure_all_dbs_creates_every_schema(notion):
    """不能再靠 lazy create：上游一個提早 return 就會讓整個 DB 永遠不存在。

    2026-08-25 健檢實證：食材庫存為空 → daily_report 的 expiring_soon()
    回空即 return → 走不到 recipes_load() → 食譜 / 本週菜單 / 採購清單
    從上線起就不存在，而且 log 全綠。
    """
    ok, total = notion_db.ensure_all_dbs()

    assert total == len(notion_db._SCHEMAS)
    assert ok == total

    created = {t for t, _ in notion.databases.create_calls}
    for name in ("食譜", "本週菜單", "採購清單"):
        assert name in created, f"{name} 應該被建出來"


def test_ensure_all_dbs_survives_one_failure(notion):
    """單一 DB 失敗不該讓其餘的都不建。"""
    notion.databases.fail_create_for.add("食譜")

    ok, total = notion_db.ensure_all_dbs()

    assert ok == total - 1
    created = {t for t, _ in notion.databases.create_calls}
    assert "採購清單" in created


def test_ensure_all_dbs_noop_without_config(monkeypatch):
    monkeypatch.setattr(notion_db, "_TOKEN", "")
    assert notion_db.ensure_all_dbs() == (0, 0)


def test_transaction_schema_has_split_columns():
    """共同消費要存兩件事：分的是哪一種、整桌多少錢。

    _ensure_properties 只補不刪，線上既有 DB 會自動長出這兩欄，
    既有資料列不動。
    """
    schema = notion_db._SCHEMAS["交易明細"]

    assert "分攤類型" in schema
    assert "原始總額" in schema

    names = [o["name"] for o in schema["分攤類型"]["select"]["options"]]
    assert names == ["個人", "共同"]
