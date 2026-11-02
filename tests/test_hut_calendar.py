from datetime import date

import hut_calendar as hc

TODAY = date(2026, 11, 2)  # 週一


def test_good_night_is_night_before_day_off():
    assert hc.is_good_night(date(2026, 11, 6))       # 週五晚 → 隔天週六
    assert hc.is_good_night(date(2026, 11, 7))       # 週六晚 → 隔天週日
    assert not hc.is_good_night(date(2026, 11, 8))   # 週日晚 → 隔天上班
    assert not hc.is_good_night(date(2026, 11, 4))
    assert hc.is_good_night(date(2026, 12, 31))      # 隔天元旦


def test_minus_months():
    assert hc.minus_months(date(2026, 11, 1), 2) == date(2026, 9, 1)
    assert hc.minus_months(date(2026, 1, 15), 2) == date(2025, 11, 15)
    assert hc.minus_months(date(2026, 4, 29), 2) == date(2026, 3, 1)  # 2/29 不存在 → 3/1
    assert hc.minus_months(date(2026, 12, 31), 1) == date(2026, 12, 1)  # 11/31 → 12/1


def test_fmt_day():
    assert hc.fmt_day(date(2026, 12, 5)) == "12/5(六)"


def test_upcoming_events():
    events = hc.upcoming_events(TODAY)
    # 嘉明湖住宿前 30 天抽：12/5(六) 那晚 → 11/5 抽
    assert (date(2026, 11, 5), "嘉明湖／向陽", date(2026, 12, 5), "15:00 抽籤（16:00 公布）") in events
    # 九九／雪霸／太魯閣前 2 個月 07:00 開放：2027/1/2(六) → 11/2
    assert any(e[:3] == (date(2026, 11, 2), "九九／雪霸／太魯閣", date(2027, 1, 2)) for e in events)
    assert all(TODAY <= e[0] < date(2026, 11, 9) for e in events)
    assert all(hc.is_good_night(e[2]) for e in events)
    assert events == sorted(events)
