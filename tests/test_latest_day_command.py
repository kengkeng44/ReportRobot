"""「最新消費」指令。

原本是每日推播的第五張卡，2026-08-16 依使用者要求拿掉 —— 每天固定跳一段
回顧性資訊會稀釋掉推播真正要提醒的事。改成要看時自己問。

排版邏輯本身沒動，測試在 test_daily_spending.py。
"""

import sys

import pytest

import command_router as cr


def _ctx(source_type="user"):
    return {"source_type": source_type, "user_id": "U1"}


def _txn(day, amount, shop="某店"):
    return {"date": day, "amount": amount, "shop": shop, "direction": "支出"}


class FakeNotion:
    def __init__(self, txns=None):
        self._txns = txns or []

    def is_configured(self):
        return True

    def transactions_load(self, limit=200):
        return self._txns


@pytest.fixture
def use_notion(monkeypatch):
    def _install(txns=None):
        monkeypatch.setitem(sys.modules, "notion_db", FakeNotion(txns))
    return _install


# ── parse ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "/最新消費", "最新消費", "/最近一天消費", "/昨天花多少", "/今天花多少",
])
def test_command_parses(text):
    assert cr.parse(text) == ("fin_latest_day", None)


def test_does_not_steal_the_existing_recent_command():
    """「最近消費」本來就是最近 10 筆，行為不能被改掉。"""
    assert cr.parse("/最近消費") == ("fin_recent", None)
    assert cr.parse("/最近交易") == ("fin_recent", None)


def test_is_personal_only():
    assert "fin_latest_day" in cr._PERSONAL_KINDS


def test_blocked_in_group(use_notion):
    use_notion([_txn("2026-08-12", 100, "全聯")])

    assert cr.handle("/最新消費", _ctx("group")) == cr.PERSONAL_ONLY_MSG


# ── handle ─────────────────────────────────────────────────

def test_shows_latest_day(use_notion):
    use_notion([_txn("2026-08-12", 839, "全聯"), _txn("2026-08-11", 50, "超商")])

    reply = cr.handle("/最新消費", _ctx())

    assert "全聯" in reply
    assert "超商" not in reply, "只給最新那一天"
    assert "本月累計" in reply


def test_no_transactions_is_readable(use_notion):
    use_notion([])

    reply = cr.handle("/最新消費", _ctx())

    assert isinstance(reply, str) and reply
    assert "沒有" in reply


# ── 推播不該再有這張卡 ─────────────────────────────────────

def test_daily_carousel_has_no_spending_bubble():
    import flex_builder

    msg = flex_builder.daily_report_carousel(
        extra_text="小知識", weather_text="晴天", premarket_text="盤前",
        today_str="2026-08-16",
    )

    titles = []
    contents = msg["contents"]
    bubbles = contents["contents"] if contents.get("type") == "carousel" else [contents]
    for b in bubbles:
        for el in b["header"]["contents"]:
            if el.get("type") == "text":
                titles.append(el["text"])
                break

    assert "💳 最近一天消費" not in titles


def test_carousel_no_longer_accepts_spending_text():
    """參數留著會讓人以為還能用。拿掉就要真的拿掉。"""
    import inspect

    import flex_builder

    params = inspect.signature(flex_builder.daily_report_carousel).parameters
    assert "spending_text" not in params


def test_daily_report_no_longer_fetches_spending():
    import daily_report

    assert not hasattr(daily_report, "_spending_recent")
