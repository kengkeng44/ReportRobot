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
    notion_db.get_or_create_db("交易明細")

    created = dict(notion.databases.create_calls)
    props = created["交易明細"]
    assert "帳戶" not in props, "relation 欄位不該出現在第一階段 create"
    assert "金額" in props


def test_relation_added_in_second_pass_with_real_db_id(notion):
    """第二階段用 databases.update 補 relation，且 @帳戶 要被解析成真實 db_id。"""
    notion_db.get_or_create_db("交易明細")

    updates = [u for u in notion.databases.update_calls if "帳戶" in u[1]]
    assert updates, "應該有一次 update 補上『帳戶』relation"

    _, props = updates[0]
    assert props["帳戶"]["relation"]["database_id"] == "db_帳戶"


def test_relation_target_db_gets_created(notion):
    """relation 指向的 DB 若不存在，要一併建出來。"""
    notion_db.get_or_create_db("交易明細")

    created_titles = [t for t, _ in notion.databases.create_calls]
    assert "帳戶" in created_titles


def test_relation_target_failure_does_not_raise(notion):
    """目標 DB 建立失敗時，主 DB 仍要能用，只是少了 relation。"""
    notion.databases.fail_create_for.add("帳戶")

    db_id = notion_db.get_or_create_db("交易明細")

    assert db_id == "db_交易明細"
    assert not [u for u in notion.databases.update_calls if "帳戶" in u[1]]


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
