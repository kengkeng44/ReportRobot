"""手機桌面小工具：緊急的事、專案進度，以及 /widget/todos 的金鑰保護。"""

from datetime import date

import pytest

import todo_widget

TODAY = date(2026, 11, 4)


def _t(i, text, owner="仁和", project="", priority=None, start=None):
    return {"id": i, "text": text, "owner": owner, "project": project,
            "priority": priority, "start": start, "end": None}


OPEN = [
    _t(1, "買衛生紙", owner="共同", project="🏠 新家佈置"),
    _t(13, "預約銀行", project="💳 辦卡與歸戶", start=TODAY),
    _t(14, "修 Wi-Fi", project="💻 電腦環境", priority="P1"),
    _t(15, "Remote Control", project="💻 電腦環境", priority="P1"),
    _t(20, "跟爸拿水藥", project="🏡 回淡水", start=date(2026, 12, 1)),
    _t(30, "逾期的事", start=date(2026, 11, 1)),
    _t(41, "還書", owner="小螢", priority="P0"),
]
DONE = [("🏠 新家佈置", "共同"), ("💻 電腦環境", "仁和"), ("💻 電腦環境", "仁和")]


def test_urgent_is_overdue_then_today_then_priority():
    out = todo_widget.build(OPEN, DONE, TODAY)
    assert [(u["text"], u["why"]) for u in out["urgent"]] == [
        ("逾期的事", "逾期 3 天"),
        ("預約銀行", "今天"),
        ("還書", "P0"),
    ]
    assert out["urgent_count"] == 3 and out["open"] == 7


def test_p1_is_not_urgent():
    out = todo_widget.build([_t(14, "修 Wi-Fi", priority="P1")], [], TODAY)
    assert out["urgent"] == [] and out["urgent_count"] == 0


def test_projects_progress_and_urgent_first():
    out = todo_widget.build(OPEN, DONE, TODAY)
    first = out["projects"][0]
    assert first["name"] == "💳 辦卡與歸戶"       # 今天到期那件讓它排第一
    assert first["urgent"] == 1
    pc = next(p for p in out["projects"] if p["name"] == "💻 電腦環境")
    assert (pc["open"], pc["done"], pc["urgent"], pc["progress"]) == (2, 2, 0, 0.5)
    names = [p["name"] for p in out["projects"]]
    assert "🏡 回淡水" in names and "🏠 新家佈置" in names


def test_who_sees_own_and_shared_only():
    out = todo_widget.build(OPEN, DONE, TODAY, who="小螢")
    assert [u["text"] for u in out["urgent"]] == ["還書"]
    assert [p["name"] for p in out["projects"]] == ["🏠 新家佈置"]
    assert out["projects"][0]["done"] == 1


def test_finished_projects_drop_off():
    out = todo_widget.build([], DONE, TODAY)
    assert out["projects"] == [] and out["urgent"] == []


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setattr(todo_widget, "payload", lambda who=None: {"who": who or "全部"})
    return TestClient(server.app)


def test_endpoint_needs_its_own_token(client, monkeypatch):
    monkeypatch.delenv("WIDGET_TOKEN", raising=False)
    assert client.get("/widget/todos").status_code == 503
    monkeypatch.setenv("WIDGET_TOKEN", "w1")
    monkeypatch.setenv("ADMIN_TOKEN", "a1")
    assert client.get("/widget/todos").status_code == 403
    assert client.get("/widget/todos", headers={"X-Widget-Token": "a1"}).status_code == 403
    r = client.get("/widget/todos?who=仁和", headers={"X-Widget-Token": "w1"})
    assert r.status_code == 200 and r.json() == {"who": "仁和"}
