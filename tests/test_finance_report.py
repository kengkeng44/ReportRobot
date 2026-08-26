"""財務分頁那五個按鈕的內容產生。

原則：沒有資料時要講清楚「為什麼沒有」與「怎麼開始」，
不能回一句「無資料」讓人不知道是壞了還是本來就空的。
"""

import pytest

import finance_report as fr


def _txn(date, amount, category="餐飲", shop="某店", direction="支出", status="授權中"):
    return {"date": date, "amount": amount, "category": category,
            "shop": shop, "direction": direction, "status": status}


# ── 本月支出 ──────────────────────────────────────────────

def test_monthly_total_and_breakdown():
    txns = [
        _txn("2026-08-01", 100, "餐飲"),
        _txn("2026-08-02", 250, "餐飲"),
        _txn("2026-08-03", 80, "超市∕量販"),
    ]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "430" in text            # 總額
    assert "餐飲" in text and "350" in text
    assert "超市∕量販" in text


def test_monthly_excludes_other_months():
    txns = [_txn("2026-08-01", 100), _txn("2026-07-31", 999)]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "999" not in text


def test_monthly_excludes_income_and_repayment():
    """收入與還款不算支出，混進去會讓數字沒有意義。"""
    txns = [
        _txn("2026-08-01", 100, direction="支出"),
        _txn("2026-08-02", 50000, direction="收入"),
        _txn("2026-08-03", 11072, direction="還款"),
    ]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "50000" not in text and "11072" not in text


def test_monthly_categories_sorted_by_amount():
    txns = [
        _txn("2026-08-01", 50, "餐飲"),
        _txn("2026-08-02", 900, "旅遊"),
    ]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert text.index("旅遊") < text.index("餐飲"), "花最多的要排前面"


def test_monthly_empty_explains_how_to_start():
    text = fr.format_monthly_spending([], "2026-08")
    assert "還沒有" in text


# ── 幣別與海外消費 ────────────────────────────────────────

def test_monthly_keeps_foreign_currency_out_of_twd_total():
    """US$30 加進台幣總計會得到一個沒有意義的數字，而且畫面上看不出來。"""
    txns = [
        {**_txn("2026-08-01", 100), "currency": "TWD"},
        {**_txn("2026-08-02", 30), "currency": "USD"},
    ]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "NT$100" in text
    assert "130" not in text, "台幣總計不可以把美元加進來"
    assert "USD" in text, "外幣要另外列，不能默默消失"


def test_monthly_treats_missing_currency_as_twd():
    """遷移前的資料沒有幣別欄，當時只有國泰一個來源，一律台幣。"""
    txns = [_txn("2026-08-01", 100), _txn("2026-08-02", 250)]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "350" in text


def test_monthly_flags_overseas_amounts_as_provisional():
    """海外消費的台幣金額是授權當下的估算，結匯後會變。"""
    txns = [
        {**_txn("2026-08-01", 100), "region": "TW"},
        {**_txn("2026-08-02", 14, shop="Amazon web services"), "region": "US"},
    ]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "海外" in text
    assert "變動" in text


def test_monthly_no_overseas_note_when_all_domestic():
    txns = [{**_txn("2026-08-01", 100), "region": "TW"}]
    assert "海外" not in fr.format_monthly_spending(txns, "2026-08")


def test_recent_marks_overseas_transaction():
    txns = [{**_txn("2026-08-08", 14, shop="Amazon web services"), "region": "US"}]

    text = fr.format_recent(txns)

    assert "US" in text


def test_recent_uses_currency_unit():
    txns = [{**_txn("2026-08-08", 30, shop="OpenAI"), "currency": "USD"}]

    text = fr.format_recent(txns)

    assert "USD" in text
    assert "NT$30" not in text, "美元不能標成台幣"


# ── 最近交易 ──────────────────────────────────────────────

def test_recent_is_newest_first_and_limited():
    txns = [_txn(f"2026-08-{d:02d}", d) for d in range(1, 16)]

    text = fr.format_recent(txns, limit=5)

    lines = [l for l in text.splitlines() if l.startswith("・")]
    assert len(lines) == 5
    assert "08-15" in lines[0]


def test_recent_marks_pending_authorization():
    """授權中代表金額還可能變（外幣結匯、退款），要標出來。"""
    txns = [_txn("2026-08-10", 100, status="授權中")]

    text = fr.format_recent(txns)

    assert "授權" in text


def test_recent_shows_shop_or_category_when_shop_empty():
    txns = [_txn("2026-08-10", 100, category="旅遊", shop="")]

    text = fr.format_recent(txns)

    assert "旅遊" in text


def test_recent_empty():
    assert "還沒有" in fr.format_recent([])


# ── 卡費 ──────────────────────────────────────────────────

def test_card_bill_shows_due_and_amount():
    bills = [{"period": "2026-07", "due": "2026-08-09",
              "amount": 11072, "minimum": 1287, "status": "自動扣繳"}]

    text = fr.format_card_bills(bills)

    # 金額帶千分位比較好讀
    assert "11,072" in text and "2026-08-09" in text
    assert "1,287" in text


def test_card_bill_empty_explains_source():
    """帳單解析器還沒做，要說清楚而不是只回無資料。"""
    text = fr.format_card_bills([])
    assert "還沒有" in text


# ── 淨值 ──────────────────────────────────────────────────

def test_net_worth_shows_latest_snapshot():
    snaps = [
        {"date": "2026-08-10", "cash": 100, "stock": 200, "card_due": 50, "net": 250},
        {"date": "2026-08-12", "cash": 120, "stock": 260, "card_due": 40, "net": 340},
    ]

    text = fr.format_net_worth(snaps)

    assert "340" in text and "2026-08-12" in text


def test_net_worth_shows_change_from_previous():
    snaps = [
        {"date": "2026-08-10", "cash": 0, "stock": 0, "card_due": 0, "net": 250},
        {"date": "2026-08-12", "cash": 0, "stock": 0, "card_due": 0, "net": 340},
    ]

    text = fr.format_net_worth(snaps)

    assert "90" in text


def test_net_worth_empty():
    assert "還沒有" in fr.format_net_worth([])


# ── 手動記一筆 ────────────────────────────────────────────

@pytest.mark.parametrize("text,shop,amount", [
    ("午餐 120", "午餐", 120),
    ("120 午餐", "午餐", 120),
    ("計程車 350", "計程車", 350),
    ("咖啡 85元", "咖啡", 85),
    ("薪水 50000", "薪水", 50000),
])
def test_parse_manual_entry(text, shop, amount):
    got = fr.parse_manual(text)
    assert got is not None
    assert got["shop"] == shop
    assert got["amount"] == amount


def test_manual_entry_needs_an_amount():
    """沒有金額就不能記 —— 猜一個數字進去比不記更糟。"""
    assert fr.parse_manual("午餐") is None
    assert fr.parse_manual("") is None


def test_manual_entry_defaults_to_expense():
    got = fr.parse_manual("午餐 120")
    assert got["direction"] == "支出"
    assert got["source"] == "手動"


def test_manual_income_detected():
    """薪水是收入，記成支出會讓月結完全錯。"""
    got = fr.parse_manual("薪水 50000")
    assert got["direction"] == "收入"


def test_manual_entry_has_fingerprint():
    got = fr.parse_manual("午餐 120")
    assert got.get("fingerprint")


def test_manual_entries_same_day_same_amount_differ_by_shop():
    a = fr.parse_manual("午餐 120")
    b = fr.parse_manual("晚餐 120")
    assert a["fingerprint"] != b["fingerprint"]


# ── 當月逐筆明細（2026-08-26，每日信件用）──────────────────
# 使用者要「一整個月的花銷都列出來」。既有的 format_monthly_spending
# 只給分類統計，看不到單筆花在哪。

def _mtxn(date, shop, amount, category="餐飲", currency="TWD", direction="支出"):
    """包既有的 _txn（它帶著 _is_spending 需要的 direction / status），
    只補上這組測試要驗的 currency —— 不要另外造一份會漂移的假資料。

    排除收入 / 還款靠的是 direction 欄位，不是 category。"""
    t = _txn(date, amount, category=category, shop=shop, direction=direction)
    t["currency"] = currency
    return t


def test_monthly_detail_lists_every_transaction():
    text = fr.format_monthly_detail([
        _mtxn("2026-08-01", "全家", 85),
        _mtxn("2026-08-15", "星巴克", 160),
    ], "2026-08")

    assert "全家" in text
    assert "星巴克" in text


def test_monthly_detail_groups_by_day_newest_first():
    """最近的花費先看到 —— 信件是每天早上看的，舊的往下捲。"""
    text = fr.format_monthly_detail([
        _mtxn("2026-08-01", "舊的", 100),
        _mtxn("2026-08-20", "新的", 200),
    ], "2026-08")

    assert text.index("新的") < text.index("舊的")


def test_monthly_detail_has_month_total():
    text = fr.format_monthly_detail([
        _mtxn("2026-08-01", "全家", 85),
        _mtxn("2026-08-15", "星巴克", 160),
    ], "2026-08")

    assert "245" in text


def test_monthly_detail_excludes_other_months():
    text = fr.format_monthly_detail([
        _mtxn("2026-07-31", "上個月", 999),
        _mtxn("2026-08-01", "這個月", 85),
    ], "2026-08")

    assert "上個月" not in text
    assert "這個月" in text


def test_monthly_detail_excludes_income_and_repayment():
    """只列花銷。把還款算成支出會讓月總額變兩倍。"""
    rows = [_mtxn("2026-08-01", "全家", 85),
            _mtxn("2026-08-02", "信用卡還款", 5000, direction="還款")]

    text = fr.format_monthly_detail(rows, "2026-08")

    assert "全家" in text
    assert "信用卡還款" not in text


def test_monthly_detail_empty_month():
    assert fr.format_monthly_detail([], "2026-08") == fr._EMPTY_MONTH


def test_monthly_detail_shows_daily_subtotal():
    text = fr.format_monthly_detail([
        _mtxn("2026-08-01", "全家", 85),
        _mtxn("2026-08-01", "星巴克", 160),
    ], "2026-08")

    assert "245" in text


def test_monthly_detail_keeps_foreign_currency_separate():
    """外幣不能跟台幣相加 —— 加起來是個沒有意義的數字。"""
    text = fr.format_monthly_detail([
        _mtxn("2026-08-01", "全家", 85),
        _mtxn("2026-08-02", "Netflix", 15, currency="USD"),
    ], "2026-08")

    assert "USD" in text
