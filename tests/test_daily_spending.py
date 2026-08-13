"""每日推播的「最近一天消費」段落。

核心行為：國泰彙整信天生延遲一天，所以這裡取的是「資料裡最新的那一天」，
不是字面上的昨天，而且日期要照實寫出來。
"""

from datetime import date

import finance_report


def _txn(day, amount, shop="某店", direction="支出"):
    return {"date": day, "amount": amount, "shop": shop, "direction": direction}


def test_shows_latest_day_total_and_count():
    txns = [
        _txn("2026-08-12", 839, "全聯"),
        _txn("2026-08-12", 351, "統一超商"),
        _txn("2026-08-12", 100, "便利商店"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "8/12" in text
    assert "NT$1,290" in text
    assert "3 筆" in text
    assert "全聯" in text and "NT$839" in text


# ── 過濾規則 ──────────────────────────────────────────────

def test_returns_none_when_empty():
    assert finance_report.format_latest_day_spending([], date(2026, 8, 13)) is None


def test_returns_none_when_only_income():
    txns = [_txn("2026-08-12", 50000, "薪水", direction="收入")]

    assert finance_report.format_latest_day_spending(txns, date(2026, 8, 13)) is None


def test_ignores_future_dates():
    """資料髒掉時，未來日期不該主導『最新一天』。"""
    txns = [
        _txn("2026-08-12", 100, "正常"),
        _txn("2026-09-30", 999, "壞資料"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "8/12" in text
    assert "壞資料" not in text


def test_ignores_rows_without_valid_date():
    txns = [
        _txn("2026-08-12", 100, "正常"),
        _txn("", 999, "沒日期"),
        _txn("not-a-date", 999, "爛日期"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "正常" in text
    assert "沒日期" not in text and "爛日期" not in text


def test_only_latest_day_is_shown():
    txns = [
        _txn("2026-08-12", 100, "今天的"),
        _txn("2026-08-11", 999, "昨天的"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "今天的" in text
    assert "昨天的" not in text
    assert "1 筆" in text


def test_missing_direction_counts_as_spending():
    """direction 缺值時視為支出 —— 沿用既有 _is_spending 的約定。"""
    txns = [{"date": "2026-08-12", "amount": 100, "shop": "無方向"}]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "無方向" in text


def test_none_amount_counts_as_a_row_but_zero():
    """金額沒解析出來的仍是一筆消費，但不能讓總額變成 None 而炸掉。"""
    txns = [
        _txn("2026-08-12", 100, "有金額"),
        _txn("2026-08-12", None, "沒金額"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "2 筆" in text
    assert "NT$100" in text
    assert "沒金額" in text
    assert "NT$-" in text, "金額不明要顯示 -，不是 0，才看得出是缺資料"
