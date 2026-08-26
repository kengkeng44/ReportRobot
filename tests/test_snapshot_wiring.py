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
