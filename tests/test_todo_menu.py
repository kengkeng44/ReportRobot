"""待辦分頁（Rich Menu 六格）與它對應的指令。

2026-09-07 使用者要求：按「待辦」直接看到「加 P0 / 加待辦」，
不要再先看清單、再按 ➕。清單本身改成只列 P0。

加待辦走 **prompt 預填鍵盤**（`待辦 P0 `）而不是 postback 待命：
少一步，而且沒有隱藏狀態 —— 使用者看得到鍵盤裡填了什麼，
不會發生「按了 ➕ 然後忘記自己在待命中」。
➕ 那條路留著不動，兩條並存。
"""

import json

import pytest

import command_router as cr
import flex_builder
import personal
import setup_richmenu as rm

PERSONAL_CTX = {"source_type": "user", "user_id": "U1"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()
    import todo_parse
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "NONE")
    yield
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()


def _dump(msg):
    return json.dumps(msg, ensure_ascii=False, default=str)


# ── 選單結構 ──────────────────────────────────────────────

def _cells(key):
    return rm.MENUS[key]["cells"]


def _actions(key):
    return [a for _l, _s, _c, a in _cells(key)]


def test_todo_menu_exists():
    assert "todo" in rm.MENUS


def test_main_todo_cell_switches_to_the_page():
    """主選單的「待辦」從直接送指令改成翻頁。
    翻頁是 channel 層行為，不送 webhook 也不計 push 配額。"""
    todo_cells = [a for label, _s, _c, a in _cells("main") if label == "待辦"]

    assert todo_cells == [("switch", "todo")]


def test_todo_menu_has_six_cells():
    assert len(_cells("todo")) == 6


def test_todo_menu_can_go_back():
    assert ("switch", "main") in _actions("todo")


def test_add_p0_prefills_the_keyboard():
    """prompt 只開鍵盤並預填，使用者補完內容才送出。"""
    assert ("prompt", "待辦 P0 ") in _actions("todo")


def test_add_plain_todo_prefills_without_a_priority():
    """不帶優先度 = 一般事項。P1/P2/P3 在系統裡行為完全一樣，
    各佔一格是浪費版面 —— 真正的問題只有「重不重要」。"""
    assert ("prompt", "待辦 ") in _actions("todo")


def test_todo_menu_has_both_list_views():
    assert ("message", "/待辦") in _actions("todo")
    assert ("message", "/待辦 全部") in _actions("todo")


def test_reminder_moved_onto_the_todo_page():
    """提醒跟待辦是同一類事，放在一起比埋在「更多」裡好找。"""
    assert ("message", "/提醒") in _actions("todo")


# ── 清單只列 P0 ───────────────────────────────────────────

def test_list_shows_only_p0():
    personal.add_todo("U1", "重要的", priority="P0")
    personal.add_todo("U1", "普通的")

    reply = _dump(cr.handle("/待辦", PERSONAL_CTX))

    assert "重要的" in reply
    assert "普通的" not in reply


def test_all_shows_everything():
    personal.add_todo("U1", "重要的", priority="P0")
    personal.add_todo("U1", "普通的")

    reply = _dump(cr.handle("/待辦 全部", PERSONAL_CTX))

    assert "重要的" in reply
    assert "普通的" in reply


def test_empty_p0_list_does_not_claim_you_have_nothing():
    """有 10 筆普通待辦時說「目前沒有待辦事項」是騙人的。"""
    personal.add_todo("U1", "普通的")

    reply = _dump(cr.handle("/待辦", PERSONAL_CTX))

    assert "沒有待辦事項" not in reply


def test_p0_list_offers_a_way_to_see_everything():
    """非 P0 的待辦在信裡看不到，清單再濾掉就完全消失了。"""
    personal.add_todo("U1", "重要的", priority="P0")

    reply = _dump(flex_builder.todo_list_flex(
        personal.todos_important("U1"), only_important=True))

    assert "全部" in reply


def test_full_list_has_no_see_all_button():
    """已經在全部清單裡了，再放一顆「看全部」只是雜訊。"""
    personal.add_todo("U1", "普通的")

    reply = _dump(flex_builder.todo_list_flex(personal.list_todos("U1")))

    assert "/待辦 全部" not in reply


# ── 預填鍵盤送回來的訊息 ──────────────────────────────────

def test_prefilled_message_creates_a_todo_with_priority():
    """鍵盤預填「待辦 P0 」，使用者補「交社宅資料」送出。"""
    cr.handle("待辦 P0 交社宅資料", PERSONAL_CTX)

    item = personal.list_todos("U1")[0]
    assert item["text"] == "交社宅資料"
    assert item["priority"] == "P0"


def test_prefilled_message_parses_dates_too():
    cr.handle("待辦 P0 明天交社宅資料", PERSONAL_CTX)

    item = personal.list_todos("U1")[0]
    assert item["text"] == "交社宅資料"
    assert item["start"] is not None


def test_prefilled_message_without_priority():
    cr.handle("待辦 買牛奶", PERSONAL_CTX)

    item = personal.list_todos("U1")[0]
    assert item["text"] == "買牛奶"
    assert item["priority"] is None


def test_bare_prefill_does_not_create_an_empty_todo():
    """使用者按了「加待辦」但沒補內容就送出。"""
    cr.handle("待辦 ", PERSONAL_CTX)

    assert personal.list_todos("U1") == []


def test_malformed_subcommand_does_not_become_a_todo():
    """「待辦 完成 abc」是打錯的指令，不是一件叫「完成 abc」的事。"""
    cr.handle("待辦 完成 abc", PERSONAL_CTX)

    assert personal.list_todos("U1") == []


def test_existing_add_subcommand_still_works():
    """/待辦 加 X 是既有用法，不能弄壞。"""
    cr.handle("/待辦 加 繳健保費", PERSONAL_CTX)

    assert personal.list_todos("U1")[0]["text"] == "繳健保費"
