"""➕ 與截止日按鈕的 postback。"""

import json
from datetime import date, timedelta

import pytest

import command_router as cr
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


def _text(reply):
    return json.dumps(reply, ensure_ascii=False, default=str)


# ── ➕ ────────────────────────────────────────────────────

def test_plus_enters_pending_state():
    cr.handle_postback("action=todo_add_start", "U1")

    assert personal.is_pending_todo("U1") is True


def test_plus_says_go_ahead():
    """使用者要知道機器人在等他講話。"""
    reply = cr.handle_postback("action=todo_add_start", "U1")

    assert "請說" in _text(reply)


def test_pressing_plus_twice_is_harmless():
    cr.handle_postback("action=todo_add_start", "U1")
    cr.handle_postback("action=todo_add_start", "U1")

    assert personal.is_pending_todo("U1") is True


# ── 截止日按鈕 ────────────────────────────────────────────

def test_today_sets_todays_date(monkeypatch):
    monkeypatch.setattr(
        cr, "_due_from_key",
        lambda key, today: date(2026, 9, 5) if key == "today" else None)
    tid = personal.add_todo("U1", "交資料")

    cr.handle_postback(f"action=todo_set_due&id={tid}&d=today", "U1")

    assert personal.list_todos("U1")[0]["start"] == date(2026, 9, 5)


def test_none_leaves_it_unset():
    """「不設」不是「設成今天」。"""
    tid = personal.add_todo("U1", "交資料")

    reply = cr.handle_postback(f"action=todo_set_due&id={tid}&d=none", "U1")

    assert personal.list_todos("U1")[0]["start"] is None
    assert "不設" in _text(reply)


def test_missing_todo_id_is_reported():
    reply = cr.handle_postback("action=todo_set_due&id=99&d=today", "U1")

    assert "找不到" in _text(reply)


def test_reply_shows_the_date_that_was_set():
    tid = personal.add_todo("U1", "交資料")

    reply = _text(
        cr.handle_postback(f"action=todo_set_due&id={tid}&d=tomorrow", "U1"))

    assert "已設" in reply or "📅" in reply


# ── _due_from_key（純邏輯）────────────────────────────────

SAT = date(2026, 9, 5)          # 週六


def test_key_today():
    assert cr._due_from_key("today", SAT) == SAT


def test_key_tomorrow():
    assert cr._due_from_key("tomorrow", SAT) == SAT + timedelta(days=1)


def test_key_friday_takes_the_next_friday():
    """週六按「週五」→ 下一個週五 9/11，不是已經過去的 9/04。
    按鈕設出一個昨天的截止日，等於一按就逾期。"""
    assert cr._due_from_key("friday", SAT) == date(2026, 9, 11)


def test_key_friday_on_a_friday_is_today():
    """在週五按「週五」就是今天 —— 「今天要交」是最常見的說法。"""
    assert cr._due_from_key("friday", date(2026, 9, 11)) == date(2026, 9, 11)


def test_key_next_monday():
    assert cr._due_from_key("next_monday", SAT) == date(2026, 9, 7)


def test_key_none():
    assert cr._due_from_key("none", SAT) is None


def test_unknown_key():
    assert cr._due_from_key("whatever", SAT) is None
