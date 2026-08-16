"""每日推播的食材提醒卡片直接附「已用掉」按鈕。

目標是減少操作次數：看到「菠菜快過期」不必再打「用掉 菠菜」，直接點卡片。

三個非談不可的點：
- 一張 bubble 不能塞無限顆按鈕 → 只放最急的幾樣，其餘講清楚還有幾樣
- postback 要用 page_id 定位（名稱會重複），且有 300 字元上限
- 卡片會一直留在聊天室，隔天還能點 → 重複點擊不能爆、不能重複扣
"""

import pytest

import command_router as cr
import daily_report
import flex_builder
import kitchen


def _pantry(*rows):
    """rows: (name, days_left) 或 (name, days_left, page_id)"""
    out = []
    for i, r in enumerate(rows):
        name, days = r[0], r[1]
        page_id = r[2] if len(r) > 2 else f"p{i}"
        out.append({"page_id": page_id, "name": name, "qty": 1,
                    "unit": "顆", "days_left": days, "category": "蔬菜"})
    return out


def _walk(node):
    """把 Flex dict 展平成所有子元件。"""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _buttons(bubble):
    return [n for n in _walk(bubble) if n.get("type") == "button"]


def _texts(bubble):
    return [n.get("text", "") for n in _walk(bubble) if n.get("type") == "text"]


# ── 純邏輯：挑出要放按鈕的食材 ────────────────────────────

def test_actions_only_include_expiring_items():
    items, more = kitchen.expiring_actions(
        _pantry(("菠菜", 1), ("米", 300)), threshold_days=3)

    assert [i["name"] for i in items] == ["菠菜"]
    assert more == 0


def test_actions_most_urgent_first():
    items, _ = kitchen.expiring_actions(
        _pantry(("板豆腐", 2), ("菠菜", -1), ("雞蛋", 0)), threshold_days=3)

    assert [i["name"] for i in items] == ["菠菜", "雞蛋", "板豆腐"]


def test_actions_carry_page_id_and_readable_days():
    items, _ = kitchen.expiring_actions(_pantry(("菠菜", 1, "abc-123")))

    assert items[0]["page_id"] == "abc-123"
    assert items[0]["days_text"] == "剩 1 天"


def test_actions_skip_rows_without_page_id():
    """定位不到 Notion 那一列就不要放按鈕 —— 按了也做不了事。"""
    rows = _pantry(("菠菜", 1), ("板豆腐", 1))
    rows[0]["page_id"] = None

    items, more = kitchen.expiring_actions(rows)

    assert [i["name"] for i in items] == ["板豆腐"]
    assert more == 0, "跳過的不算進『還有幾樣』，那是另一回事"


def test_actions_cap_count_and_report_remainder():
    rows = _pantry(*[(f"菜{i}", i % 3) for i in range(9)])

    items, more = kitchen.expiring_actions(rows, threshold_days=3, limit=4)

    assert len(items) == 4
    assert more == 5


# ── Flex：按鈕怎麼長 ──────────────────────────────────────

def test_bubble_has_one_button_per_item():
    items, _ = kitchen.expiring_actions(_pantry(("菠菜", 1), ("板豆腐", 2)))

    bubble = flex_builder.kitchen_reminder_bubble(items, subtitle="2026-08-13")

    assert len(_buttons(bubble)) == 2


def test_button_postback_carries_page_id():
    items, _ = kitchen.expiring_actions(_pantry(("菠菜", 1, "abc-123")))

    bubble = flex_builder.kitchen_reminder_bubble(items)
    action = _buttons(bubble)[0]["action"]

    assert action["type"] == "postback"
    assert "abc-123" in action["data"]
    assert "pantry_used" in action["data"]


def test_postback_data_within_line_limit():
    """LINE postback data 上限 300 字元；中文 urlencode 後會膨脹 9 倍。"""
    long_name = "超級無敵長的食材名稱" * 5
    items, _ = kitchen.expiring_actions(
        [{"page_id": "b1f2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
          "name": long_name, "days_left": 1}])

    bubble = flex_builder.kitchen_reminder_bubble(items)

    for b in _buttons(bubble):
        assert len(b["action"]["data"]) <= 300


def test_button_has_display_text_so_user_sees_feedback():
    """點了沒反應會讓人以為壞了 —— displayText 讓使用者那側立刻有回饋。"""
    items, _ = kitchen.expiring_actions(_pantry(("菠菜", 1)))

    action = _buttons(flex_builder.kitchen_reminder_bubble(items))[0]["action"]

    assert "菠菜" in action["displayText"]


def test_bubble_lists_item_names_and_days():
    items, _ = kitchen.expiring_actions(_pantry(("菠菜", 1)))

    texts = " ".join(_texts(flex_builder.kitchen_reminder_bubble(items)))

    assert "菠菜" in texts and "剩 1 天" in texts


def test_bubble_says_how_many_more_are_hidden():
    items, more = kitchen.expiring_actions(
        _pantry(*[(f"菜{i}", 1) for i in range(8)]), limit=3)

    texts = " ".join(_texts(flex_builder.kitchen_reminder_bubble(items, more_count=more)))

    assert "5" in texts, "被截掉的要講清楚有幾樣，不能默默吞掉"


def test_bubble_keeps_recipe_suggestion_text():
    items, _ = kitchen.expiring_actions(_pantry(("菠菜", 1)))

    texts = " ".join(_texts(
        flex_builder.kitchen_reminder_bubble(items, extra_text="💡 建議今天煮\n・菠菜豆腐湯")))

    assert "菠菜豆腐湯" in texts


def test_bubble_title_matches_text_version():
    """標題要跟純文字版一致，carousel 的順序測試才不會兩套。"""
    items, _ = kitchen.expiring_actions(_pantry(("菠菜", 1)))
    bubble = flex_builder.kitchen_reminder_bubble(items)

    assert bubble["header"]["contents"][0]["text"] == "🥬 食材提醒"


# ── postback：點下去之後 ──────────────────────────────────

@pytest.fixture
def fake_pantry(monkeypatch):
    import notion_db

    store = {"rows": _pantry(("菠菜", 1, "pid-1")), "shopping": [], "fail": False}

    def _set_status(pid, status):
        if store["fail"]:
            return False
        row = next((r for r in store["rows"] if r["page_id"] == pid), None)
        if row is None:
            return False
        store["rows"].remove(row)
        return True

    monkeypatch.setattr(notion_db, "pantry_load", lambda status="在庫": list(store["rows"]))
    monkeypatch.setattr(notion_db, "pantry_set_status", _set_status)
    monkeypatch.setattr(
        notion_db, "shopping_add",
        lambda name, category=None, source="手動", qty=1: (
            store["shopping"].append(name) or "sid"),
    )
    return store


def _click(page_id="pid-1", name="菠菜"):
    from urllib.parse import urlencode
    return cr.handle_postback(
        urlencode({"action": "pantry_used", "pid": page_id, "n": name}), "U1")


def test_click_marks_used_and_adds_to_shopping_list(fake_pantry):
    reply = _click()

    assert "菠菜" in reply
    assert fake_pantry["rows"] == []
    assert fake_pantry["shopping"] == ["菠菜"]


def test_second_click_is_friendly_and_does_not_double_add(fake_pantry):
    """推播卡片會一直留在聊天室，隔天照樣點得到。"""
    _click()

    reply = _click()

    assert isinstance(reply, str)
    assert "菠菜" in reply
    assert fake_pantry["shopping"] == ["菠菜"], "不能重複加進採購清單"


def test_click_reports_notion_write_failure(fake_pantry):
    fake_pantry["fail"] = True

    reply = _click()

    assert "失敗" in reply or "稍後" in reply
    assert fake_pantry["shopping"] == [], "沒寫成功就不該加採購清單"


def test_other_family_member_cannot_change_pantry(fake_pantry, monkeypatch):
    """每日情報推到家人群組，按鈕誰都按得到；庫存是個人資料，只認本人。"""
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "U-owner")

    reply = cr.handle_postback("action=pantry_used&pid=pid-1&n=%E8%8F%A0%E8%8F%9C",
                               "U-someone-else")

    assert "本人" in reply
    assert len(fake_pantry["rows"]) == 1, "別人按不能動到庫存"
    assert fake_pantry["shopping"] == []


def test_owner_can_change_pantry(fake_pantry, monkeypatch):
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "U-owner")

    reply = cr.handle_postback("action=pantry_used&pid=pid-1&n=%E8%8F%A0%E8%8F%9C",
                               "U-owner")

    assert "菠菜" in reply
    assert fake_pantry["rows"] == []


def test_click_without_page_id_is_readable(fake_pantry):
    reply = cr.handle_postback("action=pantry_used&n=%E8%8F%A0%E8%8F%9C", "U1")

    assert isinstance(reply, str) and reply
