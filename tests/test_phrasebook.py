"""每日三句的排程決策。

英/西走固定間隔重複,中文金句走隨機不重複 —— 兩種節奏不同的理由見
docs/superpowers/specs/2026-09-01-phrasebook-and-spending-chart-design.md 2.3。

這個模組不碰 Notion 也不碰 AI,所以整份測試沒有任何 mock。
"""

from datetime import date

import phrasebook


D = date(2026, 9, 1)


# ── 間隔表 ────────────────────────────────────────────────

def test_intervals_follow_forgetting_curve():
    """使用者要的是「隔一個月、三個月再重來」,對應第 3、第 4 級。"""
    assert phrasebook.INTERVALS == (1, 7, 30, 90, 180)


def test_next_due_first_appearance_is_tomorrow():
    assert phrasebook.next_due(1, D) == date(2026, 9, 2)


def test_next_due_climbs_through_the_table():
    assert phrasebook.next_due(2, D) == date(2026, 9, 8)     # +7
    assert phrasebook.next_due(3, D) == date(2026, 10, 1)    # +30
    assert phrasebook.next_due(4, D) == date(2026, 11, 30)   # +90


def test_next_due_caps_at_last_interval():
    """背過的東西還是會忘,只是慢一點 —— 封頂而不是停止出現。"""
    assert phrasebook.next_due(5, D) == date(2027, 2, 28)    # +180
    assert phrasebook.next_due(99, D) == date(2027, 2, 28)


def test_next_due_treats_zero_as_first():
    """防呆:Notion 的「出現次數」沒填時讀回來是 0。"""
    assert phrasebook.next_due(0, D) == date(2026, 9, 2)


# ── 挑句 ──────────────────────────────────────────────────

def _row(page_id, sentence, due=None, appeared=0):
    return {
        "page_id": page_id, "sentence": sentence,
        "meaning": "", "note": "", "appeared": appeared, "due": due,
    }


def test_pick_due_returns_none_when_nothing_is_due():
    rows = [_row("a", "hello", due="2026-09-05")]

    assert phrasebook.pick_due(rows, D) is None


def test_pick_due_returns_none_for_empty_library():
    assert phrasebook.pick_due([], D) is None


def test_pick_due_takes_the_most_overdue_first():
    """逾期最久的先還債。"""
    rows = [
        _row("a", "newer", due="2026-08-31"),
        _row("b", "older", due="2026-08-01"),
    ]

    assert phrasebook.pick_due(rows, D)["page_id"] == "b"


def test_pick_due_prefers_freshly_pasted_rows():
    """使用者剛貼進 Notion 的句子「下次出現」是空的,當天就該上場。

    不這樣做的話,貼完還得手動去填一個日期欄位 —— 那張表就變成家事。
    """
    rows = [
        _row("old", "overdue", due="2026-01-01"),
        _row("new", "just pasted", due=None),
    ]

    assert phrasebook.pick_due(rows, D)["page_id"] == "new"


def test_pick_due_includes_today():
    """due 正好是今天要算到期,不是明天。"""
    rows = [_row("a", "hello", due="2026-09-01")]

    assert phrasebook.pick_due(rows, D)["page_id"] == "a"


def test_pick_due_skips_future_rows_but_still_picks_overdue_ones():
    """真實的庫是混的:有到期的也有還沒到的。"""
    rows = [
        _row("future", "not yet", due="2026-12-25"),
        _row("overdue", "due now", due="2026-08-01"),
    ]

    assert phrasebook.pick_due(rows, D)["page_id"] == "overdue"


# ── 推進排程 ──────────────────────────────────────────────

def test_advance_increments_and_reschedules():
    row = _row("a", "hello", due="2026-08-01", appeared=2)

    out = phrasebook.advance(row, D)

    assert out == {
        "appeared": 3,
        "last_seen": D,
        "due": date(2026, 10, 1),      # 第 3 次 → +30
    }


def test_advance_handles_missing_count():
    """Notion 沒填「出現次數」時讀回來是 None。"""
    row = {"page_id": "a", "sentence": "hi", "appeared": None, "due": None}

    out = phrasebook.advance(row, D)

    assert out["appeared"] == 1
    assert out["due"] == date(2026, 9, 2)


def test_advance_casts_float_count_from_notion():
    """Notion 的 number 欄位可能回 3.0 而不是 3。

    float 拿去當 INTERVALS 的索引會 TypeError —— 而且只在真的接上
    Notion 之後才會炸,測試用 int 寫死是抓不到的。
    """
    row = {"page_id": "a", "sentence": "hi", "appeared": 3.0, "due": None}

    out = phrasebook.advance(row, D)

    assert out["appeared"] == 4
    assert out["due"] == date(2026, 11, 30)      # 第 4 次 → +90
