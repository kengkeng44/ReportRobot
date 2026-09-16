from datetime import date, datetime

import pytest

import weather


LOCS = ["淡水區", "金山區"]
SAMPLE = (
    "• 淡水藝術節｜2026-09-20｜淡水老街｜https://example.com/a\n"
    "• 金山市集｜09/16-09/18｜金山老街\n"
    "• 河岸音樂會｜2026-09-25~2026-09-27｜淡水河岸"
)


@pytest.fixture(autouse=True)
def _clean_cache():
    weather._EVENTS_CACHE.clear()
    yield
    weather._EVENTS_CACHE.clear()


def _set_today(monkeypatch, d):
    monkeypatch.setattr(weather, "now_tpe", lambda: datetime(d.year, d.month, d.day, 6, 0))


def _counting_fetch(monkeypatch, result=SAMPLE):
    calls = []

    def fake(locations, today):
        calls.append(today)
        return result

    monkeypatch.setattr(weather, "_fetch_local_events", fake)
    return calls


def test_second_call_same_week_uses_cache(monkeypatch):
    calls = _counting_fetch(monkeypatch)
    _set_today(monkeypatch, date(2026, 9, 16))
    weather.get_local_events(LOCS)
    _set_today(monkeypatch, date(2026, 9, 19))
    weather.get_local_events(LOCS)
    assert len(calls) == 1


def test_refetches_after_seven_days(monkeypatch):
    calls = _counting_fetch(monkeypatch)
    _set_today(monkeypatch, date(2026, 9, 16))
    weather.get_local_events(LOCS)
    _set_today(monkeypatch, date(2026, 9, 23))
    weather.get_local_events(LOCS)
    assert len(calls) == 2


def test_different_locations_cached_separately(monkeypatch):
    calls = _counting_fetch(monkeypatch)
    _set_today(monkeypatch, date(2026, 9, 16))
    weather.get_local_events(LOCS)
    weather.get_local_events(["板橋區"])
    assert len(calls) == 2


def test_cached_events_drop_finished_ones(monkeypatch):
    _counting_fetch(monkeypatch)
    _set_today(monkeypatch, date(2026, 9, 16))
    first = weather.get_local_events(LOCS)
    assert "金山市集" in first
    _set_today(monkeypatch, date(2026, 9, 21))
    later = weather.get_local_events(LOCS)
    assert "金山市集" not in later      # 09/18 結束
    assert "淡水藝術節" not in later    # 09/20 結束
    assert "河岸音樂會" in later        # 結束日取區間最後一天 09/27


def test_failure_is_not_cached(monkeypatch):
    calls = _counting_fetch(monkeypatch, result=None)
    _set_today(monkeypatch, date(2026, 9, 16))
    assert weather.get_local_events(LOCS) == ""
    weather.get_local_events(LOCS)
    assert len(calls) == 2


def test_no_events_result_is_cached(monkeypatch):
    calls = _counting_fetch(monkeypatch, result="")
    _set_today(monkeypatch, date(2026, 9, 16))
    assert weather.get_local_events(LOCS) == ""
    weather.get_local_events(LOCS)
    assert len(calls) == 1


@pytest.mark.parametrize("line, expected", [
    ("• A｜2026-09-20｜地點", date(2026, 9, 20)),
    ("• A｜09/16-09/18｜地點", date(2026, 9, 18)),
    ("• A｜2026-09-25~2026-09-27｜地點", date(2026, 9, 27)),
    ("• A｜01/05｜地點", date(2027, 1, 5)),   # 沒年份且落在半年前 → 明年
    ("• A｜近期｜地點", None),
    ("• 沒有分隔符的一行", None),
    ("• A｜02/30｜地點", None),               # 不存在的日期
])
def test_event_end_date(line, expected):
    assert weather._event_end_date(line, date(2026, 9, 16)) == expected


def test_unparseable_lines_are_kept():
    text = "• A｜近期｜地點\n• B｜2026-09-01｜地點"
    assert weather._drop_past_events(text, date(2026, 9, 16)) == "• A｜近期｜地點"
