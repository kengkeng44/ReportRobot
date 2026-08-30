"""transactions_load 的分頁行為。

Notion 單頁上限 100 筆，要拿更多必須用 next_cursor 續撈。原本只查一次
就回，limit 傳 200 也只拿得到 100 筆 —— 不會報錯，本月支出只是靜靜變小，
看起來就像那個月比較省。這組測試守住那個行為。
"""

import notion_db


class PagedDatabases:
    """依 start_cursor 分頁吐資料的假 Notion。記錄每次查詢的參數。"""

    def __init__(self, total):
        self._rows = [self._row(i) for i in range(total)]
        self.queries = []

    @staticmethod
    def _row(i):
        return {
            "properties": {
                "日期": {"date": {"start": f"2026-08-{(i % 28) + 1:02d}"}},
                "金額": {"number": i + 1},
                "商店": {"rich_text": [{"plain_text": f"店{i}"}]},
                "類別": {"select": {"name": "餐飲"}},
                "方向": {"select": {"name": "支出"}},
                "狀態": {"select": {"name": "授權中"}},
                "來源": {"select": {"name": "手動" if i % 2 else "國泰消費彙整"}},
            }
        }

    def query(self, **kwargs):
        self.queries.append(kwargs)
        start = int(kwargs.get("start_cursor") or 0)
        size = kwargs.get("page_size", 100)
        chunk = self._rows[start:start + size]
        nxt = start + len(chunk)
        return {
            "results": chunk,
            "has_more": nxt < len(self._rows),
            "next_cursor": str(nxt) if nxt < len(self._rows) else None,
        }


class FakeClient:
    def __init__(self, databases):
        self.databases = databases


def _install(monkeypatch, total):
    dbs = PagedDatabases(total)
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", "page_fake")
    monkeypatch.setattr(notion_db, "_client", FakeClient(dbs))
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db_交易明細")
    return dbs


def test_loads_beyond_one_page(monkeypatch):
    """這是整組測試的重點：超過 100 筆時不能只回 100。"""
    _install(monkeypatch, total=250)

    rows = notion_db.transactions_load(limit=200)

    assert len(rows) == 200


def test_stops_at_limit_not_at_end_of_data(monkeypatch):
    dbs = _install(monkeypatch, total=1000)

    rows = notion_db.transactions_load(limit=150)

    assert len(rows) == 150
    assert len(dbs.queries) == 2, "150 筆應該只需要兩頁，不要多撈"


def test_stops_when_data_runs_out(monkeypatch):
    """資料比 limit 少時要正常結束，不能一直空轉。"""
    dbs = _install(monkeypatch, total=11)

    rows = notion_db.transactions_load(limit=200)

    assert len(rows) == 11
    assert len(dbs.queries) == 1


def test_single_page_still_works(monkeypatch):
    _install(monkeypatch, total=40)
    assert len(notion_db.transactions_load(limit=100)) == 40


def test_page_size_never_exceeds_notion_max(monkeypatch):
    """page_size 超過 100 Notion 會直接回錯誤。"""
    dbs = _install(monkeypatch, total=500)

    notion_db.transactions_load(limit=300)

    assert all(q["page_size"] <= 100 for q in dbs.queries)


def test_fields_are_mapped(monkeypatch):
    _install(monkeypatch, total=3)

    rows = notion_db.transactions_load()

    assert rows[0]["amount"] == 1
    assert rows[0]["shop"] == "店0"
    assert rows[0]["category"] == "餐飲"
    assert rows[0]["status"] == "授權中"
    assert rows[0]["currency"] == "TWD", "沒有幣別欄的舊資料當台幣"


def test_partial_result_survives_midway_failure(monkeypatch):
    """第二頁炸掉時，第一頁的資料要留著 —— 半個月的帳還是能算，
    比整個變空好。"""
    dbs = _install(monkeypatch, total=500)
    original = dbs.query
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("Notion 掛了")
        return original(**kwargs)

    dbs.query = flaky

    rows = notion_db.transactions_load(limit=300)

    assert len(rows) == 100


def test_load_reads_source(monkeypatch):
    """來源寫得進去就要讀得回來，否則分不出手動記帳與自動同步。

    transaction_add 一直有送「來源」，但這裡沒讀回來 —— 不會報錯，
    只是每一筆的 source 都是 None，任何想區分兩者的功能都會安靜地失效。
    """
    _install(monkeypatch, 4)

    rows = notion_db.transactions_load(limit=4)

    assert [r["source"] for r in rows] == [
        "國泰消費彙整", "手動", "國泰消費彙整", "手動"]


class OneRow:
    """單列假 Notion，用來測欄位讀取與舊資料 fallback。"""

    def __init__(self, props):
        self._props = props
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"results": [{"properties": self._props}],
                "has_more": False, "next_cursor": None}


def _install_row(monkeypatch, props):
    dbs = OneRow(props)
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", "page_fake")
    monkeypatch.setattr(notion_db, "_client", FakeClient(dbs))
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db_交易明細")
    return dbs


def test_reads_split_columns(monkeypatch):
    _install_row(monkeypatch, {
        "日期": {"date": {"start": "2026-08-30"}},
        "金額": {"number": 300},
        "原始總額": {"number": 600},
        "分攤類型": {"select": {"name": "共同"}},
    })

    row = notion_db.transactions_load(limit=1)[0]

    assert row["split_type"] == "共同"
    assert row["total"] == 600
    assert row["amount"] == 300


def test_old_rows_without_split_columns_default_to_personal(monkeypatch):
    """遷移前的資料沒有這兩欄。既有的國泰同步資料本來就是自己刷的，
    一律當個人；原始總額回退成金額 —— 個人消費兩者本來就相等。

    沒有這兩條 fallback，所有統計都得特判 None。"""
    _install_row(monkeypatch, {
        "日期": {"date": {"start": "2026-08-01"}},
        "金額": {"number": 361},
        "來源": {"select": {"name": "國泰消費彙整"}},
    })

    row = notion_db.transactions_load(limit=1)[0]

    assert row["split_type"] == "個人"
    assert row["total"] == 361
