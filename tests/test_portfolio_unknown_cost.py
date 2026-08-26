"""持倉成本未知時的顯示與總計。

來源：複委託月對帳單的「股票庫存明細表」只有股數與參考市值，**沒有成本均價**
（2026-08-25 /admin/statement-dump 實測）。所以拿庫存快照當持倉起點之後，
一定會出現「股數確定、成本未知」的部位。

兩個總計對這種部位的要求是相反的，必須分開算：
  淨值   → 要計入。股票是真的持有，市值是真的，漏掉就是 HANDOFF 4.1 的少算。
  損益   → 不能計入。有市值沒成本，value - cost 會把整筆市值當成獲利。
"""

import portfolio as pf


def _stub_prices(monkeypatch, prices, rate=32.0):
    monkeypatch.setattr(pf, "get_live_price", lambda t: prices.get(t))
    monkeypatch.setattr(pf, "get_stock_name", lambda t: t)
    monkeypatch.setattr(pf, "_get_usd_twd", lambda: rate)


def test_unknown_cost_row_does_not_crash(monkeypatch):
    _stub_prices(monkeypatch, {"AAPL": 300.0})

    data = pf._compute_portfolio_data({"AAPL": {"shares": 3, "avg_cost": None}})

    assert data["rows"][0]["shares"] == 3


def test_unknown_cost_shows_as_unknown_not_zero(monkeypatch):
    """均價顯示 0 會讓人以為是零成本取得，那是假資訊。"""
    _stub_prices(monkeypatch, {"AAPL": 300.0})

    row = pf._compute_portfolio_data({"AAPL": {"shares": 3, "avg_cost": None}})["rows"][0]

    assert row["avg"] is None
    assert "0" not in row["avg_str"]


def test_unknown_cost_has_no_fabricated_pnl(monkeypatch):
    _stub_prices(monkeypatch, {"AAPL": 300.0})

    row = pf._compute_portfolio_data({"AAPL": {"shares": 3, "avg_cost": None}})["rows"][0]

    assert row["pnl"] is None
    assert row["pnl_pct"] is None


def test_unknown_cost_still_counts_toward_net_value(monkeypatch):
    """核心：這正是 HANDOFF 4.1 要修的少算。持股是真的，市值一定要進淨值。"""
    _stub_prices(monkeypatch, {"2317": 200.0})

    data = pf._compute_portfolio_data({"2317": {"shares": 1000, "avg_cost": None}})

    assert data["net_value_ntd"] == 200_000


def test_unknown_cost_does_not_inflate_pnl_summary(monkeypatch):
    """2317 成本已知、0050 成本未知。小計損益只能反映 2317。"""
    _stub_prices(monkeypatch, {"2317": 110.0, "0050": 50.0})

    data = pf._compute_portfolio_data({
        "2317": {"shares": 1000, "avg_cost": 100.0},
        "0050": {"shares": 1000, "avg_cost": None},
    })

    assert data["tw_summary"]["pnl"] == 10_000
    assert data["tw_summary"]["pct"] == 10.0


def test_net_value_includes_both_known_and_unknown(monkeypatch):
    """小計排除未知成本，但淨值兩者都要算。"""
    _stub_prices(monkeypatch, {"2317": 110.0, "0050": 50.0})

    data = pf._compute_portfolio_data({
        "2317": {"shares": 1000, "avg_cost": 100.0},
        "0050": {"shares": 1000, "avg_cost": None},
    })

    assert data["net_value_ntd"] == 160_000


def test_unknown_cost_tickers_are_reported(monkeypatch):
    """要讓呼叫端說得出「哪幾檔沒算進損益」，不能靜靜少算。"""
    _stub_prices(monkeypatch, {"2317": 110.0, "0050": 50.0})

    data = pf._compute_portfolio_data({
        "2317": {"shares": 1000, "avg_cost": 100.0},
        "0050": {"shares": 1000, "avg_cost": None},
    })

    assert data["unknown_cost_tickers"] == ["0050"]


def test_all_unknown_cost_yields_no_summary_but_real_net_value(monkeypatch):
    """全部成本未知時不要生一個 0% 的假小計，但淨值仍然成立。"""
    _stub_prices(monkeypatch, {"2317": 110.0})

    data = pf._compute_portfolio_data({"2317": {"shares": 1000, "avg_cost": None}})

    assert data["tw_summary"] is None
    assert data["net_value_ntd"] == 110_000


def test_unknown_cost_with_no_live_price_does_not_crash(monkeypatch):
    """成本未知 + 現價也抓不到（興櫃 / 黃金存摺）—— 排序鍵不能炸。"""
    _stub_prices(monkeypatch, {})

    data = pf._compute_portfolio_data({"2317": {"shares": 1000, "avg_cost": None}})

    assert data["rows"][0]["current"] is None


def test_summary_text_renders_with_unknown_cost(monkeypatch):
    """整條路走到底：文字版持倉不能因為成本未知就整個炸掉。"""
    _stub_prices(monkeypatch, {"2317": 110.0})

    text = pf.build_portfolio_summary({"2317": {"shares": 1000, "avg_cost": None}})

    assert "2317" in text


def test_summary_says_which_positions_lack_cost(monkeypatch):
    """少算要說得出是哪幾檔。靜靜少算正是 HANDOFF 4.1 一開始沒被發現的原因。"""
    _stub_prices(monkeypatch, {"2317": 110.0, "0050": 50.0})

    text = pf.build_portfolio_summary({
        "2317": {"shares": 1000, "avg_cost": 100.0},
        "0050": {"shares": 1000, "avg_cost": None},
    })

    assert "成本未知" in text
    assert "0050" in text


def test_summary_has_no_such_note_when_all_costs_known(monkeypatch):
    _stub_prices(monkeypatch, {"2317": 110.0})

    text = pf.build_portfolio_summary({"2317": {"shares": 1000, "avg_cost": 100.0}})

    assert "成本未知" not in text


def test_text_net_value_includes_unknown_cost_positions(monkeypatch):
    """文字版淨值也要含成本未知的部位 —— 漏掉就是 HANDOFF 4.1 的少算重演。"""
    _stub_prices(monkeypatch, {"2317": 110.0})

    text = pf.build_portfolio_summary({"2317": {"shares": 1000, "avg_cost": None}})

    assert "110,000" in text


def test_text_net_value_survives_missing_fx_rate(monkeypatch):
    """匯率抓不到時走另一條分支 —— 那條也不能漏掉未知成本的部位。"""
    _stub_prices(monkeypatch, {"2317": 110.0, "AAPL": 300.0}, rate=None)

    text = pf.build_portfolio_summary({
        "2317": {"shares": 1000, "avg_cost": None},
        "AAPL": {"shares": 2, "avg_cost": None},
    })

    assert "110,000" in text
    assert "600" in text
