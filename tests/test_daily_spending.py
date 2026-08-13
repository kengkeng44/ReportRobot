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
