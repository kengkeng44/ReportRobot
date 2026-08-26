"""複委託月對帳單「股票庫存明細表」parser。

HANDOFF 4.1 的正解是拿月對帳單庫存欄位當持倉起點。2026-08-25 用
`/admin/statement-dump` 撈到真實格式後才動手寫（第 7 節：不要照 snippet 猜）。

**版面數字全部換成虛構值** —— repo 是公開的，真實持倉不能進 git。
換掉的只有數字，pdfplumber 攤平後的版面結構一字未動，那才是 parser 要對付的東西。

實測到的三種變形（都來自同一份對帳單）：
  1. 證券名稱留在資料行內      NASD AAPL APPLE INC 8 0 USD TWD USD ...
  2. 證券名稱被推到上一行      NASD GOOG 1 0 USD TWD USD ...
  3. 證券名稱拆成上下兩行      SPACE / EXPLOT 夾著 NASD SPCX 22 0 ...

所以不能靠「第幾個欄位」定位，要拿 `USD TWD USD` 當錨點回推股數。

另外：庫存表只有參考收盤價與參考市值，**沒有成本均價**。avg_cost 一律 None，
不拿收盤價充數 —— 那會讓未實現損益永遠是 0，是個看起來正常的假數字。
"""

import holdings


# ── 真實版面，虛構數字 ──────────────────────────────────
US_INVENTORY_TEXT = """股票庫存明細表（截至印表時間) Stock Inevntory (until printing time)
收盤日期
證券代 匯率日期
市場 證券名稱 保管銀行 庫存股數 圈存股數 參考收盤 參考市值(原幣) 參考市值(新台幣)
號 參考匯率
價格
Closing Date of
Stock Security Custodian Number of Number of Earmarked Date Exchange Reference Market Value Reference Market
Ticker
Exchange Name Bank Share(s) Held Share(s) Closing Exchange (original currency) Value (NTD)
Price Rate
115.07.31 115.07.31
CITIBANK
NASD AAPL APPLE INC 3 0 USD TWD USD 926.73 TWD 30,025
(HK)
308.9100 32.399000
股票庫存明細表（截至印表時間) Stock Inevntory (until printing time)
收盤日期
證券代 匯率日期
市場 證券名稱 保管銀行 庫存股數 圈存股數 參考收盤 參考市值(原幣) 參考市值(新台幣)
號 參考匯率
價格
Closing Date of
Stock Security Custodian Number of Number of Earmarked Date Exchange Reference Market Value Reference Market
Ticker
Exchange Name Bank Share(s) Held Share(s) Closing Exchange (original currency) Value (NTD)
Price Rate
115.07.31 115.07.31
ALPHABET CITIBANK
NASD GOOG 2 0 USD TWD USD 713.30 TWD 23,110
INC (HK)
356.6500 32.399000
115.07.31 115.07.31
SPACE CITIBANK
NASD SPCX 9 0 USD TWD USD 975.33 TWD 31,600
EXPLOT (HK)
108.3700 32.399000
訊息及說明:
海外債發行機構評等調整通知:
1. 標普於2026/7/2將必能寶公司債信評等由B+調降至B
"""


def test_parses_all_three_layout_variants():
    """三種版面變形都要抽得到，一種漏掉就是少算一整檔持倉。"""
    inv = holdings.parse_us_inventory(US_INVENTORY_TEXT)

    assert inv["AAPL"]["shares"] == 3   # 名稱在行內
    assert inv["GOOG"]["shares"] == 2   # 名稱在上一行
    assert inv["SPCX"]["shares"] == 9   # 名稱拆成上下兩行


def test_finds_exactly_the_three_positions():
    """不能多抓 —— 表頭重複出現、後面還有公告文字。"""
    inv = holdings.parse_us_inventory(US_INVENTORY_TEXT)

    assert set(inv) == {"AAPL", "GOOG", "SPCX"}


def test_avg_cost_is_none_not_closing_price():
    """庫存表沒有成本。拿收盤價充數會讓未實現損益永遠顯示 0。"""
    inv = holdings.parse_us_inventory(US_INVENTORY_TEXT)

    assert inv["AAPL"]["avg_cost"] is None


def test_earmarked_column_is_not_mistaken_for_shares():
    """庫存股數右邊那欄是圈存股數（實測都是 0）。抓錯欄位會讓持倉全歸零。"""
    inv = holdings.parse_us_inventory(US_INVENTORY_TEXT)

    assert all(v["shares"] > 0 for v in inv.values())


def test_no_inventory_section_returns_empty():
    """台股月對帳單根本沒有庫存表（實測），不能炸也不能亂猜。"""
    assert holdings.parse_us_inventory("親愛的客戶您好，這是您 6月份 的現貨對帳單") == {}


def test_empty_input_returns_empty():
    assert holdings.parse_us_inventory("") == {}
    assert holdings.parse_us_inventory(None) == {}


# ── 快照沒有成本時 build_portfolio 不能炸 ──────────────────
# parse_us_inventory 的 avg_cost 一律 None（庫存表沒有成本欄），
# 但 build_portfolio 原本寫死 shares * avg_cost —— 直接 TypeError。

def _snap(holdings_dict):
    return [{"market": "US", "period": (2026, 7), "holdings": holdings_dict}]


def test_snapshot_without_cost_does_not_crash():
    portfolio, _ = holdings.build_portfolio(
        [], _snap({"AAPL": {"shares": 3, "avg_cost": None}})
    )

    assert portfolio["AAPL"]["shares"] == 3


def test_unknown_cost_stays_unknown():
    """不能拿 0 充數 —— 均價 0 會讓損益率算成爆賺。"""
    portfolio, _ = holdings.build_portfolio(
        [], _snap({"AAPL": {"shares": 3, "avg_cost": None}})
    )

    assert portfolio["AAPL"]["avg_cost"] is None


def test_trades_after_snapshot_still_apply_without_cost():
    """快照沒成本，但快照日之後的買進有成本 —— 股數一定要對。"""
    trade = {"ticker": "AAPL", "action": "buy", "shares": 2,
             "price": 300.0, "date": (2026, 8, 5), "market": "US"}

    portfolio, _ = holdings.build_portfolio(
        [trade], _snap({"AAPL": {"shares": 3, "avg_cost": None}})
    )

    assert portfolio["AAPL"]["shares"] == 5
