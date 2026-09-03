"""本月消費的類別彙總與圓餅圖。

刻意跟 finance_report 分檔:那個模組被 command_router 每次處理 LINE
指令都 import,不該從此拖著 matplotlib(見計畫的檔案結構那節)。

這份測試不驗 PNG 像素 —— 驗圖片內容是脆的,而且真正會出錯的是彙總。
"""

import spending_chart


def _txn(day, amount, category, currency="TWD", direction="支出"):
    return {"date": day, "amount": amount, "category": category,
            "currency": currency, "direction": direction}


def test_summarize_groups_by_category():
    rows = [
        _txn("2026-09-01", 100, "餐飲"),
        _txn("2026-09-02", 50, "餐飲"),
        _txn("2026-09-02", 300, "超市∕量販"),
    ]

    assert spending_chart.summarize(rows, "2026-09") == [
        ("超市∕量販", 300), ("餐飲", 150),
    ]


def test_summarize_sorts_by_amount_desc():
    rows = [_txn("2026-09-01", 10, "餐飲"), _txn("2026-09-01", 900, "旅遊")]

    assert [c for c, _ in spending_chart.summarize(rows, "2026-09")] == ["旅遊", "餐飲"]


def test_summarize_ignores_other_months():
    rows = [_txn("2026-08-31", 999, "餐飲"), _txn("2026-09-01", 100, "餐飲")]

    assert spending_chart.summarize(rows, "2026-09") == [("餐飲", 100)]


def test_summarize_ignores_income():
    """收入混進支出圓餅圖會讓每一片的百分比都錯。"""
    rows = [
        _txn("2026-09-01", 100, "餐飲"),
        _txn("2026-09-02", 50000, "其他", direction="收入"),
    ]

    assert spending_chart.summarize(rows, "2026-09") == [("餐飲", 100)]


def test_summarize_ignores_foreign_currency():
    """把 US$15 加進台幣會得到一個沒有意義、而且看不出哪裡怪的數字。"""
    rows = [
        _txn("2026-09-01", 100, "餐飲"),
        _txn("2026-09-02", 15, "旅遊", currency="USD"),
    ]

    assert spending_chart.summarize(rows, "2026-09") == [("餐飲", 100)]


def test_summarize_fills_missing_category():
    """國泰偶爾送沒有類別的筆數 —— 落到「其他」而不是消失。"""
    rows = [_txn("2026-09-01", 100, None)]

    assert spending_chart.summarize(rows, "2026-09") == [("其他", 100)]


def test_summarize_collapses_tail_into_other():
    """14 片的圓餅圖是色票不是圖表 —— 前 6 大 + 其他。"""
    rows = [_txn("2026-09-01", (10 - i) * 100, f"類別{i}") for i in range(9)]

    out = spending_chart.summarize(rows, "2026-09", top_n=6)

    assert len(out) == 7
    assert out[-1][0] == "其他"
    # 第 7-9 名:200 + 300 + 400（i=8,7,6 → 200,300,400）
    assert out[-1][1] == 900


def test_other_is_always_last_even_when_large():
    """「其他」是分類殘渣,排序上不跟真類別競爭 —— 永遠壓在最後一片。"""
    rows = [
        _txn("2026-09-01", 9999, "其他"),
        _txn("2026-09-01", 100, "餐飲"),
    ]

    out = spending_chart.summarize(rows, "2026-09")

    assert out[-1][0] == "其他"


def test_summarize_returns_empty_without_spending():
    assert spending_chart.summarize([], "2026-09") == []
    assert spending_chart.summarize([_txn("2026-08-01", 100, "餐飲")], "2026-09") == []
