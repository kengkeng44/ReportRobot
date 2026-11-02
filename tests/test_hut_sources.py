"""林業署三站月曆解析。樣本是 2026-11-02 實抓的頁面。"""

from datetime import date
from pathlib import Path

import hut_sources as hs

FIX = Path(__file__).parent / "fixtures" / "hut"


def _html(name):
    return (FIX / name).read_text(encoding="utf-8")


def _find(slots, unit, day):
    return next(s for s in slots if s.unit == unit and s.day == day)


def test_jmlnt_beds_and_campsite():
    slots = hs.parse_calendar(_html("jmlnt.html"), "jmlnt", 2026, 11)
    s = _find(slots, "嘉明湖山屋", date(2026, 11, 7))
    assert (s.site, s.kind, s.remaining, s.applicants) == ("jmlnt", "bed", 10, 202)
    camp = _find(slots, "嘉明湖營地", date(2026, 11, 7))
    assert (camp.kind, camp.remaining, camp.applicants) == ("tent", 0, 8)
    xy = _find(slots, "向陽山屋", date(2026, 11, 8))
    assert (xy.remaining, xy.applicants) == (22, 55)


def test_tconline_day_without_apply_button_still_parsed():
    """天池 11/6 已過申請期，沒有「線上申請」按鈕，日期要從格子裡別處拿。"""
    slots = hs.parse_calendar(_html("tconline.html"), "tconline", 2026, 11)
    s = _find(slots, "天池山莊", date(2026, 11, 6))
    assert (s.kind, s.remaining, s.applicants) == ("bed", 17, 12)
    assert _find(slots, "天池山莊", date(2026, 11, 7)).applicants == 301
    big = _find(slots, "周圍營地大營地4*4", date(2026, 11, 8))
    assert (big.kind, big.remaining) == ("tent", 3)
    assert _find(slots, "周圍營地小營地3*3", date(2026, 11, 6)).remaining == 18


def test_kgonline_ajax_calendar():
    slots = hs.parse_calendar(_html("kgonline_cal.html"), "kgonline", 2026, 12)
    s = _find(slots, "檜谷山莊", date(2026, 12, 3))
    assert (s.kind, s.remaining, s.applicants) == ("bed", 42, 52)
    tent = _find(slots, "周圍營地四人帳篷", date(2026, 12, 3))
    assert (tent.kind, tent.remaining, tent.applicants) == ("tent", 7, 3)


def test_all_days_in_requested_month():
    for name, site, month in (("jmlnt.html", "jmlnt", 11), ("tconline.html", "tconline", 11),
                              ("kgonline_cal.html", "kgonline", 12)):
        slots = hs.parse_calendar(_html(name), site, 2026, month)
        assert len(slots) > 20
        assert {s.day.month for s in slots} == {month}


def test_no_calendar_gives_no_slots():
    assert hs.parse_calendar(_html("kgonline.html"), "kgonline", 2026, 11) == []


def test_slot_dict_round_trip():
    s = hs.Slot("jmlnt", "嘉明湖山屋", "bed", date(2026, 12, 5), 3, None)
    assert hs.Slot.from_dict(s.to_dict()) == s
    assert s.key() == ("jmlnt", "嘉明湖山屋", "2026-12-05")
