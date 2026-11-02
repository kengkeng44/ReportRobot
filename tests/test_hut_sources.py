"""林業署三站月曆解析。樣本是 2026-11-02 實抓的頁面。"""

from datetime import date
from pathlib import Path

import requests

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


def test_find_csrf_present_and_absent():
    assert hs.find_csrf(_html("jmlnt.html")) == "YXdkMjI2MDIwMjYtMTEtMDI="
    assert hs.find_csrf(_html("kgonline_cal.html")) == ""
    assert hs.find_csrf(None) == ""


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code in (403, 429):
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    """index 頁給 csrf，月曆請求一律回同一份月曆樣本。"""

    def __init__(self, index, month, status=200):
        self.index, self.month, self.status = index, month, status
        self.headers, self.calls = {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(("get", params))
        return _Resp(self.month if params else self.index, self.status)

    def post(self, url, data=None, timeout=None):
        self.calls.append(("post", data))
        return _Resp(self.month, self.status)


def test_fetch_site_get_navigation_and_csrf():
    jml = _html("jmlnt.html")
    sess = _Session(jml, jml)
    slots = hs.fetch_site("jmlnt", date(2026, 11, 2), session=sess, sleep=lambda s: None)
    months = [(p["date_set[year]"], p["date_set[month]"]) for kind, p in sess.calls if p]
    assert months == [(2026, 11), (2026, 12), (2027, 1)]
    assert all(p["csrf"] == "YXdkMjI2MDIwMjYtMTEtMDI=" for kind, p in sess.calls if p)
    assert sess.headers["User-Agent"] == hs.USER_AGENT
    assert len(slots) > 60


def test_fetch_site_kgonline_posts_ajax():
    sess = _Session(_html("kgonline.html"), _html("kgonline_cal.html"))
    hs.fetch_site("kgonline", date(2026, 12, 1), months=1, session=sess, sleep=lambda s: None)
    kind, data = sess.calls[-1]
    assert kind == "post"
    assert data["mode"] == "show_calendar" and (data["year"], data["month"]) == (2026, 12)


def test_fetch_all_zero_rows_and_http_error_become_errors():
    kg_index = _html("kgonline.html")
    bad = {"jmlnt": _Session("<html></html>", ""),                 # 沒 csrf
           "tconline": _Session(kg_index, "<html>改版</html>"),     # 0 筆
           "kgonline": _Session(kg_index, "", status=500)}
    calls = iter(["jmlnt", "jmlnt", "tconline", "tconline", "kgonline", "kgonline"])
    slots, errors = hs.fetch_all(date(2026, 11, 2),
                                 session_factory=lambda: bad[next(calls)],
                                 sleep=lambda s: None)
    assert slots == []
    assert set(errors) == {"jmlnt", "tconline", "kgonline"}
    assert "0 筆" in errors["tconline"]


def test_fetch_all_retry_once_then_ok():
    jml = _html("jmlnt.html")
    sessions = iter([_Session(jml, jml, status=503), _Session(jml, jml)])
    slots, errors = hs.fetch_all(date(2026, 11, 2), sites=["jmlnt"],
                                 session_factory=lambda: next(sessions),
                                 sleep=lambda s: None)
    assert errors == {} and slots


def test_fetch_all_blocked_403_not_retried():
    jml = _html("jmlnt.html")
    made = []

    def factory():
        made.append(1)
        return _Session(jml, jml, status=403)

    slots, errors = hs.fetch_all(date(2026, 11, 2), sites=["jmlnt"],
                                 session_factory=factory, sleep=lambda s: None)
    assert len(made) == 1
    assert slots == [] and "HTTPError" in errors["jmlnt"] and "403" in errors["jmlnt"]
