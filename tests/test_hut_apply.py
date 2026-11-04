import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "hut_apply", Path(__file__).parent.parent / "tools" / "hut_apply.py")
ha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ha)

TEAM = {"team_name": "喵隊",
        "leader": {"name": "王小明", "id_no": "A123456789", "phone": "0900"},
        "members": [{"name": "李小華", "id_no": "B223456789"}]}


def _ctx(**kw):
    c = {"site": "jmlnt", "date": date(2026, 12, 12), "nights": 1, "people": 2,
         "dry_run": False, "team": TEAM}
    c.update(kw)
    return c


class FakePage:
    def __init__(self):
        self.actions = []

    def goto(self, url, wait_until=None):
        self.actions.append(("goto", url))

    def content(self):
        return "<html>ok</html>"

    def fill(self, sel, value):
        self.actions.append(("fill", sel, value))

    def select_option(self, sel, label=None):
        self.actions.append(("select", sel, label))

    def click(self, sel):
        self.actions.append(("click", sel))

    def wait_for_load_state(self, state):
        pass

    def wait_for_selector(self, sel, timeout=None):
        self.actions.append(("wait", sel))

    def screenshot(self, **kw):
        return b"\xff\xd8\xffPAGE"

    def locator(self, sel):
        page = self

        class L:
            first = None

            def screenshot(self, **kw):
                page.actions.append(("shot", sel))
                return b"\x89PNG\r\n\x1a\nCAP"
        loc = L()
        loc.first = loc
        return loc


class FakeApi:
    def __init__(self, states):
        self.calls, self.states = [], list(states)

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/admin/hut-captcha":
            return {"id": "c1"}
        if method == "GET":
            return self.states.pop(0)
        return {"ok": True}


STEPS = [("goto", "login.php"), ("fill", "#acc", "account"), ("fill", "#pw", "password"),
         ("captcha", "#capimg", "#capin", "login"), ("click", "#go"), ("wait", "#menu"),
         ("fill", "#leader", "leader.name"), ("fill", "#m0", "members.0.name"),
         ("captcha", "#capimg2", "#capin2", "apply"), ("submit", "#send")]


def test_run_steps_full(monkeypatch):
    monkeypatch.setenv("HUT_FOREST_ACCOUNT", "me@x")
    monkeypatch.setenv("HUT_FOREST_PASSWORD", "pw")
    page = FakePage()
    api = FakeApi([{"state": "waiting"}, {"state": "answered", "code": "1111"},
                   {"state": "answered", "code": "2222"}])
    cid = ha.run_steps(page, STEPS, _ctx(), api=api, sleep=lambda s: None)
    assert cid == "c1"
    assert ("fill", "#acc", "me@x") in page.actions
    assert ("fill", "#capin", "1111") in page.actions
    assert ("fill", "#capin2", "2222") in page.actions
    assert page.actions[-1] == ("click", "#send")
    sent = [b for m, p, b in api.calls if p == "/admin/hut-captcha"]
    assert sent[0]["step"] == "login" and sent[1]["step"] == "apply"
    assert "A123456789" not in json.dumps(sent, ensure_ascii=False)   # 身分證不上 LINE
    assert "王小明" in sent[1]["summary"]


def test_per_site_secret_override(monkeypatch):
    monkeypatch.setenv("HUT_FOREST_ACCOUNT", "general")
    monkeypatch.setenv("HUT_KGONLINE_ACCOUNT", "kg")
    assert ha._secret("kgonline", "ACCOUNT") == "kg"
    assert ha._secret("jmlnt", "ACCOUNT") == "general"


def test_dry_run_stops_before_apply_captcha(monkeypatch):
    page = FakePage()
    api = FakeApi([])
    steps = [("goto", "x"), ("captcha", "#i", "#c", "apply"), ("submit", "#send")]
    assert ha.run_steps(page, steps, _ctx(dry_run=True), api=api, sleep=lambda s: None) is None
    assert api.calls == [] and ("click", "#send") not in page.actions


def test_cancel_stops(monkeypatch):
    api = FakeApi([{"state": "cancelled"}])
    with pytest.raises(ha.Stop, match="取消"):
        ha.ask_captcha(b"\x89PNG", "x", "apply", api=api, sleep=lambda s: None)


def test_poll_timeout():
    t = iter(range(0, 10000, 100))
    api = FakeApi([{"state": "waiting"}] * 100)
    with pytest.raises(ha.Stop, match="逾時"):
        ha.ask_captcha(b"\x89PNG", "x", "apply", api=api, sleep=lambda s: None,
                       clock=lambda: next(t))


def test_waf_block_stops():
    page = FakePage()
    page.content = lambda: "Request Rejected"
    with pytest.raises(ha.Stop, match="WAF"):
        ha.run_steps(page, [("goto", "x")], _ctx(), api=FakeApi([]), sleep=lambda s: None)


def test_missing_team_value():
    with pytest.raises(ha.Stop, match="members.5.name"):
        ha._value("members.5.name", _ctx())


def test_flows_not_ready_until_recon():
    with pytest.raises(ha.NotReady, match="recon"):
        ha.check_ready("forest")


def test_load_team(monkeypatch):
    monkeypatch.delenv("HUT_TEAM_JSON", raising=False)
    with pytest.raises(ha.Stop):
        ha.load_team()
    monkeypatch.setenv("HUT_TEAM_JSON", json.dumps(TEAM))
    assert ha.load_team()["leader"]["name"] == "王小明"
