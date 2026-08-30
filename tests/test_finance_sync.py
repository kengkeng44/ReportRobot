"""財務同步管線：Gmail → parser → 去重 → Notion。

重點是「不要寫重複」和「不要因為一封信壞掉就整批中斷」。
"""

import pathlib

import pytest

import finance_sync

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "cathay_daily.html").read_text(encoding="utf-8")


class FakeNotion:
    def __init__(self, existing=None, fail_add=False):
        self.existing = set(existing or [])
        self.added = []
        self.fail_add = fail_add

    def is_configured(self):
        return True

    def transactions_existing_fingerprints(self, limit=400):
        return set(self.existing)

    def transaction_add(self, txn):
        if self.fail_add:
            return None
        self.added.append(txn)
        return f"page_{len(self.added)}"


class FakeGmail:
    """最小可用的 Gmail service 替身（users().messages().list/get）。"""

    def __init__(self, messages):
        self._messages = messages   # {id: html}
        self.list_calls = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults):
        self.list_calls.append(q)
        return _Exec({"messages": [{"id": mid} for mid in self._messages]})

    def get(self, userId, id, format):
        html = self._messages[id]
        if html is None:
            raise RuntimeError("讀信失敗")
        return _Exec({
            "payload": {
                "mimeType": "multipart/alternative",
                "parts": [{"mimeType": "text/html",
                           "body": {"data": _b64(html)}}],
            }
        })


class _Exec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


def _b64(text):
    import base64
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


# ── 正常路徑 ──────────────────────────────────────────────

def test_writes_parsed_transactions():
    notion = FakeNotion()
    gmail = FakeGmail({"m1": FIXTURE})

    stats = finance_sync.sync(service=gmail, notion=notion)

    assert stats["parsed"] == 3
    assert stats["written"] == 3
    assert len(notion.added) == 3


def test_attaches_mail_url_for_traceability():
    notion = FakeNotion()
    gmail = FakeGmail({"m1": FIXTURE})

    finance_sync.sync(service=gmail, notion=notion)

    assert all("m1" in t["mail_url"] for t in notion.added)


def test_query_includes_lookback_window():
    notion = FakeNotion()
    gmail = FakeGmail({"m1": FIXTURE})

    finance_sync.sync(service=gmail, notion=notion, lookback_days=7)

    assert all("newer_than:7d" in q for q in gmail.list_calls)


# ── 去重 ──────────────────────────────────────────────────

def test_skips_already_written_fingerprints():
    from parsers import cathay_daily
    known = {t["fingerprint"] for t in cathay_daily.parse(FIXTURE)}
    notion = FakeNotion(existing=known)
    gmail = FakeGmail({"m1": FIXTURE})

    stats = finance_sync.sync(service=gmail, notion=notion)

    assert stats["written"] == 0
    assert stats["skipped"] == 3
    assert notion.added == []


def test_rerunning_twice_does_not_duplicate():
    """排程重跑是常態（Railway 重啟、手動觸發），不能長出重複。"""
    notion = FakeNotion()
    gmail = FakeGmail({"m1": FIXTURE})

    finance_sync.sync(service=gmail, notion=notion)
    notion.existing = {t["fingerprint"] for t in notion.added}
    second = finance_sync.sync(service=gmail, notion=notion)

    assert second["written"] == 0
    assert len(notion.added) == 3


def test_same_message_seen_twice_in_one_run_writes_once():
    """同一批裡有重複信件（Gmail 有時會回同一封的多個 id）。"""
    notion = FakeNotion()
    gmail = FakeGmail({"m1": FIXTURE, "m2": FIXTURE})

    stats = finance_sync.sync(service=gmail, notion=notion)

    assert stats["written"] == 3, "第二封的內容跟第一封相同，不該重複寫"
    assert stats["skipped"] == 3


# ── 失敗處理 ──────────────────────────────────────────────

def test_aborts_when_existing_fingerprints_unreadable():
    """讀不到既有指紋就不要寫 —— 寧可這次不同步，也不要製造重複。"""
    notion = FakeNotion()
    notion.transactions_existing_fingerprints = lambda limit=400: None
    gmail = FakeGmail({"m1": FIXTURE})

    stats = finance_sync.sync(service=gmail, notion=notion)

    assert stats["written"] == 0
    assert notion.added == []


def test_one_bad_message_does_not_stop_the_batch():
    notion = FakeNotion()
    gmail = FakeGmail({"bad": None, "m1": FIXTURE})

    stats = finance_sync.sync(service=gmail, notion=notion)

    assert stats["written"] == 3, "壞掉那封略過，其餘照寫"


def test_notion_write_failure_is_counted_not_crashed():
    notion = FakeNotion(fail_add=True)
    gmail = FakeGmail({"m1": FIXTURE})

    stats = finance_sync.sync(service=gmail, notion=notion)

    assert stats["parsed"] == 3
    assert stats["written"] == 0


def test_skips_entirely_when_notion_not_configured():
    notion = FakeNotion()
    notion.is_configured = lambda: False
    gmail = FakeGmail({"m1": FIXTURE})

    stats = finance_sync.sync(service=gmail, notion=notion)

    assert stats == {"parsed": 0, "written": 0, "skipped": 0, "sources": 0}


def test_no_gmail_service_is_graceful(monkeypatch):
    """取不到 Gmail service（token 過期）時要安靜跳過，不能炸掉排程。

    一定要 patch 掉真實的 get_gmail_service —— service=None 時 sync() 會
    自己去取一個，在有 token.pickle 的開發機上這會**真的連上 Gmail 撈信**，
    測試就變成在讀使用者的真實信箱，而且只在那台機器上紅。
    """
    import gmail_reader
    monkeypatch.setattr(gmail_reader, "get_gmail_service", lambda: None)

    notion = FakeNotion()
    stats = finance_sync.sync(service=None, notion=notion)
    assert stats["written"] == 0


# ── 摘要 ──────────────────────────────────────────────────

def test_summary_mentions_counts():
    text = finance_sync.format_summary(
        {"parsed": 5, "written": 2, "skipped": 3, "sources": 1})
    assert "2" in text and "5" in text


# ── 持倉與淨值快照 ────────────────────────────────────────

class FakePortfolioNotion(FakeNotion):
    def __init__(self):
        super().__init__()
        self.holdings = None
        self.snapshots = []

    def holdings_sync(self, rows):
        self.holdings = rows
        return len(rows), 0

    def networth_upsert(self, day, cash=None, stock=None, card_due=None, net=None):
        self.snapshots.append({"day": day, "cash": cash, "stock": stock, "net": net})
        return "page_1"


def _fake_compute(monkeypatch, net_ntd, rows=None):
    import portfolio
    monkeypatch.setattr(portfolio, "_compute_portfolio_data", lambda p: {
        "rows": rows if rows is not None else [
            {"ticker": "2330", "display": "台積電", "is_us": False,
             "shares": 100, "avg": 900.0, "current": 1000.0,
             "pnl": 10000.0, "pnl_pct": 11.1},
        ],
        "net_value_ntd": net_ntd,
    })


def test_portfolio_written_to_notion(monkeypatch):
    _fake_compute(monkeypatch, 100000.0)
    notion = FakePortfolioNotion()

    stats = finance_sync.sync_portfolio(portfolio={"2330": {}}, notion=notion)

    assert stats["holdings"] == 1
    assert notion.holdings[0]["ticker"] == "2330"


def test_snapshot_recorded_with_stock_value(monkeypatch):
    _fake_compute(monkeypatch, 100000.0)
    notion = FakePortfolioNotion()

    from datetime import date
    finance_sync.sync_portfolio(portfolio={"2330": {}}, notion=notion,
                                today=date(2026, 8, 12))

    assert notion.snapshots[0]["day"] == "2026-08-12"
    assert notion.snapshots[0]["net"] == 100000.0


def test_snapshot_skipped_when_net_unknown(monkeypatch):
    """美股有部位但抓不到匯率時 net 會是 None。
    寧可不寫，也不要記一個少算美股的淨值。"""
    _fake_compute(monkeypatch, None)
    notion = FakePortfolioNotion()

    stats = finance_sync.sync_portfolio(portfolio={"AAPL": {}}, notion=notion)

    assert stats["snapshot"] is False
    assert notion.snapshots == []


def test_empty_portfolio_is_graceful():
    notion = FakePortfolioNotion()
    stats = finance_sync.sync_portfolio(portfolio={}, notion=notion)
    assert stats["holdings"] == 0
    assert notion.snapshots == []


def test_portfolio_skipped_when_notion_unconfigured():
    notion = FakePortfolioNotion()
    notion.is_configured = lambda: False
    stats = finance_sync.sync_portfolio(portfolio={"2330": {}}, notion=notion)
    assert stats["holdings"] == 0


def test_summary_when_nothing_new():
    assert "沒有新交易" in finance_sync.format_summary(
        {"parsed": 0, "written": 0, "skipped": 0, "sources": 1})


class _PxParser:
    """假 parser：回一筆全聯、一筆其他店，用來驗商店規則有沒有接上管線。"""

    @staticmethod
    def parse(html):
        return [
            {"date": "2026-08-26", "amount": 506, "shop": "全聯福利中心－板橋板新",
             "category": "超市∕量販", "direction": "支出", "status": "授權中",
             "source": "國泰消費彙整", "fingerprint": "px-fp-1"},
            {"date": "2026-08-26", "amount": 150, "shop": "星巴克",
             "category": "餐飲", "direction": "支出", "status": "授權中",
             "source": "國泰消費彙整", "fingerprint": "sb-fp-1"},
        ]


def test_sync_applies_shared_shop_rule(monkeypatch):
    """全聯進來就該自動記成共同並只留我那半 —— 不然每個月都要手動改一次，
    而手動維護的東西遲早會漏，漏掉的月份支出就悄悄高估。"""
    monkeypatch.setattr(finance_sync, "SOURCES", [("q", _PxParser)])
    notion = FakeNotion()

    finance_sync.sync(service=FakeGmail({"m1": "<html></html>"}), notion=notion)

    by_shop = {t["shop"]: t for t in notion.added}
    px = by_shop["全聯福利中心－板橋板新"]
    assert px["split_type"] == "共同"
    assert px["amount"] == 253
    assert px["total"] == 506
    assert px["fingerprint"] == "px-fp-1", "去重鍵不能被規則改掉"

    sb = by_shop["星巴克"]
    assert sb["amount"] == 150
    assert "split_type" not in sb
