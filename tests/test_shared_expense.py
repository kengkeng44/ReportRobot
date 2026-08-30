"""共同消費分攤 —— 均分、解析、去重鍵、報表呈現。

「金額」欄的語意是「我實際負擔多少」，這一點不能被這次改動動搖：
六處既有報表都讀那一欄，語意一變就會靜靜地高估支出。
"""

import finance_report as fr


# ── 分攤計算 ─────────────────────────────────────────────

def test_my_share_halves_even_amounts():
    assert fr.my_share_of(600) == 300


def test_my_share_rounds_half_up_not_bankers():
    """內建 round() 在這兩個 .5 會給 302 與 304 —— 同樣是 .5 卻一個往下
    一個往上。共同消費除以 2 大量產生 .5，忽上忽下的話對帳查不出規律。"""
    assert fr.my_share_of(605) == 303      # 302.5 → 往上
    assert fr.my_share_of(607) == 304      # 303.5 → 往上


def test_my_share_of_zero_and_none():
    assert fr.my_share_of(0) == 0
    assert fr.my_share_of(None) == 0


def test_my_share_ratio_is_a_named_constant():
    """0.5 是政策決定不是數學。散落的 / 2 讀起來像數學，改的時候會漏。"""
    assert fr.MY_SHARE == 0.5


# ── 解析三段輸入 ─────────────────────────────────────────

def test_parse_reads_trailing_split_type():
    got = fr.parse_manual("晚餐 600 共同")

    assert got["split_type"] == "共同"
    assert got["total"] == 600
    assert got["amount"] == 300          # 金額欄 = 我實際負擔
    assert got["shop"] == "晚餐"


def test_parse_personal_keeps_full_amount():
    got = fr.parse_manual("午餐 120 個人")

    assert got["split_type"] == "個人"
    assert got["total"] == 120
    assert got["amount"] == 120


def test_parse_without_split_type_leaves_it_none():
    """第三段還沒選。呼叫端要靠這個 None 決定跳個人/共同按鈕。"""
    got = fr.parse_manual("晚餐 600")

    assert got["split_type"] is None
    assert got["amount"] == 600
    assert got["total"] == 600


def test_split_keyword_only_matches_at_the_end():
    """「共同基金 3000」的共同是商店名的一部分，不是分攤類型。"""
    got = fr.parse_manual("共同基金 3000")

    assert got["split_type"] is None
    assert got["shop"] == "共同基金"


def test_income_never_asks_about_splitting():
    """薪水不用跟人分。留成 None 會讓收入也跳出個人/共同那一段。"""
    got = fr.parse_manual("薪水 50000")

    assert got["direction"] == "收入"
    assert got["split_type"] == "個人"
    assert got["amount"] == 50000


def test_parse_still_returns_none_without_amount():
    """既有行為：沒金額不猜。記一筆金額錯的帳比沒記更難發現。"""
    assert fr.parse_manual("午餐") is None
    assert fr.parse_manual("") is None
    assert fr.parse_manual("晚餐 共同") is None


# ── 去重鍵 ───────────────────────────────────────────────

def test_personal_fingerprint_format_unchanged():
    """既有資料的比對基準不能動 —— 個人維持四段格式。"""
    assert (fr.make_manual_fingerprint("2026-08-30", 120, "午餐")
            == fr.make_manual_fingerprint("2026-08-30", 120, "午餐", "個人"))


def test_shared_and_personal_with_same_share_do_not_collide():
    """個人 300 與共同 600（分攤 300）的「金額」欄都是 300。
    不加區別就會產生相同 fingerprint，其中一筆會被當成重複。"""
    personal = fr.parse_manual("晚餐 300 個人")
    shared = fr.parse_manual("晚餐 600 共同")

    assert personal["amount"] == shared["amount"] == 300
    assert personal["fingerprint"] != shared["fingerprint"]


# ── 報表 ─────────────────────────────────────────────────

def _row(date_, amount, split_type="個人", total=None, category="餐飲"):
    return {"date": date_, "amount": amount, "category": category,
            "shop": "某店", "direction": "支出", "status": "已結帳",
            "currency": "TWD", "split_type": split_type,
            "total": total if total is not None else amount}


def test_monthly_spending_shows_shared_line():
    txns = [_row("2026-08-01", 300, "共同", 600),
            _row("2026-08-02", 250, "共同", 500),
            _row("2026-08-03", 120)]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "670" in text                      # 總額仍是我實際負擔
    assert "共同分攤" in text
    assert "550" in text                      # 我在共同消費裡負擔的
    assert "1,100" in text                    # 整桌加起來


def test_monthly_spending_hides_shared_line_when_none():
    """常態是零的欄位每個月都佔一行，會讓人不再讀它。"""
    txns = [_row("2026-08-01", 120), _row("2026-08-02", 80)]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "共同分攤" not in text


def test_monthly_total_still_counts_my_share_only():
    """金額欄的語意是「我實際負擔」—— 這次改動不能讓總額變成整桌。"""
    txns = [_row("2026-08-01", 300, "共同", 600)]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "NT$300" in text
