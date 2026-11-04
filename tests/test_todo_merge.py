"""三份待辦合成一份：開機載入共同待辦時，把你和小螢舊表裡的未完成待辦搬進來。"""

import pytest

import notion_db
import personal

GROUP = "Cgroup"


def test_loading_shared_list_never_touches_old_tables(monkeypatch):
    """舊表已刪：載入共同待辦只碰「共同待辦」，不會去找（或重建）Todos、小螢待辦。"""
    monkeypatch.setenv("COUPLE_GROUP_ID", GROUP)
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")
    asked = []
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: asked.append(name) or "db")

    class C:
        databases = None
    c = C()
    c.databases = type("D", (), {"query": staticmethod(lambda **kw: {"results": []})})
    monkeypatch.setattr(notion_db, "_get_client", lambda: c)
    notion_db.todos_load_for_user("Uadmin")
    notion_db.todos_load_for_user(GROUP)
    assert set(asked) == {"共同待辦"}


def test_no_group_means_no_merge(monkeypatch):
    monkeypatch.delenv("COUPLE_GROUP_ID", raising=False)
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")
    assert personal.canonical_owner("Uadmin") == "Uadmin"


def test_list_rereads_notion_so_web_added_todos_show_up(monkeypatch):
    """網頁儀表板直接寫 Notion 的待辦，過了重讀時間 LINE 也看得到，編號接著往下。"""
    import time
    monkeypatch.delenv("COUPLE_GROUP_ID", raising=False)
    monkeypatch.setattr(personal, "_notion_enabled", lambda: True)
    rows = [{"page_id": "a", "local_id": 1, "text": "舊的"}]
    monkeypatch.setattr(notion_db, "todos_load_for_user", lambda uid: list(rows))
    monkeypatch.setattr(notion_db, "todos_create", lambda *a, **kw: "new")
    for d in (personal._TODOS, personal._TODO_NEXT_ID, personal._TODOS_LOADED_AT):
        d.clear()
    personal._TODOS_LOADED_USERS.clear()
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    assert [t["text"] for t in personal.list_todos("U1")] == ["舊的"]
    rows.append({"page_id": "b", "local_id": 2, "text": "網頁加的"})
    assert len(personal.list_todos("U1")) == 1          # 還沒到重讀時間
    clock[0] += personal.TODOS_REFRESH_SECONDS + 1
    assert [t["text"] for t in personal.list_todos("U1")] == ["舊的", "網頁加的"]
    assert personal.add_todo("U1", "LINE 加的") == 3


@pytest.mark.parametrize("text,kind", [
    ("Railway 資安事件：換掉機器人的密鑰", "💻 電腦設定"),
    ("修 Wi-Fi 斷線：關網卡省電", "💻 電腦設定"),
    ("山屋自動申請：回家用電腦跑 recon", "🤖 自動化"),
    ("每天跑cron要更新我的股票淨值", "🤖 自動化"),
    ("信用卡歸戶", "💰 理財"),
    ("晚上要預約各個銀行辦信用卡跟調整額度", "💰 理財"),
    ("有空去找虹螢國中老師", "👥 找人"),
    ("全聯載具歸戶", "📋 手續"),
    ("買蒜頭冰磚", "🛒 採購"),
    ("帶胃藥回家", "🏡 回家"),
])
def test_personal_types(text, kind):
    import todo_parse
    assert todo_parse.guess_type(text) == kind
