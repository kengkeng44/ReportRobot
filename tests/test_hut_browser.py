import importlib.util
from pathlib import Path

import hut_onestop

_spec = importlib.util.spec_from_file_location(
    "hut_browser", Path(__file__).parent.parent / "tools" / "hut_browser.py")
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)

CAL = """<table class="table_bed"><tr><td><a href="bed_6main.aspx?node_id=3&sdate=2026-12-05">
<span>餘額</span> (<span>2</span>,<span>0</span>) <span>排隊預約</span> <span>9</span></a></td></tr></table>"""


class FakePage:
    def __init__(self, html_for=lambda url: CAL):
        self.html_for, self.url, self.actions = html_for, "", []
        self.response = None

    def goto(self, url, wait_until=None):
        self.url = url
        self.actions.append(("goto", url))
        return self.response

    def content(self):
        return self.html_for(self.url)

    def select_option(self, selector, value=None, label=None):
        self.actions.append(("select", selector, value or label))

    def click(self, selector):
        self.actions.append(("click", selector))

    def wait_for_load_state(self, state=None):
        pass

    def wait_for_selector(self, selector, timeout=None):
        self.actions.append(("wait", selector))

    def eval_on_selector(self, selector, script):
        return {"#con_ddlYear": "2026", "#con_ddlMonth": "12"}[selector]


def test_collect_all_targets():
    page = FakePage()
    slots, errors = hb.collect_onestop(page, sleep=lambda s: None)
    assert errors == {}
    assert {s.site for s in slots} == set(hut_onestop.ONESTOP)
    assert ("select", "#con_ddlYear", "2027") in page.actions   # 12 月的下一個月跨年


def test_blocked_stops_everything():
    page = FakePage(html_for=lambda url: "<title>Request Rejected</title>")
    slots, errors = hb.collect_onestop(page, sleep=lambda s: None)
    assert slots == []
    assert set(errors) == set(hut_onestop.ONESTOP)
    assert all("Request Rejected" in m for m in errors.values())
    assert sum(1 for a in page.actions if a[0] == "goto") == 1   # 第一個被擋就停


def test_zero_rows_is_error():
    page = FakePage(html_for=lambda url: "<html>改版</html>")
    _, errors = hb.collect_onestop(page, sleep=lambda s: None)
    assert all("0 筆" in m for m in errors.values())


def test_http_error_stops_batch():
    page = FakePage()
    page.response = type("R", (), {"status": 403})()
    slots, errors = hb.collect_onestop(page, sleep=lambda s: None)
    assert slots == []
    assert set(errors) == set(hut_onestop.ONESTOP)
    assert all("HTTP 403" in m for m in errors.values())
    assert sum(1 for a in page.actions if a[0] == "goto") == 1


def test_timeout_stops_batch():
    class TimeoutError(Exception):
        pass

    class P(FakePage):
        def goto(self, url, wait_until=None):
            super().goto(url, wait_until)
            raise TimeoutError("slow")

    page = P()
    slots, errors = hb.collect_onestop(page, sleep=lambda s: None)
    assert slots == [] and set(errors) == set(hut_onestop.ONESTOP)
    assert sum(1 for a in page.actions if a[0] == "goto") == 1


def test_gap_between_every_page_hit():
    events = []

    class P(FakePage):
        def goto(self, url, wait_until=None):
            events.append("hit")
            return super().goto(url, wait_until)

        def click(self, selector):
            events.append("hit")
            super().click(selector)

        def select_option(self, selector, value=None, label=None):
            events.append("hit")   # 選單會自動 postback，也算一次請求
            super().select_option(selector, value, label)

    page = P()
    hb.collect_onestop(page, sleep=lambda s: events.append(("sleep", s)))
    hits = [i for i, e in enumerate(events) if e == "hit"]
    for a, b in zip(hits, hits[1:]):
        between = [e for e in events[a:b] if e != "hit"]
        assert between and all(s >= 4 for _, s in between)
    assert events[-1] == "hit"   # 最後一站後不再多睡



def test_search_clicked_only_when_calendar_missing():
    """選宿營地後月曆沒自動出來，才停 4 秒按查詢。"""
    gaps = []

    class Timeout(Exception):
        pass
    Timeout.__name__ = "TimeoutError"

    class P(FakePage):
        def wait_for_selector(self, selector, timeout=None):
            super().wait_for_selector(selector, timeout)
            if timeout == 10000:
                raise Timeout()

    page = P()
    hb._open_target(page, "bed_6.aspx", "排雲山莊", sleep=gaps.append)
    assert ("click", "#con_btnsearch") in page.actions
    assert gaps == [hb.PAGE_GAP, hb.PAGE_GAP]


def test_next_month_waits_for_that_month():
    page = FakePage()
    hb._next_month(page, sleep=lambda s: None)
    assert ("wait", "table.table_bed a[href*='sdate=2027-01']") in page.actions
    assert ("select", "#con_ddlYear", "2027") in page.actions
