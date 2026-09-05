"""每日信的「今日待辦」篩選與排版。

篩選規則（使用者 2026-09-05 確認）：截止日 ≤ 今天  OR  優先度 = P0

兩個維度刻意不合併：截止日回答「什麼時候該做」，P0 回答「不管什麼
時候都得盯著」。只用日期篩，沒設日期的重要事情會消失；只用優先度篩，
時效性就沒了。
"""

from datetime import date

import pytest

import personal

TODAY = date(2026, 9, 5)


@pytest.fixture(autouse=True)
def _clean():
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()
    yield
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()


def _add(text, start=None, end=None, priority=None):
    return personal.add_todo("U1", text, start=start, end=end, priority=priority)


def _texts(rows):
    return [r["text"] for r in rows]


# ── 篩選 ──────────────────────────────────────────────────

def test_due_today_is_included():
    _add("繳健保費", start=TODAY)

    assert _texts(personal.todos_due_today("U1", TODAY)) == ["繳健保費"]


def test_overdue_is_included():
    _add("交資料", start=date(2026, 9, 1))

    assert _texts(personal.todos_due_today("U1", TODAY)) == ["交資料"]


def test_future_is_excluded():
    _add("下週的事", start=date(2026, 9, 12))

    assert personal.todos_due_today("U1", TODAY) == []


def test_p0_without_a_date_is_included():
    """沒設日期的重要事情不該消失。"""
    _add("準備面試", priority="P0")

    assert _texts(personal.todos_due_today("U1", TODAY)) == ["準備面試"]


def test_p0_in_the_future_is_included():
    """P0 不管什麼時候都得盯著。"""
    _add("面試", start=date(2026, 9, 20), priority="P0")

    assert _texts(personal.todos_due_today("U1", TODAY)) == ["面試"]


def test_non_p0_without_a_date_is_excluded():
    """沒日期又不重要的事不進信 —— 那會讓信回到「全部列出來」，
    跟使用者要的安靜相反。LINE 打「待辦」照樣看得到。"""
    _add("有空再說")

    assert personal.todos_due_today("U1", TODAY) == []


def test_end_date_is_the_deadline():
    """一段期間的待辦，截止日看 end 不看 start。
    9/01-9/10 的事在 9/05 還沒到期。"""
    _add("出差", start=date(2026, 9, 1), end=date(2026, 9, 10))

    assert personal.todos_due_today("U1", TODAY) == []


def test_range_ending_today_is_included():
    _add("出差", start=date(2026, 9, 1), end=TODAY)

    assert _texts(personal.todos_due_today("U1", TODAY)) == ["出差"]


def test_other_users_todos_are_not_mine():
    personal.add_todo("U2", "別人的事", start=TODAY)

    assert personal.todos_due_today("U1", TODAY) == []


# ── 排序 ──────────────────────────────────────────────────

def test_overdue_comes_first():
    _add("今天的", start=TODAY)
    _add("逾期的", start=date(2026, 9, 1))
    _add("P0沒日期", priority="P0")

    assert _texts(personal.todos_due_today("U1", TODAY)) == [
        "逾期的", "今天的", "P0沒日期"]


def test_more_overdue_comes_earlier():
    _add("逾期兩天", start=date(2026, 9, 3))
    _add("逾期四天", start=date(2026, 9, 1))

    assert _texts(personal.todos_due_today("U1", TODAY))[0] == "逾期四天"


# ── 排版 ──────────────────────────────────────────────────

def test_format_marks_how_overdue():
    _add("交資料", start=date(2026, 9, 2))

    out = personal.format_today_todos("U1", TODAY)

    assert "逾期 3 天" in out
    assert "交資料" in out


def test_format_marks_today():
    _add("繳健保費", start=TODAY)

    assert "今天" in personal.format_today_todos("U1", TODAY)


def test_format_shows_priority():
    _add("準備面試", priority="P0")

    assert "P0" in personal.format_today_todos("U1", TODAY)


def test_format_returns_none_when_empty():
    """空的區塊直接不放，不要留一張「今天沒待辦」的空卡片 ——
    跟其他每日信區塊同一套。"""
    assert personal.format_today_todos("U1", TODAY) is None


def test_format_does_not_leak_other_days():
    _add("下週的事", start=date(2026, 9, 12))

    assert personal.format_today_todos("U1", TODAY) is None


def test_format_todos_is_untouched():
    """LINE 打「待辦」的行為維持原樣 —— 那支要顯示全部。
    動它會弄壞指令查詢，而那個壞法在信上看不出來。"""
    _add("下週的事", start=date(2026, 9, 12))

    assert "下週的事" in personal.format_todos("U1")
