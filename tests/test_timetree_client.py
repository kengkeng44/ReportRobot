"""TimeTree 只讀：事件落在哪天、重複規則展開、轉成 Google 形狀、出錯不拖垮。"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import couple_calendar
import timetree_client

TPE = ZoneInfo("Asia/Taipei")
DAY = date(2026, 11, 8)


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _timed(start_tpe, hours=1, **kw):
    end = start_tpe.replace(hour=start_tpe.hour + hours)
    return {"title": "吃飯", "all_day": False, "start_at": _ms(start_tpe), "end_at": _ms(end),
            "start_timezone": "Asia/Taipei", "end_timezone": "Asia/Taipei", **kw}


def _all_day(d, end=None, **kw):
    utc = lambda x: datetime(x.year, x.month, x.day, tzinfo=timezone.utc)
    return {"title": "露營", "all_day": True, "start_at": _ms(utc(d)), "end_at": _ms(utc(end or d)),
            "start_timezone": "UTC", "end_timezone": "UTC", **kw}


def test_timed_event_uses_taipei_date():
    # 台北 11/8 07:00 = UTC 11/7 23:00，要算 11/8
    ev = _timed(datetime(2026, 11, 8, 7, tzinfo=TPE))
    assert timetree_client._occurs_on(ev, DAY)
    assert not timetree_client._occurs_on(ev, date(2026, 11, 7))
    g = timetree_client._to_google_shape(ev, DAY)
    assert g["start"]["dateTime"].startswith("2026-11-08T07:00")
    assert g["source"] == "timetree"


def test_multi_day_all_day_is_inclusive():
    ev = _all_day(date(2026, 11, 7), end=date(2026, 11, 8))
    assert timetree_client._occurs_on(ev, DAY)
    assert not timetree_client._occurs_on(ev, date(2026, 11, 9))
    assert timetree_client._to_google_shape(ev, DAY)["start"] == {"date": "2026-11-08"}


def test_weekly_recurrence():
    ev = _timed(datetime(2026, 10, 18, 19, tzinfo=TPE), recurrences=["RRULE:FREQ=WEEKLY"])  # 週日
    assert timetree_client._occurs_on(ev, DAY)  # 11/8 也是週日
    assert not timetree_client._occurs_on(ev, date(2026, 11, 9))
    assert timetree_client._to_google_shape(ev, DAY)["start"]["dateTime"].startswith("2026-11-08T19:00")


def test_yearly_all_day_recurrence():
    ev = _all_day(date(2020, 11, 8), recurrences=["RRULE:FREQ=YEARLY"])
    assert timetree_client._occurs_on(ev, DAY)
    assert not timetree_client._occurs_on(ev, date(2026, 11, 7))


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("TIMETREE_EMAIL", raising=False)
    monkeypatch.setenv("TIMETREE_CALENDAR", "我們")
    assert timetree_client.events_on(DAY) == []


def test_errors_are_swallowed(monkeypatch):
    monkeypatch.setenv("TIMETREE_EMAIL", "a@b.c")
    monkeypatch.setenv("TIMETREE_PASSWORD", "x")
    monkeypatch.setenv("TIMETREE_CALENDAR", "我們")

    def boom(name):
        raise RuntimeError("blocked")
    monkeypatch.setattr(timetree_client, "_find_calendar", boom)
    assert timetree_client.events_on(DAY) == []


def test_events_on_filters_by_day(monkeypatch):
    monkeypatch.setenv("TIMETREE_EMAIL", "a@b.c")
    monkeypatch.setenv("TIMETREE_PASSWORD", "x")
    monkeypatch.setenv("TIMETREE_CALENDAR", "我們")
    monkeypatch.setattr(timetree_client, "_find_calendar", lambda n: {"id": "1", "name": n})
    monkeypatch.setattr(timetree_client, "fetch_raw_events", lambda cid: [
        _timed(datetime(2026, 11, 8, 12, tzinfo=TPE)),
        _timed(datetime(2026, 11, 9, 12, tzinfo=TPE)),
    ])
    assert len(timetree_client.events_on(DAY)) == 1


def test_cookie_mode_skips_password_login(monkeypatch):
    monkeypatch.delenv("TIMETREE_EMAIL", raising=False)
    monkeypatch.setenv("TIMETREE_SESSION_COOKIE", "abc")
    assert timetree_client.enabled()
    s = timetree_client.login()
    assert s.cookies.get("_session_id") == "abc"


def test_cookie_expired_notifies_admin(monkeypatch):
    import admin_notify
    monkeypatch.setenv("TIMETREE_SESSION_COOKIE", "abc")
    monkeypatch.setenv("TIMETREE_CALENDAR", "我們")
    sent = []
    monkeypatch.setattr(admin_notify, "notify_admin", lambda err, context=None: sent.append(err))

    def expired(name):
        raise timetree_client.CookieExpired("x")
    monkeypatch.setattr(timetree_client, "_find_calendar", expired)
    assert timetree_client.events_on(DAY) == []
    assert sent and "TIMETREE_SESSION_COOKIE" in sent[0]


def test_merged_into_preview(monkeypatch):
    monkeypatch.setattr(couple_calendar, "_fetch_google", lambda *a: [
        {"summary": "看展", "start": {"dateTime": "2026-11-08T14:00:00+08:00"}}])
    monkeypatch.setattr(timetree_client, "events_on", lambda d: [
        {"summary": "吃早餐", "start": {"dateTime": "2026-11-08T09:00:00+08:00"}, "source": "timetree"}])
    events = couple_calendar.fetch_events(DAY)
    assert [e["summary"] for e in events] == ["吃早餐", "看展"]
    text = couple_calendar.build_preview(DAY, events, [])
    assert "🌳 09:00　吃早餐" in text and "⭐ 14:00　看展" in text


def test_google_failure_keeps_timetree(monkeypatch):
    def boom(*a):
        raise RuntimeError("no token")
    monkeypatch.setattr(couple_calendar, "_fetch_google", boom)
    monkeypatch.setattr(timetree_client, "events_on", lambda d: [
        {"summary": "吃早餐", "start": {"date": "2026-11-08"}, "source": "timetree"}])
    assert len(couple_calendar.fetch_events(DAY)) == 1
