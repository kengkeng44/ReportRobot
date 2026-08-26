"""把庫存快照接進 get_portfolio_from_gmail。

在此之前 `holdings.build_portfolio` 是孤兒程式碼 —— 22 個測試全綠，但沒有任何
生產路徑會執行到它。三個消費端（持倉查詢 / 淨值 / 每天 15:30 的 Notion 同步）
全部還是走 `_aggregate_portfolio`，也就是 HANDOFF 4.1 那條有 bug 的舊路徑。

快照來源兩邊不一樣（2026-08-25 /admin/statement-dump 實測）：
  美股 → 複委託月對帳單有「股票庫存明細表」，直接 parse
  台股 → 月對帳單**沒有**庫存表，只能靠環境變數手動給
"""

import gmail_reader as gr


US_INVENTORY = """股票庫存明細表（截至印表時間) Stock Inevntory (until printing time)
NASD AAPL APPLE INC 3 0 USD TWD USD 926.73 TWD 30,025
"""

US_INVENTORY_NEWER = """股票庫存明細表（截至印表時間) Stock Inevntory (until printing time)
NASD AAPL APPLE INC 7 0 USD TWD USD 2,162.37 TWD 70,059
"""

US_MONTHLY = "富邦證券2026~6複委託月對帳單"
US_MONTHLY_NEWER = "富邦證券2026~7複委託月對帳單"
US_DAILY = "富邦證券2026/7/7複委託日對帳單"
TW_MONTHLY = "【富邦證券】有價證券月對帳單-2026年6月"


def test_us_monthly_statement_becomes_snapshot():
    snaps = gr._build_snapshots([(US_MONTHLY, US_INVENTORY)], {})

    assert len(snaps) == 1
    assert snaps[0]["market"] == "US"
    assert snaps[0]["period"] == (2026, 6)
    assert snaps[0]["holdings"]["AAPL"]["shares"] == 3


def test_only_latest_us_period_is_used():
    """舊的一期庫存已經過時，混用會讓 cutoff 算錯。"""
    snaps = gr._build_snapshots([
        (US_MONTHLY, US_INVENTORY),
        (US_MONTHLY_NEWER, US_INVENTORY_NEWER),
    ], {})

    assert len(snaps) == 1
    assert snaps[0]["period"] == (2026, 7)
    assert snaps[0]["holdings"]["AAPL"]["shares"] == 7


def test_daily_statement_is_not_a_snapshot_source():
    """日對帳單沒有庫存表，拿它當快照會把 cutoff 訂在錯的日子。"""
    snaps = gr._build_snapshots([(US_DAILY, US_INVENTORY)], {})

    assert snaps == []


def test_tw_snapshot_comes_from_env():
    snaps = gr._build_snapshots([], {
        "TW_HOLDINGS": "2330:10@2249.6",
        "TW_HOLDINGS_ASOF": "2026-06-30",
    })

    assert len(snaps) == 1
    assert snaps[0]["market"] == "TW"
    assert snaps[0]["holdings"]["2330"]["shares"] == 10


def test_no_tw_env_means_no_tw_snapshot():
    """沒設定就退回成交累加，不要生一個空快照 —— 空快照會被當成「真的沒持股」。"""
    assert gr._build_snapshots([], {}) == []


def test_tw_statement_never_yields_snapshot():
    """台股月對帳單整份沒有庫存表（實測），不能硬解出東西來。"""
    snaps = gr._build_snapshots([(TW_MONTHLY, "有價證券買賣現股: 單位:新台幣元")], {})

    assert snaps == []


def test_both_markets_can_coexist():
    snaps = gr._build_snapshots([(US_MONTHLY, US_INVENTORY)], {
        "TW_HOLDINGS": "2330:10@2249.6",
        "TW_HOLDINGS_ASOF": "2026-06-30",
    })

    assert {s["market"] for s in snaps} == {"US", "TW"}


def test_empty_inventory_does_not_become_snapshot():
    """庫存表解析為空（格式變了）不能當成「這期沒持股」—— 那會把之前的成交全跳過。"""
    snaps = gr._build_snapshots([(US_MONTHLY, "股票庫存明細表（截至印表時間)")], {})

    assert snaps == []


# ── 端到端：接線後 get_portfolio_from_gmail 要真的用到快照 ──────

def test_position_with_no_recent_trades_survives(monkeypatch):
    """HANDOFF 4.1 的核心場景：買很久、之後沒再交易的部位不能消失。

    抓不到任何信（等同該部位的成交早就超出 3 個月範圍），但手動快照有它 ——
    修好之前這裡回 {}，持倉整檔蒸發、淨值少算。
    """
    monkeypatch.setattr(gr, "_download_email_items", lambda: [])
    monkeypatch.setenv("TW_HOLDINGS", "2330:10@2249.6")
    monkeypatch.setenv("TW_HOLDINGS_ASOF", "2026-06-30")

    portfolio = gr.get_portfolio_from_gmail()

    assert portfolio["2330"]["shares"] == 10


def test_no_snapshot_still_falls_back_to_trade_accumulation(monkeypatch):
    """沒有任何快照時要維持原本行為，不能因為接線就整個不能用。"""
    monkeypatch.setattr(gr, "_download_email_items", lambda: [])
    monkeypatch.delenv("TW_HOLDINGS", raising=False)
    monkeypatch.delenv("TW_HOLDINGS_ASOF", raising=False)

    assert gr.get_portfolio_from_gmail() == {}


# ── 起始庫存改由 Notion 提供（2026-08-26）──────────────────
# 使用者要能用 Notion App 直接改持倉,不用為了改一筆股數登入 Infisical。
# 環境變數保留當備援：Notion 掛掉時還有東西可用。

def _notion(monkeypatch, rows):
    import notion_db
    monkeypatch.setattr(notion_db, "starting_holdings_load", lambda: rows)


def _pos(ticker, market="TW", shares=10, cost=None, asof="2026-08-26"):
    return {"ticker": ticker, "market": market, "shares": shares,
            "avg_cost": cost, "asof": asof}


def test_notion_rows_become_snapshot(monkeypatch):
    _notion(monkeypatch, [_pos("2330", shares=10, cost=2249.6)])

    snaps = gr._build_snapshots([], {})
    tw = [s for s in snaps if s["market"] == "TW"][0]

    assert tw["holdings"]["2330"]["shares"] == 10
    assert tw["cutoff"] == (2026, 8, 26)


def test_notion_wins_over_env(monkeypatch):
    """兩邊都有時以 Notion 為準 —— 使用者改的是 Notion,那才是他的意圖。"""
    _notion(monkeypatch, [_pos("2330", shares=99)])

    snaps = gr._build_snapshots([], {
        "TW_HOLDINGS": "2330:10", "TW_HOLDINGS_ASOF": "2026-06-30",
    })
    tw = [s for s in snaps if s["market"] == "TW"][0]

    assert tw["holdings"]["2330"]["shares"] == 99


def test_env_used_when_notion_empty(monkeypatch):
    _notion(monkeypatch, [])

    snaps = gr._build_snapshots([], {
        "TW_HOLDINGS": "2330:10", "TW_HOLDINGS_ASOF": "2026-06-30",
    })

    assert [s["market"] for s in snaps] == ["TW"]


def test_markets_are_grouped_separately(monkeypatch):
    _notion(monkeypatch, [_pos("2330"), _pos("AAPL", market="US")])

    snaps = gr._build_snapshots([], {})

    assert {s["market"] for s in snaps} == {"TW", "US"}


def test_gold_grouped_with_tw(monkeypatch):
    _notion(monkeypatch, [_pos("2330"), _pos("AU9901")])

    snaps = gr._build_snapshots([], {})
    tw = [s for s in snaps if s["market"] == "TW"][0]

    assert set(tw["holdings"]) == {"2330", "AU9901"}


def test_mixed_asof_uses_latest(monkeypatch):
    """同市場混用不同基準日本身是設定錯誤。取最晚的（最接近現況）,
    不要猜 —— 取最早會讓晚的那份重複計算。"""
    _notion(monkeypatch, [
        _pos("2330", asof="2026-06-30"),
        _pos("0050", asof="2026-08-26"),
    ])

    snaps = gr._build_snapshots([], {})
    tw = [s for s in snaps if s["market"] == "TW"][0]

    assert tw["cutoff"] == (2026, 8, 26)


def test_notion_snapshot_labelled_notion(monkeypatch):
    """來源要說得出是哪來的 —— 數字不對時才知道去哪改。"""
    _notion(monkeypatch, [_pos("2330")])

    snaps = gr._build_snapshots([], {})
    tw = [s for s in snaps if s["market"] == "TW"][0]

    assert tw["origin"] == "notion"


def test_us_statement_still_wins_for_us(monkeypatch):
    """美股有月對帳單庫存（真實資料）,不該被手動設定蓋掉。"""
    _notion(monkeypatch, [_pos("AAPL", market="US", shares=99)])

    snaps = gr._build_snapshots([(US_MONTHLY, US_INVENTORY)], {})
    us = [s for s in snaps if s["market"] == "US"][0]

    assert us["holdings"]["AAPL"]["shares"] == 3
