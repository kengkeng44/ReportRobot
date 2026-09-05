"""待命中收到訊息時 command_router 的行為。

規則（使用者 2026-09-05 確認）：
  認得出是已知指令 → 解除待命、回一句「已取消」，然後照常執行那個指令
  認不出來         → 當作待辦內容記下來

刻意不是「以 / 開頭就解除」：Rich Menu 的按鈕送的是**文字訊息**，
而且不是每顆都有斜線（setup_richmenu.py 的「記一筆」就沒有）。
只認斜線的話，按「記一筆」會得到一筆叫「記一筆」的待辦。
"""

import json

import pytest

import command_router as cr
import personal

PERSONAL_CTX = {"source_type": "user", "user_id": "U1"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()
    # 日期解析不該打 AI
    import todo_parse
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "NONE")
    yield
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()


def _text(reply):
    """handle 可能回 str / dict / list，全部攤成一段字串好斷言。"""
    return json.dumps(reply, ensure_ascii=False, default=str)


def test_unknown_text_becomes_a_todo():
    personal.start_pending_todo("U1")

    reply = cr.handle("交社宅資料", PERSONAL_CTX)

    assert personal.list_todos("U1")[0]["text"] == "交社宅資料"
    assert "已記下" in _text(reply)


def test_recording_ends_the_pending_state():
    """記完就離開待命，下一句話是普通訊息。"""
    personal.start_pending_todo("U1")
    cr.handle("交社宅資料", PERSONAL_CTX)

    assert personal.is_pending_todo("U1") is False


def test_a_known_command_cancels_instead_of_recording():
    """按了 ➕ 又改按別的按鈕時，不該記下一筆叫「快過期」的待辦。"""
    personal.start_pending_todo("U1")

    reply = cr.handle("help", PERSONAL_CTX)

    assert personal.list_todos("U1") == []
    assert "已取消" in _text(reply)
    assert personal.is_pending_todo("U1") is False


def test_the_cancelled_command_still_runs():
    """解除待命之後那個指令要照常執行，不是吞掉。"""
    personal.start_pending_todo("U1")

    reply = _text(cr.handle("help", PERSONAL_CTX))

    assert "待辦清單" in reply          # HELP_TEXT 的內容


def test_bare_richmenu_button_also_cancels():
    """Rich Menu 的「記一筆」送的是**沒有斜線**的裸文字。"""
    personal.start_pending_todo("U1")

    cr.handle("記一筆", PERSONAL_CTX)

    assert personal.list_todos("U1") == []


def test_not_pending_means_normal_handling():
    """沒按 ➕ 時隨口講的話不該變成待辦。"""
    reply = cr.handle("交社宅資料", PERSONAL_CTX)

    assert personal.list_todos("U1") == []
    assert reply is None or "已記下" not in _text(reply)


def test_group_chat_never_records():
    """待辦是個人功能。群組裡就算狀態殘留也不能記。"""
    personal.start_pending_todo("U1")

    cr.handle("交社宅資料", {"source_type": "group", "user_id": "U1"})

    assert personal.list_todos("U1") == []


def test_date_and_priority_are_parsed():
    personal.start_pending_todo("U1")

    cr.handle("P0 明天交社宅資料", PERSONAL_CTX)

    item = personal.list_todos("U1")[0]
    assert item["text"] == "交社宅資料"
    assert item["priority"] == "P0"
    assert item["start"] is not None


def test_missing_date_triggers_the_fallback_buttons():
    """沒講日期時：內容先記下來，再跳按鈕。"""
    personal.start_pending_todo("U1")

    reply = cr.handle("交社宅資料", PERSONAL_CTX)

    assert personal.list_todos("U1")[0]["text"] == "交社宅資料"
    assert "todo_set_due" in _text(reply)


def test_date_given_means_no_buttons():
    personal.start_pending_todo("U1")

    reply = cr.handle("明天交社宅資料", PERSONAL_CTX)

    assert "todo_set_due" not in _text(reply)


def test_empty_message_does_not_record_a_blank_todo():
    personal.start_pending_todo("U1")

    cr.handle("   ", PERSONAL_CTX)

    assert personal.list_todos("U1") == []
