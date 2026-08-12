"""每日推播的食材提醒段落。

核心行為：沒有快過期的食材時要**完全不出現**，不要每天跳一則
「沒有要過期的」—— 那會訓練人略過整則推播。
"""

import pytest

import daily_report
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
        import sys
        fake = FakeNotion(**kwargs)
        monkeypatch.setitem(sys.modules, "notion_db", fake)
        return fake
    return _install


def _pantry(*rows):
    return [{"page_id": f"p{i}", "name": n, "qty": 1, "unit": "顆", "days_left": d}
            for i, (n, d) in enumerate(rows)]


# ── 提醒內容 ──────────────────────────────────────────────

def test_no_bubble_when_nothing_expiring(use_notion):
    use_notion(pantry=_pantry(("高麗菜", 30)))
    assert daily_report._kitchen_reminder() is None


def test_no_bubble_when_pantry_empty(use_notion):
    use_notion(pantry=[])
    assert daily_report._kitchen_reminder() is None


def test_no_bubble_when_notion_not_configured(use_notion):
    use_notion(pantry=_pantry(("菠菜", 1)), configured=False)
    assert daily_report._kitchen_reminder() is None


def test_lists_expiring_items(use_notion):
    use_notion(pantry=_pantry(("菠菜", 1), ("板豆腐", 2), ("米", 300)))

    text = daily_report._kitchen_reminder()

    assert "菠菜" in text and "板豆腐" in text
    assert "米" not in text, "還很久的不該混進提醒"


def test_includes_recipe_suggestion_when_available(use_notion):
    use_notion(
        pantry=_pantry(("菠菜", 1), ("板豆腐", 2)),
        recipes=[{"name": "菠菜豆腐湯", "ingredients": ["菠菜", "板豆腐"], "minutes": 20}],
    )

    text = daily_report._kitchen_reminder()

    assert "菠菜豆腐湯" in text


def test_still_reminds_when_no_recipes(use_notion):
    """食譜庫空的時候，至少要講哪些快過期。"""
    use_notion(pantry=_pantry(("菠菜", 1)), recipes=[])

    text = daily_report._kitchen_reminder()

    assert text and "菠菜" in text


def test_expired_items_are_included(use_notion):
    """已經過期的更要講。"""
    use_notion(pantry=_pantry(("豆腐", -2)))

    text = daily_report._kitchen_reminder()

    assert "豆腐" in text and "已過期" in text


# ── carousel 整合 ─────────────────────────────────────────

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


def test_kitchen_bubble_appears_when_text_given():
    msg = flex_builder.daily_report_carousel(
        extra_text="小知識", weather_text="晴天", premarket_text="盤前",
        today_str="2026-08-12", kitchen_text="⚠️ 菠菜快過期",
    )
    assert "🥬 食材提醒" in _titles(msg)


def test_kitchen_bubble_absent_when_none():
    msg = flex_builder.daily_report_carousel(
        extra_text="小知識", weather_text="晴天", premarket_text="盤前",
        today_str="2026-08-12",
    )
    assert "🥬 食材提醒" not in _titles(msg)


def test_kitchen_bubble_comes_before_weather():
    """有時效、要今天動手的事，要排在天氣前面。"""
    msg = flex_builder.daily_report_carousel(
        extra_text="小知識", weather_text="晴天", premarket_text="盤前",
        today_str="2026-08-12", kitchen_text="⚠️ 菠菜快過期",
    )
    titles = _titles(msg)
    assert titles.index("🥬 食材提醒") < titles.index("🌤️ 天氣報告")


def test_existing_callers_without_kitchen_still_work():
    """kitchen_text 是新增的關鍵字參數，舊呼叫方式不能壞。"""
    msg = flex_builder.daily_report_carousel("a", "b", "c", "2026-08-12")
    assert msg is not None
