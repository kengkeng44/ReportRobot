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
