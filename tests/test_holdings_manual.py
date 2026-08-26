"""台股手動持倉快照。

為什麼台股要手動：2026-08-25 用 `/admin/statement-dump` 撈富邦「有價證券月對帳單」
實測，**整份沒有庫存表** —— 只有交易明細、應收付、集保「異動」（本期空的）。
富邦自己在信裡寫「個人即時庫存餘額明細⋯⋯請登入網路交易系統或富邦e01查詢」。

所以 HANDOFF 4.1 寫的「從月對帳單庫存欄位初始化」對美股成立、對台股不成立。
台股改由使用者查一次 e01 填進環境變數當起點，之後靠成交自動累加。

**基準日是必填**：build_portfolio 會跳過基準日以前的成交（已含在庫存裡）。
基準日猜錯就會少算或雙重計算，而且結果是個有具體數字、沒人看得出錯的持倉。
所以缺基準日一律回 None 讓系統退回現有行為，不預設成今天。

持倉數字走環境變數（Infisical），不進 repo —— repo 是公開的。
"""

import holdings


def test_parses_shares_and_cost():
    snap = holdings.manual_snapshot("TW", "2317:1000@95.5", "2026-06-30")

    assert snap["market"] == "TW"
    assert snap["period"] == (2026, 6)
    assert snap["holdings"]["2317"] == {"shares": 1000, "avg_cost": 95.5}


def test_cost_is_optional():
    """只填股數也要能用 —— 成本不知道就是不知道，不要逼使用者編一個。"""
    snap = holdings.manual_snapshot("TW", "0050:500", "2026-06-30")

    assert snap["holdings"]["0050"] == {"shares": 500, "avg_cost": None}


def test_multiple_positions_with_loose_spacing():
    """使用者手打的東西一定會有空格。"""
    snap = holdings.manual_snapshot("TW", " 2317:1000@95.5 , 0050:500 ", "2026-06-30")

    assert set(snap["holdings"]) == {"2317", "0050"}


def test_alphanumeric_ticker_survives():
    """00632R 這種帶字母的台股代號不能被吃掉。"""
    snap = holdings.manual_snapshot("TW", "00632R:2000", "2026-06-30")

    assert snap["holdings"]["00632R"]["shares"] == 2000


def test_missing_asof_returns_none():
    """沒有基準日就無法決定哪些成交要跳過 —— 寧可沒有快照，不要算錯。"""
    assert holdings.manual_snapshot("TW", "2317:1000", None) is None
    assert holdings.manual_snapshot("TW", "2317:1000", "") is None


def test_malformed_asof_returns_none():
    assert holdings.manual_snapshot("TW", "2317:1000", "六月底") is None


def test_empty_spec_returns_none():
    """沒設定就是沒設定，回 None 讓 build_portfolio 走原本的成交累加。"""
    assert holdings.manual_snapshot("TW", "", "2026-06-30") is None
    assert holdings.manual_snapshot("TW", None, "2026-06-30") is None


def test_garbage_entries_are_skipped_not_fatal():
    """一筆打錯不該讓整份持倉消失，但也不能靜靜吞掉 —— 見 skipped。"""
    snap = holdings.manual_snapshot("TW", "2317:1000, 這是亂打的, 0050:500", "2026-06-30")

    assert set(snap["holdings"]) == {"2317", "0050"}
    assert snap["skipped"] == ["這是亂打的"]


def test_all_entries_garbage_returns_none():
    """全部都解析不出來時回 None，不要回一個空持倉假裝快照存在 ——
    空快照會讓 build_portfolio 以為「基準日時真的沒持股」而跳過之前的成交。"""
    assert holdings.manual_snapshot("TW", "亂打, 又亂打", "2026-06-30") is None


def test_zero_or_negative_shares_rejected():
    snap = holdings.manual_snapshot("TW", "2317:0, 0050:500", "2026-06-30")

    assert set(snap["holdings"]) == {"0050"}


def test_manual_snapshot_is_labelled_manual():
    """來源要說實話：台股沒有月對帳單庫存表，說成「月對帳單庫存」會害
    下一個人去查一份不存在的東西。"""
    snap = holdings.manual_snapshot("TW", "2317:1000", "2026-06-30")

    assert snap["origin"] == "manual"


def test_describe_sources_says_manual_not_statement():
    _, sources = holdings.build_portfolio(
        [], [holdings.manual_snapshot("TW", "2317:1000", "2026-06-30")]
    )

    text = holdings.describe_sources(sources)

    assert "手動" in text
    assert "月對帳單" not in text


# ── 手動基準日可能是月中,cutoff 不能無條件進位到月底 ──────────

def test_mid_month_asof_does_not_skip_later_trades():
    """基準日 8/26,8/28 的成交必須算進去。

    build_portfolio 原本用 month_end(period) 當 cutoff（月對帳單庫存本來
    就是月底狀態）,手動基準日沿用那套會把 8/27~8/31 的成交當成「已含在
    庫存裡」而跳過 —— 今天填今天看不出來,過幾天才開始悄悄漏算。
    """
    snap = holdings.manual_snapshot("TW", "2330:10", "2026-08-26")
    trade = {"ticker": "2330", "action": "buy", "shares": 5,
             "price": 1000.0, "date": (2026, 8, 28), "market": "TW"}

    portfolio, _ = holdings.build_portfolio([trade], [snap])

    assert portfolio["2330"]["shares"] == 15


def test_trade_on_asof_day_is_treated_as_already_included():
    """基準日當天的成交已經反映在那份庫存裡,再加一次就是雙重計算。"""
    snap = holdings.manual_snapshot("TW", "2330:10", "2026-08-26")
    trade = {"ticker": "2330", "action": "buy", "shares": 5,
             "price": 1000.0, "date": (2026, 8, 26), "market": "TW"}

    portfolio, _ = holdings.build_portfolio([trade], [snap])

    assert portfolio["2330"]["shares"] == 10


def test_statement_snapshot_still_uses_month_end():
    """月對帳單庫存沒有 cutoff 欄位,要維持原本的月底行為。"""
    snap = {"market": "US", "period": (2026, 7),
            "holdings": {"AAPL": {"shares": 3, "avg_cost": None}}}
    trade = {"ticker": "AAPL", "action": "buy", "shares": 2,
             "price": 300.0, "date": (2026, 7, 20), "market": "US"}

    portfolio, _ = holdings.build_portfolio([trade], [snap])

    assert portfolio["AAPL"]["shares"] == 3
