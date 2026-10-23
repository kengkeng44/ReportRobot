"""情侶群組明天預告：明天有 ⭐ 特別活動才推，到期待辦順便帶；只有待辦不推。"""

from datetime import date

import pytest

import couple_calendar
import personal

GROUP = "Cgroup"
TODAY = date(2026, 10, 24)      # 週六晚上推
TOMORROW = date(2026, 10, 25)   # 週日


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("COUPLE_GROUP_ID", GROUP)
    monkeypatch.setattr(couple_calendar.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(personal, "_notion_enabled", lambda: False)
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()
    yield
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()


def _events(monkeypatch, items):
    monkeypatch.setattr(couple_calendar, "fetch_events", lambda day: items)


def _run():
    sent = []
    text = couple_calendar.run_evening_preview(
        push_func=lambda to, msg: sent.append((to, msg)), today=TODAY)
    return text, sent


def test_nothing_means_no_push(monkeypatch):
    _events(monkeypatch, [])
    text, sent = _run()
    assert text is None and sent == []


def test_undated_p0_alone_does_not_push(monkeypatch):
    _events(monkeypatch, [])
    personal.add_todo(GROUP, "很重要但沒日期", priority="P0")
    assert _run() == (None, [])


def test_special_event_pushes_to_couple_group(monkeypatch):
    _events(monkeypatch, [
        {"summary": "故宮之夜", "start": {"dateTime": "2026-10-25T19:00:00+08:00"},
         "location": "國立故宮博物院, 台北市"},
        {"summary": "合歡山", "start": {"date": "2026-10-25"}},
    ])
    text, sent = _run()
    assert sent == [(GROUP, text)]
    assert "10/25（日）" in text
    assert "⭐ 19:00　故宮之夜（國立故宮博物院）" in text
    assert "⭐ 整天　合歡山" in text
    assert "待辦" not in text


def test_todos_alone_do_not_push(monkeypatch):
    _events(monkeypatch, [])
    personal.add_todo(GROUP, "繳電費", start=TOMORROW)
    personal.add_todo(GROUP, "退貨", start=date(2026, 10, 21))
    assert _run() == (None, [])


def test_due_todos_ride_along_with_event(monkeypatch):
    _events(monkeypatch, [{"summary": "生日", "start": {"date": "2026-10-25"}}])
    personal.add_todo(GROUP, "繳電費", start=TOMORROW)
    personal.add_todo(GROUP, "買禮物", start=TODAY)
    personal.add_todo(GROUP, "退貨", start=date(2026, 10, 21))
    personal.add_todo(GROUP, "下週的事", start=date(2026, 10, 30))
    text, sent = _run()
    assert len(sent) == 1
    lines = text.splitlines()
    assert "⭐ 整天　生日" in lines
    assert "⬜ [3] 退貨（⚠️ 逾期 3 天）" in lines
    assert "⬜ [2] 買禮物（今天）" in lines
    assert "⬜ [1] 繳電費（明天）" in lines
    assert "下週的事" not in text
    # 逾期最久的排最前面
    assert lines.index("⬜ [3] 退貨（⚠️ 逾期 3 天）") < lines.index("⬜ [1] 繳電費（明天）")


def test_only_group_todos_not_personal(monkeypatch):
    _events(monkeypatch, [{"summary": "生日", "start": {"date": "2026-10-25"}}])
    personal.add_todo("Uself", "我自己的", start=TOMORROW)
    text, _ = _run()
    assert "我自己的" not in text and "待辦" not in text


def test_same_night_pushes_only_once(monkeypatch):
    _events(monkeypatch, [{"summary": "生日", "start": {"date": "2026-10-25"}}])
    first, sent1 = _run()
    second, sent2 = _run()
    assert first and len(sent1) == 1
    assert second is None and sent2 == []


def test_calendar_failure_means_no_push(monkeypatch):
    def boom(day):
        raise RuntimeError("token 過期")
    monkeypatch.setattr(couple_calendar, "fetch_events", boom)
    personal.add_todo(GROUP, "繳電費", start=TOMORROW)
    assert _run() == (None, [])


def test_no_group_configured_skips(monkeypatch):
    monkeypatch.delenv("COUPLE_GROUP_ID")
    _events(monkeypatch, [{"summary": "生日", "start": {"date": "2026-10-25"}}])
    assert _run() == (None, [])


def test_fetch_events_without_calendar_id_returns_empty(monkeypatch):
    monkeypatch.delenv("SPECIAL_CALENDAR_ID", raising=False)
    assert couple_calendar.fetch_events(TOMORROW, service=object()) == []
