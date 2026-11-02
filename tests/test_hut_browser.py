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

    def goto(self, url, wait_until=None):
        self.url = url
        self.actions.append(("goto", url))

    def content(self):
        return self.html_for(self.url)

    def select_option(self, selector, value=None, label=None):
        self.actions.append(("select", selector, value or label))

    def click(self, selector):
        self.actions.append(("click", selector))

    def wait_for_load_state(self, state=None):
        pass

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
