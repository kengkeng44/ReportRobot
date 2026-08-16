"""「快過期」指令。

原本是每日推播的一張卡，2026-08-16 依使用者要求拿掉 —— 每天固定跳的東西
越多，整則推播越容易被整個略過。改成要看時自己問，「已用掉」按鈕也一起
搬過來，看到就能直接處理。

按鈕本身與 kitchen.expiring_actions 的測試在 test_push_action_buttons.py。
"""

import sys

import pytest

import command_router as cr
import flex_builder


class FakeNotion:
    def __init__(self, pantry=None, recipes=None, configured=True):
        self._pantry = pantry or []
        self._recipes = recipes or []
        self._configured = configured

    def is_configured(self):
        return self._configured

    def pantry_load(self, status="在庫"):
        return self._pantry

    def recipes_load(self, pantry_rows=None):
        return self._recipes


@pytest.fixture
def use_notion(monkeypatch):
    def _install(**kwargs):
        fake = FakeNotion(**kwargs)
        monkeypatch.setitem(sys.modules, "notion_db", fake)
        return fake
    return _install


def _pantry(*rows):
    return [{"page_id": f"p{i}", "name": n, "qty": 1, "unit": "顆", "days_left": d}
            for i, (n, d) in enumerate(rows)]


def _ctx():
    return {"source_type": "user", "user_id": "U1"}


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _texts(bubble):
    return " ".join(n.get("text", "") for n in _walk(bubble) if n.get("type") == "text")


def _buttons(bubble):
    return [n for n in _walk(bubble) if n.get("type") == "button"]


# ── 有快過期的：卡片 + 按鈕 ────────────────────────────────

def test_lists_expiring_items(use_notion):
    use_notion(pantry=_pantry(("菠菜", 1), ("板豆腐", 2), ("米", 300)))

    reply = cr.handle("/快過期", _ctx())
    text = _texts(reply)

    assert "菠菜" in text and "板豆腐" in text
    assert "米" not in text, "還很久的不該混進提醒"


def test_expiring_items_get_buttons(use_notion):
    """看到就能直接處理 —— 這是把卡片從推播搬過來的重點。"""
    use_notion(pantry=_pantry(("菠菜", 1), ("板豆腐", 2)))

    assert len(_buttons(cr.handle("/快過期", _ctx()))) == 2


def test_expired_items_are_included(use_notion):
    """已經過期的更要講。"""
    use_notion(pantry=_pantry(("豆腐", -2)))

    assert "豆腐" in _texts(cr.handle("/快過期", _ctx()))


def test_falls_back_to_text_without_page_id(use_notion):
    """按鈕定位不到 Notion 那列就別給按鈕，但提醒不能消失。"""
    rows = _pantry(("菠菜", 1))
    rows[0]["page_id"] = None
    use_notion(pantry=rows)

    reply = cr.handle("/快過期", _ctx())

    assert isinstance(reply, str), "沒有可操作項目時退回純文字"
    assert "菠菜" in reply


# ── 沒有快過期的 ───────────────────────────────────────────

def test_nothing_expiring_is_still_answered(use_notion):
    """指令跟推播不一樣：使用者主動問就要回答，
    回「沒有要過期的」才是對的，不能靜默。"""
    use_notion(pantry=_pantry(("高麗菜", 30)))

    reply = cr.handle("/快過期", _ctx())

    assert isinstance(reply, str) and reply


def test_empty_pantry_is_answered(use_notion):
    use_notion(pantry=[])

    assert cr.handle("/快過期", _ctx())


# ── 推播不該再有這張卡 ─────────────────────────────────────

def _titles(msg):
    contents = msg["contents"]
    bubbles = contents["contents"] if contents.get("type") == "carousel" else [contents]
    out = []
    for b in bubbles:
        for el in b["header"]["contents"]:
            if el.get("type") == "text":
                out.append(el["text"])
                break
    return out


def test_daily_carousel_has_no_kitchen_bubble():
    msg = flex_builder.daily_report_carousel(
        extra_text="小知識", weather_text="晴天", premarket_text="盤前",
        today_str="2026-08-16",
    )

    assert "🥬 食材提醒" not in _titles(msg)


def test_daily_carousel_is_exactly_the_three_cards():
    """使用者指定推播只留這三張。多一張就是又開始稀釋。"""
    msg = flex_builder.daily_report_carousel(
        extra_text="小知識", weather_text="晴天", premarket_text="盤前",
        today_str="2026-08-16",
    )

    assert _titles(msg) == ["💫 今日一則", "🌤️ 天氣報告", "📊 盤前報告"]


def test_carousel_no_longer_accepts_kitchen_args():
    """參數留著會讓人以為還能用。拿掉就要真的拿掉。"""
    import inspect

    params = inspect.signature(flex_builder.daily_report_carousel).parameters
    for gone in ("kitchen_text", "kitchen_items", "kitchen_more", "spending_text"):
        assert gone not in params


def test_daily_report_no_longer_fetches_kitchen():
    import daily_report

    assert not hasattr(daily_report, "_kitchen_reminder")
    assert not hasattr(daily_report, "_kitchen_payload")
