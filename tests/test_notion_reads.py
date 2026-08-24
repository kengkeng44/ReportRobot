"""持倉與淨值快照的讀取行為。

兩個缺口都是 2026-08-25 健檢時發現的：
- networth_load 沒指定 sorts，Notion 的回傳順序未定義。畫成折線圖是一團
  亂麻，而且看起來只像「淨值波動很大」，不像程式有問題。
- 持倉只有 holdings_sync 能寫，沒有函式讀得回來（每日推播是直接從 Gmail
  重算的，所以一直沒人發現）。
"""

import pytest

import notion_db


class RecordingDatabases:
    """記錄 query 參數並吐固定資料的假 Notion。"""

    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"results": self._rows, "has_more": False, "next_cursor": None}


class FakeClient:
    def __init__(self, databases):
        self.databases = databases


def _install(monkeypatch, rows, db_id="db_fake"):
    dbs = RecordingDatabases(rows)
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", "page_fake")
    monkeypatch.setattr(notion_db, "_client", FakeClient(dbs))
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: db_id)
    return dbs


# ── 淨值快照 ──────────────────────────────────────────────

def _networth_row(day, net):
    return {
        "properties": {
            "日期": {"title": [{"plain_text": day}]},
            "現金": {"number": None},
            "股票市值": {"number": net},
            "信用卡未繳": {"number": None},
            "淨值": {"number": net},
        }
    }


def test_networth_load_asks_notion_to_sort_by_date(monkeypatch):
    """這是整組測試的重點：一定要送 sorts，不能靠 Notion 的預設順序。"""
    dbs = _install(monkeypatch, [_networth_row("2026-08-01", 100)])

    notion_db.networth_load()

    sorts = dbs.queries[0].get("sorts")
    assert sorts == [{"property": "日期", "direction": "ascending"}]


def test_networth_load_returns_oldest_first(monkeypatch):
    """趨勢圖要由舊到新畫，順序反了折線會整條倒過來。"""
    rows = [_networth_row("2026-08-01", 100), _networth_row("2026-08-02", 120)]
    _install(monkeypatch, rows)

    out = notion_db.networth_load()

    assert [r["date"] for r in out] == ["2026-08-01", "2026-08-02"]
    assert out[-1]["net"] == 120


def test_networth_load_survives_failure(monkeypatch):
    class Boom:
        def query(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", "page_fake")
    monkeypatch.setattr(notion_db, "_client", FakeClient(Boom()))
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db_fake")

    assert notion_db.networth_load() == []


# ── 持倉 ──────────────────────────────────────────────────

def _holding_row(ticker, value, pct=None):
    return {
        "properties": {
            "代號": {"title": [{"plain_text": ticker}]},
            "名稱": {"rich_text": [{"plain_text": f"{ticker} 名稱"}]},
            "市場": {"select": {"name": "TW"}},
            "股數": {"number": 100},
            "平均成本": {"number": 10},
            "現價": {"number": 12},
            "市值": {"number": value},
            "未實現損益": {"number": 200},
            "報酬率": {"number": pct},
        }
    }


def test_holdings_load_reads_back_written_shape(monkeypatch):
    """欄位名要對齊 holdings_sync 的輸入，否則兩邊各講各的。"""
    _install(monkeypatch, [_holding_row("2330", 1000, 0.2)])

    out = notion_db.holdings_load()

    assert len(out) == 1
    assert out[0]["ticker"] == "2330"
    assert out[0]["display"] == "2330 名稱"
    assert out[0]["market"] == "TW"
    assert out[0]["value"] == 1000


def test_holdings_load_converts_percent_back(monkeypatch):
    """Notion 的 percent 是「1 = 100%」，讀回來要還原成人看的數字。

    holdings_sync 寫入時除以 100，這裡要對稱地乘回去 —— 不然
    20% 的報酬率會在畫面上顯示成 0.2%。
    """
    _install(monkeypatch, [_holding_row("2330", 1000, 0.2)])

    assert notion_db.holdings_load()[0]["pnl_pct"] == pytest.approx(20)


def test_holdings_load_keeps_missing_percent_as_none(monkeypatch):
    """沒有報酬率就是沒有，不能變成 0% —— 那看起來像「持平」。"""
    _install(monkeypatch, [_holding_row("2330", 1000, None)])

    assert notion_db.holdings_load()[0]["pnl_pct"] is None


def test_holdings_load_sorts_by_value_desc(monkeypatch):
    rows = [_holding_row("A", 100), _holding_row("B", 5000), _holding_row("C", 900)]
    _install(monkeypatch, rows)

    assert [r["ticker"] for r in notion_db.holdings_load()] == ["B", "C", "A"]


def test_holdings_load_puts_valueless_last(monkeypatch):
    """沒市值的沉到最後，而不是被當成 0 混在中間。"""
    rows = [_holding_row("A", None), _holding_row("B", 100)]
    _install(monkeypatch, rows)

    assert [r["ticker"] for r in notion_db.holdings_load()] == ["B", "A"]
