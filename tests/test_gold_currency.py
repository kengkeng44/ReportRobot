"""AU9901（臺銀金 / 黃金現貨）是台幣計價，不是美股。

2026-08-26 使用者在富邦證券 App 買了 AU9901，實測發現 `_is_tw_ticker('AU9901')`
回 False —— 台股代號判斷是「四碼數字」規則，AU9901 帶字母所以被判成美股。

後果不是顯示難看而已：淨值會把台幣金額當成美元再乘匯率，一筆一萬七的黃金
會膨脹成五十幾萬，而且畫面上看起來就是個正常數字。

程式裡本來就有 `_is_special_security` 認得 AU 開頭是黃金，但那只用在
「抓不到現價」的判斷上，沒接到幣別判斷 —— 兩個地方各自認得一半。
"""

import portfolio as pf


def _stub(monkeypatch, prices, rate=32.0):
    monkeypatch.setattr(pf, "get_live_price", lambda t: prices.get(t))
    monkeypatch.setattr(pf, "get_stock_name", lambda t: t)
    monkeypatch.setattr(pf, "_get_usd_twd", lambda: rate)


def test_gold_is_not_us_market():
    assert pf._is_tw_ticker("AU9901") is True


def test_gold_lowercase_also_recognised():
    assert pf._is_tw_ticker("au9901") is True


def test_normal_tw_ticker_still_works():
    assert pf._is_tw_ticker("2330") is True


def test_us_ticker_still_us():
    assert pf._is_tw_ticker("AAPL") is False


def test_gold_value_not_multiplied_by_fx_rate(monkeypatch):
    """核心：17,410 是台幣。被當美股就會乘 32 變成五十幾萬。"""
    _stub(monkeypatch, {"AU9901": 17410.0})

    data = pf._compute_portfolio_data({"AU9901": {"shares": 1, "avg_cost": 17410.0}})

    assert data["net_value_ntd"] == 17410


def test_gold_counts_toward_tw_summary(monkeypatch):
    _stub(monkeypatch, {"AU9901": 18000.0})

    data = pf._compute_portfolio_data({"AU9901": {"shares": 1, "avg_cost": 17410.0}})

    assert data["us_summary"] is None
    assert data["tw_summary"] is not None


def test_gold_with_no_price_still_tw(monkeypatch):
    """金價抓不到時走 N/A 分支，那條也不能把它算成美股。"""
    _stub(monkeypatch, {})

    row = pf._compute_portfolio_data({"AU9901": {"shares": 1, "avg_cost": 17410.0}})["rows"][0]

    assert row["is_us"] is False


# ── holdings 那邊也要認得 —— 成交分組用的是 guess_market ──────

def test_holdings_guess_market_treats_gold_as_tw():
    """分錯市場會讓黃金的成交套到美股快照的 cutoff 上。"""
    import holdings

    assert holdings.guess_market("AU9901") == "TW"
