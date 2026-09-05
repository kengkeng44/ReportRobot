"""按下 ➕ 之後的「待命」狀態。

只活在記憶體：壽命是幾秒，為它多一次 Notion 往返不划算。
Railway 重啟丟掉的代價只是使用者要重按一次 ➕。
"""

from datetime import date, timedelta

import pytest

import personal


@pytest.fixture(autouse=True)
def _clean():
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()
    yield
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()


def test_not_pending_by_default():
    assert personal.is_pending_todo("U1") is False


def test_start_makes_it_pending():
    personal.start_pending_todo("U1")

    assert personal.is_pending_todo("U1") is True


def test_pending_is_per_user():
    personal.start_pending_todo("U1")

    assert personal.is_pending_todo("U2") is False


def test_clear_ends_it():
    personal.start_pending_todo("U1")
    personal.clear_pending_todo("U1")

    assert personal.is_pending_todo("U1") is False


def test_clearing_when_not_pending_is_harmless():
    personal.clear_pending_todo("U1")     # 不該炸

    assert personal.is_pending_todo("U1") is False


def test_pressing_plus_twice_restarts_the_clock():
    """按兩次 ➕ 不該報錯，而且要重新計時。"""
    personal.start_pending_todo("U1")
    old = personal._PENDING_TODO["U1"]
    personal.start_pending_todo("U1")

    assert personal._PENDING_TODO["U1"] >= old
    assert personal.is_pending_todo("U1") is True


def test_pending_expires():
    """沒有逾時的話，一個忘掉的待命狀態會把使用者隔天隨口講的
    任何一句話變成待辦。"""
    personal.start_pending_todo("U1")
    personal._PENDING_TODO["U1"] -= timedelta(
        minutes=personal.PENDING_TODO_TIMEOUT_MINUTES + 1)

    assert personal.is_pending_todo("U1") is False


def test_expiry_boundary_is_still_pending():
    personal.start_pending_todo("U1")
    personal._PENDING_TODO["U1"] -= timedelta(
        minutes=personal.PENDING_TODO_TIMEOUT_MINUTES - 1)

    assert personal.is_pending_todo("U1") is True


def test_expired_state_is_swept():
    """逾時的項目要真的刪掉，不然 dict 會一直長。"""
    personal.start_pending_todo("U1")
    personal._PENDING_TODO["U1"] -= timedelta(
        minutes=personal.PENDING_TODO_TIMEOUT_MINUTES + 1)

    personal.is_pending_todo("U1")

    assert "U1" not in personal._PENDING_TODO


# ── add_todo 帶日期與優先度 ───────────────────────────────

def test_add_todo_keeps_the_date_in_memory():
    """Notion 沒設定時（本機、以及 NOTION_TOKEN 掉了的時候）
    仍然要記得日期，不然清單卡片會顯示成沒有截止日。"""
    personal.add_todo("U1", "交資料", start=date(2026, 9, 8), priority="P0")

    item = personal.list_todos("U1")[0]
    assert item["start"] == date(2026, 9, 8)
    assert item["end"] is None
    assert item["priority"] == "P0"


def test_add_todo_without_dates_still_works():
    """既有呼叫端（/待辦 加 X）不帶新參數。"""
    personal.add_todo("U1", "交資料")

    item = personal.list_todos("U1")[0]
    assert item["start"] is None
    assert item["priority"] is None


def test_set_todo_due_updates_in_memory():
    tid = personal.add_todo("U1", "交資料")

    assert personal.set_todo_due("U1", tid, date(2026, 9, 8)) is True
    assert personal.list_todos("U1")[0]["start"] == date(2026, 9, 8)


def test_set_todo_due_on_a_missing_id():
    assert personal.set_todo_due("U1", 99, date(2026, 9, 8)) is False
