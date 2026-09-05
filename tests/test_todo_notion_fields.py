"""Todos 的「期間」與「優先度」兩個新欄位。

不打網路：用一個假的 client 攔下 pages.create / pages.update
的 properties，直接斷言送出去的 payload。
"""

from datetime import date

import notion_db


# ── schema ────────────────────────────────────────────────

def test_todos_schema_has_the_two_new_fields():
    """既有 DB 由 _ensure_properties 自動補上，使用者不用手動建欄位。"""
    todos = notion_db._SCHEMAS["Todos"]

    assert "期間" in todos
    assert "優先度" in todos


def test_period_is_a_native_date_range():
    """用 Notion 原生的 date property（它本來就支援 start+end）。
    拆成「開始日」「結束日」兩欄要自己維護「結束不能早於開始」。"""
    assert notion_db._SCHEMAS["Todos"]["期間"] == {"date": {}}


def test_priority_options_are_p0_to_p3():
    options = notion_db._SCHEMAS["Todos"]["優先度"]["select"]["options"]

    assert [o["name"] for o in options] == ["P0", "P1", "P2", "P3"]


# ── todos_create ──────────────────────────────────────────

class FakePages:
    def __init__(self):
        self.created = []
        self.updated = []

    def create(self, parent, properties):
        self.created.append(properties)
        return {"id": "page-1"}

    def update(self, page_id, **kwargs):
        self.updated.append((page_id, kwargs))
        return {"id": page_id}


class FakeClient:
    def __init__(self):
        self.pages = FakePages()


def _install(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(notion_db, "_get_client", lambda: client)
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db-1")
    return client


def test_create_sends_a_date_range(monkeypatch):
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "出差", 1,
                           start=date(2026, 9, 1), end=date(2026, 9, 10))

    assert client.pages.created[0]["期間"] == {
        "date": {"start": "2026-09-01", "end": "2026-09-10"}
    }


def test_single_day_leaves_end_empty(monkeypatch):
    """只講一天時 end 留空 —— 兩邊填一樣的日期等於逼讀取端
    處理兩種等價表示法。"""
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 1, start=date(2026, 9, 8))

    assert client.pages.created[0]["期間"] == {
        "date": {"start": "2026-09-08", "end": None}
    }


def test_no_date_omits_the_field_entirely(monkeypatch):
    """Notion 的 date property 不接受 start=None，硬送整筆會失敗。"""
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 1)

    assert "期間" not in client.pages.created[0]


def test_priority_is_sent_as_select(monkeypatch):
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 1, priority="P0")

    assert client.pages.created[0]["優先度"] == {"select": {"name": "P0"}}


def test_no_priority_omits_the_field(monkeypatch):
    """select 不接受 name=None。而且「沒設優先度」跟「設成 P2」
    是兩件不同的事，不要偷偷補預設值。"""
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 1)

    assert "優先度" not in client.pages.created[0]


def test_unknown_priority_is_dropped(monkeypatch):
    """P9 不在選項裡。Notion 遇到未定義的 select 值會**擴充 schema**
    而不是報錯 —— 那種偏移完全沒有訊號（見 _SPEND_CATEGORIES 的註解）。"""
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 1, priority="P9")

    assert "優先度" not in client.pages.created[0]


def test_existing_fields_still_sent(monkeypatch):
    """加新欄位不能弄丟舊的。"""
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 7)
    props = client.pages.created[0]

    assert props["LocalId"] == {"number": 7}
    assert props["Done"] == {"checkbox": False}
    assert "分類" in props
