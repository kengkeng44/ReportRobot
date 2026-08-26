"""Notion「起始庫存」表 —— 持倉計算的輸入。

為什麼不用既有的「持倉」表：那張是**程式的輸出**，每天 15:30 被
finance_sync 覆寫（欄位有現價 / 市值 / 報酬率 / 更新時間）。把起始庫存
填進同一張表會產生回饋迴圈：程式算錯 → 寫回 Notion → 下次把錯的當起點
讀回來 → 錯誤被固化成「事實」而且查不出源頭。

所以輸入與輸出分兩張表。這張只讀不寫。

為什麼不放環境變數：使用者要能用 Notion App 直接改持倉（2026-08-26），
不用為了改一筆股數去登入 Infisical。財務資料本來就都在 Notion。
"""

import notion_db


class _Databases:
    def __init__(self, rows, boom=False):
        self._rows = rows
        self.boom = boom
        self.queries = []

    def query(self, **kwargs):
        if self.boom:
            raise RuntimeError("boom")
        self.queries.append(kwargs)
        return {"results": self._rows, "has_more": False, "next_cursor": None}


class _Client:
    def __init__(self, databases):
        self.databases = databases


def _install(monkeypatch, rows, boom=False):
    dbs = _Databases(rows, boom=boom)
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", "page_fake")
    monkeypatch.setattr(notion_db, "_client", _Client(dbs))
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db_fake")
    return dbs


def _row(ticker, shares=10, cost=2249.6, market="TW", asof="2026-08-26"):
    return {
        "properties": {
            "代號": {"title": [{"plain_text": ticker}]},
            "市場": {"select": {"name": market} if market else None},
            "股數": {"number": shares},
            "平均成本": {"number": cost},
            "基準日": {"date": {"start": asof} if asof else None},
        }
    }


def test_loads_positions(monkeypatch):
    _install(monkeypatch, [_row("2330")])

    out = notion_db.starting_holdings_load()

    assert out[0]["ticker"] == "2330"
    assert out[0]["shares"] == 10
    assert out[0]["avg_cost"] == 2249.6
    assert out[0]["market"] == "TW"
    assert out[0]["asof"] == "2026-08-26"


def test_row_without_asof_is_skipped(monkeypatch):
    """基準日決定哪些成交要跳過。沒有基準日的庫存無法安全使用 ——
    猜一個日期就是少算或雙重計算,而且錯得無聲無息。"""
    _install(monkeypatch, [_row("2330", asof=None)])

    assert notion_db.starting_holdings_load() == []


def test_row_without_shares_is_skipped(monkeypatch):
    _install(monkeypatch, [_row("2330", shares=None)])

    assert notion_db.starting_holdings_load() == []


def test_zero_shares_is_skipped(monkeypatch):
    """0 股不是持倉。留著會讓 build_portfolio 以為這檔存在但沒有量。"""
    _install(monkeypatch, [_row("2330", shares=0)])

    assert notion_db.starting_holdings_load() == []


def test_cost_is_optional(monkeypatch):
    """成本不知道就是不知道,不要逼使用者編一個 —— 下游會標「未知」。"""
    _install(monkeypatch, [_row("2330", cost=None)])

    out = notion_db.starting_holdings_load()

    assert out[0]["avg_cost"] is None


def test_market_defaults_by_ticker_shape(monkeypatch):
    """市場沒填就用代號推,不要整列丟掉 —— 使用者手打漏一欄很正常。"""
    _install(monkeypatch, [_row("AAPL", market=None)])

    out = notion_db.starting_holdings_load()

    assert out[0]["market"] == "US"


def test_gold_is_tw_even_though_it_has_letters(monkeypatch):
    """AU9901（臺銀金）是台幣計價。判成美股會讓金額乘上匯率。"""
    _install(monkeypatch, [_row("AU9901", market=None)])

    out = notion_db.starting_holdings_load()

    assert out[0]["market"] == "TW"


def test_empty_table_returns_empty(monkeypatch):
    _install(monkeypatch, [])

    assert notion_db.starting_holdings_load() == []


def test_query_failure_does_not_raise(monkeypatch):
    """Notion 掛掉不能讓每日排程整個進 error listener —— 退回沒有快照就好。"""
    _install(monkeypatch, [], boom=True)

    assert notion_db.starting_holdings_load() == []


def test_describe_sources_says_notion():
    """來源要說得出是哪來的 —— 數字不對時才知道該去哪改。
    Notion 來的被說成「月對帳單庫存」會害人去翻一份不存在的表。"""
    import holdings

    snap = {"market": "TW", "period": (2026, 8), "cutoff": (2026, 8, 26),
            "origin": "notion", "holdings": {"2330": {"shares": 10, "avg_cost": None}}}

    _, sources = holdings.build_portfolio([], [snap])
    text = holdings.describe_sources(sources)

    assert "Notion" in text
    assert "月對帳單" not in text
