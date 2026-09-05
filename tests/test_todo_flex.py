"""待辦清單卡片與防呆按鈕卡。"""

import json
from datetime import date

import flex_builder


def _dump(msg):
    return json.dumps(msg, ensure_ascii=False)


CHOICES = (("今天", "today"), ("明天", "tomorrow"), ("不設", "none"))


# ── 防呆按鈕卡 ────────────────────────────────────────────

def test_due_prompt_carries_the_todo_id():
    """按鈕要更新**那一筆**，不是新增第二筆。"""
    msg = flex_builder.todo_due_prompt_flex(7, CHOICES)

    assert "id=7" in _dump(msg)


def test_due_prompt_has_every_choice():
    text = _dump(flex_builder.todo_due_prompt_flex(7, CHOICES))

    for label, key in CHOICES:
        assert label in text
        assert f"d={key}" in text


def test_due_prompt_uses_postback_not_message():
    """message 型的按鈕會在對話裡留下一句「今天」，
    而且會被待命攔截或指令解析再處理一次。"""
    text = _dump(flex_builder.todo_due_prompt_flex(7, CHOICES))

    assert "postback" in text
    assert '"type": "message"' not in text


# ── 清單卡片 ──────────────────────────────────────────────

def _item(tid=1, text="交資料", start=None, end=None, priority=None):
    return {"id": tid, "text": text, "start": start, "end": end,
            "priority": priority}


def test_list_has_an_add_button():
    """使用者要的是「按一顆按鈕就能加」，不是打 /待辦 加。"""
    msg = flex_builder.todo_list_flex([_item()])

    assert "todo_add_start" in _dump(msg)


def test_empty_list_also_has_the_add_button():
    """清單空的時候最需要那顆按鈕。"""
    msg = flex_builder.todo_list_flex([])

    assert "todo_add_start" in _dump(msg)


def test_due_date_is_shown():
    msg = flex_builder.todo_list_flex([_item(start=date(2026, 9, 8))])

    assert "9/08" in _dump(msg)


def test_date_range_shows_both_ends():
    msg = flex_builder.todo_list_flex(
        [_item(start=date(2026, 9, 1), end=date(2026, 9, 10))])
    text = _dump(msg)

    assert "9/01" in text and "9/10" in text


def test_priority_is_shown():
    msg = flex_builder.todo_list_flex([_item(priority="P0")])

    assert "P0" in _dump(msg)


def test_item_without_dates_still_renders():
    """既有待辦兩欄都是空的。這裡炸掉的話清單整個打不開。"""
    msg = flex_builder.todo_list_flex([{"id": 1, "text": "交資料"}])

    assert "交資料" in _dump(msg)


def test_complete_button_survived():
    """加東西不能弄掉既有的按鈕。"""
    assert "todo_complete" in _dump(flex_builder.todo_list_flex([_item()]))
