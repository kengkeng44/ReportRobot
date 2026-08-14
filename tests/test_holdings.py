"""持倉初始化純邏輯。

實際事故（HANDOFF 4.1）：`get_portfolio_from_gmail()` 是從抓取範圍內的信件
**重建**持倉，不是讀庫存快照。買很久、之後就沒再交易的部位（例如台積電）
會整個消失 —— 淨值快照少算，Notion 持倉表留下孤兒資料列。

正確做法：拿最近一期月對帳單的庫存欄位當起點，只套用快照日**之後**的成交。

主旨樣本全部取自真實信件（富邦證券寄件人 admin@bhu.fbs.com.tw），
不是照 snippet 猜的 —— 猜格式的下場見 HANDOFF 第 7 節。
"""

import holdings


# ── 真實主旨樣本 ───────────────────────────────────────────
TW_MONTHLY = "【富邦證券】有價證券月對帳單-2026年6月"
US_MONTHLY = "富邦證券2026~7複委託月對帳單"
US_DAILY = "富邦證券2026/7/7複委託日對帳單"
TW_DAILY = "富邦證券2026年6月29日證券成交回報"
TW_EMERGING_DAILY = "富邦證券2026年5月5日興櫃成交回報"

REAL_SUBJECTS = [
    US_MONTHLY,
    US_DAILY,
    "富邦證券2026~6複委託月對帳單",
    "【富邦證券】有價證券月對帳單-2026年6月",
    TW_DAILY,
    "【富邦證券】有價證券月對帳單-2026年5月",
    TW_EMERGING_DAILY,
    "富邦證券2026~4複委託月對帳單",
]


def _trade(ticker, action, shares, price, date, market=None):
    t = {
        "ticker": ticker,
        "action": action,
        "shares": shares,
        "price": price,
        "date": date,
    }
    if market:
        t["market"] = market
    return t


# ══════════════════════════════════════════════════════════
# 主旨判讀
# ══════════════════════════════════════════════════════════

def test_monthly_period_from_tw_statement():
    assert holdings.monthly_statement_period(TW_MONTHLY) == (2026, 6)


def test_monthly_period_from_us_statement():
    """複委託月對帳單的年月是 `2026~7` 這種寫法。"""
    assert holdings.monthly_statement_period(US_MONTHLY) == (2026, 7)


def test_daily_reports_are_not_monthly_statements():
    """成交回報主旨也含「年」「月」，不能被當成月對帳單 —— 否則會拿
    當日成交當庫存快照，整份持倉直接錯掉。"""
    assert holdings.monthly_statement_period(TW_DAILY) is None
    assert holdings.monthly_statement_period(TW_EMERGING_DAILY) is None
    assert holdings.monthly_statement_period(US_DAILY) is None
    assert holdings.monthly_statement_period("") is None
    assert holdings.monthly_statement_period(None) is None


def test_market_from_subject():
    assert holdings.statement_market(US_MONTHLY) == "US"
    assert holdings.statement_market(US_DAILY) == "US"
    assert holdings.statement_market(TW_MONTHLY) == "TW"
    assert holdings.statement_market(TW_DAILY) == "TW"
    assert holdings.statement_market(TW_EMERGING_DAILY) == "TW"


def test_pick_latest_monthly_per_market():
    """兩個市場的月對帳單期別可能不同步（實測 2026-08：複委託到 7 月、
    有價證券只到 6 月），所以要各挑各的。"""
    latest = holdings.pick_latest_monthly(REAL_SUBJECTS)

    assert latest["TW"] == (2026, 6)
    assert latest["US"] == (2026, 7)


def test_pick_latest_monthly_ignores_daily_only_inbox():
    assert holdings.pick_latest_monthly([TW_DAILY, US_DAILY]) == {}


# ══════════════════════════════════════════════════════════
# 月底 cutoff
# ══════════════════════════════════════════════════════════

def test_month_end():
    assert holdings.month_end(2026, 6) == (2026, 6, 30)
    assert holdings.month_end(2026, 12) == (2026, 12, 31)
    assert holdings.month_end(2026, 2) == (2026, 2, 28)
    assert holdings.month_end(2024, 2) == (2024, 2, 29)


# ══════════════════════════════════════════════════════════
# 沒有快照：維持舊行為，但要講清楚為什麼可能不完整
# ══════════════════════════════════════════════════════════

def test_without_snapshot_falls_back_to_trade_accumulation():
    trades = [
        _trade("2330", "buy", 5, 2245.0, (2026, 4, 28)),
        _trade("2317", "buy", 50, 230.0, (2026, 5, 5)),
        _trade("2317", "sell", 50, 250.5, (2026, 6, 29)),
    ]

    portfolio, sources = holdings.build_portfolio(trades)

    assert portfolio == {"2330": {"shares": 5, "avg_cost": 2245.0}}
    assert sources["TW"]["source"] == "trades_only"
    assert sources["TW"]["reason"]


def test_describe_sources_explains_missing_snapshot():
    """原則 3：沒資料時要說明「為什麼沒有」，不能只說無資料。"""
    _, sources = holdings.build_portfolio(
        [_trade("AAPL", "buy", 6, 200.0, (2026, 3, 11))]
    )

    text = holdings.describe_sources(sources)
    assert "庫存" in text or "快照" in text
    assert "US" in text


# ══════════════════════════════════════════════════════════
# 有快照：核心修正
# ══════════════════════════════════════════════════════════

def _tw_snapshot(holdings_dict, period=(2026, 6)):
    return {"market": "TW", "period": period, "holdings": holdings_dict}


def test_snapshot_position_survives_without_any_trade():
    """這就是台積電消失的那個 bug：快照裡有、抓取範圍內沒交易，
    以前會整檔不見。"""
    snap = _tw_snapshot({"2330": {"shares": 5, "avg_cost": 2245.0}})

    portfolio, sources = holdings.build_portfolio([], snapshots=[snap])

    assert portfolio == {"2330": {"shares": 5, "avg_cost": 2245.0}}
    assert sources["TW"]["source"] == "snapshot"
    assert sources["TW"]["period"] == (2026, 6)


def test_trades_before_cutoff_are_not_double_counted():
    """快照日以前的成交已經反映在庫存裡，再加一次就是雙重計算。"""
    snap = _tw_snapshot({"2330": {"shares": 5, "avg_cost": 2245.0}})
    trades = [
        _trade("2330", "buy", 5, 2245.0, (2026, 4, 28), market="TW"),
        _trade("2330", "buy", 5, 2260.0, (2026, 6, 30), market="TW"),  # 邊界：當天
    ]

    portfolio, sources = holdings.build_portfolio(trades, snapshots=[snap])

    assert portfolio["2330"]["shares"] == 5
    assert sources["TW"]["trades_applied"] == 0
    assert sources["TW"]["trades_before_cutoff"] == 2


def test_trades_after_cutoff_are_applied():
    snap = _tw_snapshot({"2330": {"shares": 5, "avg_cost": 2000.0}})
    trades = [_trade("2330", "buy", 5, 2400.0, (2026, 7, 1), market="TW")]

    portfolio, sources = holdings.build_portfolio(trades, snapshots=[snap])

    assert portfolio["2330"]["shares"] == 10
    assert portfolio["2330"]["avg_cost"] == 2200.0
    assert sources["TW"]["trades_applied"] == 1


def test_position_sold_out_after_cutoff_disappears():
    snap = _tw_snapshot({"2317": {"shares": 50, "avg_cost": 230.0}})
    trades = [_trade("2317", "sell", 50, 250.5, (2026, 7, 2), market="TW")]

    portfolio, _ = holdings.build_portfolio(trades, snapshots=[snap])

    assert "2317" not in portfolio


def test_new_position_bought_after_cutoff_appears():
    snap = _tw_snapshot({"2330": {"shares": 5, "avg_cost": 2245.0}})
    trades = [_trade("00632R", "buy", 1000, 10.5, (2026, 7, 3), market="TW")]

    portfolio, _ = holdings.build_portfolio(trades, snapshots=[snap])

    assert portfolio["00632R"]["shares"] == 1000
    assert portfolio["2330"]["shares"] == 5


def test_dateless_trade_is_skipped_not_guessed():
    """原則 2：分不出在快照前還是後就跳過那一筆，不猜。
    但要記在 sources 裡，才看得出來為什麼數字對不上。"""
    snap = _tw_snapshot({"2330": {"shares": 5, "avg_cost": 2245.0}})
    trades = [_trade("2330", "buy", 5, 2400.0, None, market="TW")]

    portfolio, sources = holdings.build_portfolio(trades, snapshots=[snap])

    assert portfolio["2330"]["shares"] == 5
    assert sources["TW"]["trades_undated"] == 1


def test_two_markets_use_their_own_cutoff():
    """複委託 7 月、有價證券 6 月時，7/1 的台股成交要算，
    7/1 的美股成交已含在複委託快照裡不能再算。"""
    snaps = [
        {"market": "TW", "period": (2026, 6),
         "holdings": {"2330": {"shares": 5, "avg_cost": 2245.0}}},
        {"market": "US", "period": (2026, 7),
         "holdings": {"AAPL": {"shares": 6, "avg_cost": 250.0}}},
    ]
    trades = [
        _trade("2330", "buy", 5, 2400.0, (2026, 7, 1), market="TW"),
        _trade("AAPL", "buy", 2, 260.0, (2026, 7, 1), market="US"),
    ]

    portfolio, sources = holdings.build_portfolio(trades, snapshots=snaps)

    assert portfolio["2330"]["shares"] == 10
    assert portfolio["AAPL"]["shares"] == 6
    assert sources["TW"]["trades_applied"] == 1
    assert sources["US"]["trades_applied"] == 0


def test_market_falls_back_to_ticker_shape_when_untagged():
    """舊 caller 不會標 market，用代號形狀補（4-6 位數字 = 台股）。"""
    snaps = [{"market": "US", "period": (2026, 7),
              "holdings": {"AAPL": {"shares": 6, "avg_cost": 250.0}}}]
    trades = [_trade("AAPL", "buy", 2, 260.0, (2026, 8, 1))]

    portfolio, _ = holdings.build_portfolio(trades, snapshots=snaps)

    assert portfolio["AAPL"]["shares"] == 8


def test_market_without_snapshot_still_rebuilt_from_trades():
    """只有一邊有快照時，另一邊不能整個消失。"""
    snaps = [{"market": "TW", "period": (2026, 6),
              "holdings": {"2330": {"shares": 5, "avg_cost": 2245.0}}}]
    trades = [_trade("AAPL", "buy", 6, 250.0, (2026, 3, 11), market="US")]

    portfolio, sources = holdings.build_portfolio(trades, snapshots=snaps)

    assert portfolio["AAPL"]["shares"] == 6
    assert sources["TW"]["source"] == "snapshot"
    assert sources["US"]["source"] == "trades_only"


def test_snapshot_without_holdings_is_not_treated_as_empty_portfolio():
    """庫存解析回空的（可能是 parser 壞了）不能當成「真的沒持倉」而蓋掉
    成交重建的結果 —— 那會讓淨值直接歸零。"""
    snaps = [{"market": "TW", "period": (2026, 6), "holdings": {}}]
    trades = [_trade("2330", "buy", 5, 2245.0, (2026, 4, 28), market="TW")]

    portfolio, sources = holdings.build_portfolio(trades, snapshots=snaps)

    assert portfolio["2330"]["shares"] == 5
    assert sources["TW"]["source"] == "trades_only"
    assert "庫存" in sources["TW"]["reason"]


def test_describe_sources_mentions_snapshot_period():
    snaps = [{"market": "TW", "period": (2026, 6),
              "holdings": {"2330": {"shares": 5, "avg_cost": 2245.0}}}]

    _, sources = holdings.build_portfolio([], snapshots=snaps)

    assert "2026/06" in holdings.describe_sources(sources)


# ══════════════════════════════════════════════════════════
# 與既有累計邏輯的相容性
# ══════════════════════════════════════════════════════════

def test_aggregate_trades_matches_legacy_gmail_reader():
    """gmail_reader._aggregate_portfolio 被 server.py 的 debug 端點直接 import，
    行為不能變。"""
    import gmail_reader as gr

    trades = [
        _trade("2317", "buy", 1000, 200.0, (2026, 1, 5)),
        _trade("2317", "buy", 1000, 240.0, (2026, 2, 5)),
        _trade("2317", "sell", 500, 250.0, (2026, 3, 5)),
        _trade("SPCX", "buy", 44, 30.0, None),
    ]

    assert holdings.aggregate_trades(trades) == gr._aggregate_portfolio(trades)


def test_selling_more_than_held_does_not_go_negative():
    trades = [
        _trade("2317", "buy", 1000, 200.0, (2026, 1, 5)),
        _trade("2317", "sell", 2000, 250.0, (2026, 3, 5)),
    ]

    assert holdings.aggregate_trades(trades) == {}
