"""每日信的「持倉今日漲跌」。

刻意只回答「今天漲跌幾 %」：成本、總報酬、市值那些走 LINE 的「持股」
指令，那邊已經有完整的持倉概覽（portfolio.build_portfolio_summary）。
信裡塞全部會太長 —— 使用者 2026-09-05 指定只要漲跌。

純邏輯與 I/O 分開：format_moves / pct_change 完全不碰網路，
daily_moves 用 _store / _quote 兩個接縫讓測試整個換掉（同 phrasebook）。
"""

import stock_moves


# ── 漲跌幅 ────────────────────────────────────────────────

def test_pct_change_basic():
    assert stock_moves.pct_change(110, 100) == 10
    assert stock_moves.pct_change(90, 100) == -10


def test_pct_change_needs_a_previous_close():
    """沒有前一日收盤 ≠ 持平。硬算會得到一個看起來正常的假數字。"""
    assert stock_moves.pct_change(110, None) is None
    assert stock_moves.pct_change(None, 100) is None


def test_pct_change_survives_zero_previous_close():
    """prev=0 除下去會炸。興櫃 / 停牌的資料真的會出現 0。"""
    assert stock_moves.pct_change(110, 0) is None


# ── 組版 ──────────────────────────────────────────────────

def _item(display, price, pct):
    return {"display": display, "price": price, "pct": pct}


def test_format_shows_arrow_price_and_percent():
    out = stock_moves.format_moves([_item("台積電", 1085, 1.23)])

    assert "台積電" in out
    assert "1,085" in out
    assert "1.2%" in out
    assert "▲" in out


def test_down_uses_a_different_arrow():
    out = stock_moves.format_moves([_item("AAPL", 232.1, -0.84)])

    assert "▼" in out
    assert "0.8%" in out


def test_flat_is_neither_up_nor_down():
    """0.0% 標成紅或綠都是誤導。"""
    out = stock_moves.format_moves([_item("00632R", 18.5, 0.0)])

    assert "▲" not in out and "▼" not in out


def test_biggest_movers_come_first():
    """使用者要看的是「什麼在動」，不是「什麼最值錢」——
    所以照漲跌幅**絕對值**排，跌 5% 要排在漲 1% 前面。"""
    out = stock_moves.format_moves([
        _item("小漲", 10, 1.0),
        _item("大跌", 20, -5.0),
        _item("中漲", 30, 3.0),
    ])
    order = [out.index(n) for n in ("大跌", "中漲", "小漲")]

    assert order == sorted(order)


def test_long_portfolio_is_truncated():
    """持倉多的時候整封信會被這一塊灌爆。"""
    items = [_item(f"S{i}", 10, float(i)) for i in range(20)]

    out = stock_moves.format_moves(items, limit=5)

    assert len(out.splitlines()) <= 6      # 5 檔 + 「還有 N 檔」那行
    assert "還有" in out


def test_no_holdings_returns_none():
    """空的區塊直接不放，不要留一張空卡片。"""
    assert stock_moves.format_moves([]) is None
    assert stock_moves.format_moves(None) is None


def test_price_keeps_cents_only_when_it_has_them():
    """1085.00 顯示成 1,085；232.10 顯示成 232.1。
    台股與美股的小數位數不同，補一排 .00 只是雜訊。"""
    out = stock_moves.format_moves([_item("A", 1085.0, 1.0), _item("B", 232.10, 2.0)])

    assert "1,085 " in out or "1,085　" in out
    assert "232.1" in out
    assert "232.10" not in out


# ── daily_moves：接起 Notion 與報價 ───────────────────────

class FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def holdings_load(self, limit=100):
        return list(self._rows)


def _install(monkeypatch, rows, quotes):
    monkeypatch.setattr(stock_moves, "_store", lambda: FakeStore(rows))
    monkeypatch.setattr(stock_moves, "_quote", lambda t: quotes.get(t, (None, None)))


def test_daily_moves_joins_holdings_with_quotes(monkeypatch):
    _install(monkeypatch,
             [{"ticker": "2330", "display": "台積電"}],
             {"2330": (1085, 1072)})

    out = stock_moves.daily_moves()

    assert "台積電" in out
    assert "▲" in out


def test_ticker_is_used_when_there_is_no_display_name(monkeypatch):
    _install(monkeypatch, [{"ticker": "AAPL", "display": ""}],
             {"AAPL": (232.1, 234.0)})

    assert "AAPL" in stock_moves.daily_moves()


def test_unquotable_holdings_are_skipped_not_zeroed(monkeypatch):
    """黃金存摺、興櫃抓不到價。顯示成 0.0% 會讓人以為它今天沒動。"""
    _install(monkeypatch,
             [{"ticker": "AU9901", "display": "臺銀金"},
              {"ticker": "2330", "display": "台積電"}],
             {"2330": (1085, 1072)})

    out = stock_moves.daily_moves()

    assert "臺銀金" not in out
    assert "台積電" in out


def test_everything_unquotable_returns_none(monkeypatch):
    _install(monkeypatch, [{"ticker": "AU9901", "display": "臺銀金"}], {})

    assert stock_moves.daily_moves() is None


def test_no_holdings_at_all_returns_none(monkeypatch):
    _install(monkeypatch, [], {})

    assert stock_moves.daily_moves() is None


def test_one_broken_quote_does_not_kill_the_rest(monkeypatch):
    """一檔抓價炸掉，其他檔還是要出現。"""
    def _boom(ticker):
        if ticker == "BAD":
            raise RuntimeError("yahoo 掛了")
        return (1085, 1072)

    monkeypatch.setattr(stock_moves, "_store",
                        lambda: FakeStore([{"ticker": "BAD", "display": "壞檔"},
                                           {"ticker": "2330", "display": "台積電"}]))
    monkeypatch.setattr(stock_moves, "_quote", _boom)

    out = stock_moves.daily_moves()

    assert "台積電" in out
    assert "壞檔" not in out
