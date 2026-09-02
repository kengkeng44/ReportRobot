"""語句庫 / 金句庫的 Notion 讀寫。

排程決策在 phrasebook.py(純邏輯,另一份測試),這裡只驗 I/O:
撈回來的欄位對不對、寫回去的 payload 對不對、Notion 掛掉會不會炸。

假 client 的形狀沿用 tests/test_notion_reads.py 的做法。
"""

from datetime import date

import notion_db


D = date(2026, 9, 1)


class RecordingDatabases:
    def __init__(self, pages):
        self._pages = pages
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"results": self._pages, "has_more": False, "next_cursor": None}


class RecordingPages:
    def __init__(self):
        self.created = []
        self.updated = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "new_page"}

    def update(self, **kwargs):
        self.updated.append(kwargs)
        return {"id": kwargs.get("page_id")}


class FakeClient:
    def __init__(self, databases, pages):
        self.databases = databases
        self.pages = pages


def _install(monkeypatch, pages_rows, db_id="db_fake"):
    dbs = RecordingDatabases(pages_rows)
    pages = RecordingPages()
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", "page_fake")
    monkeypatch.setattr(notion_db, "_client", FakeClient(dbs, pages))
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: db_id)
    return dbs, pages


def _phrase_page(page_id, sentence, meaning="", note="",
                 appeared=None, due=None):
    return {
        "id": page_id,
        "properties": {
            "句子": {"title": [{"plain_text": sentence}]},
            "中文意思": {"rich_text": [{"plain_text": meaning}]},
            "情境備註": {"rich_text": [{"plain_text": note}]},
            "出現次數": {"number": appeared},
            "下次出現": {"date": {"start": due} if due else None},
        },
    }


def _quote_page(page_id, text, source="", last_seen=None):
    return {
        "id": page_id,
        "properties": {
            "金句": {"title": [{"plain_text": text}]},
            "出處": {"rich_text": [{"plain_text": source}]},
            "上次出現": {"date": {"start": last_seen} if last_seen else None},
        },
    }


# ── 撈句子 ────────────────────────────────────────────────

def test_phrases_load_filters_by_language(monkeypatch):
    """撈英文時不能把西班牙文一起撈回來 —— 兩個語言各挑各的。"""
    dbs, _ = _install(monkeypatch, [_phrase_page("a", "hello")])

    notion_db.phrases_load("英文")

    assert dbs.queries[0]["filter"] == {
        "property": "語言", "select": {"equals": "英文"},
    }


def test_phrases_load_maps_fields(monkeypatch):
    _install(monkeypatch, [
        _phrase_page("p1", "Play it by ear.", meaning="再看情況決定吧",
                     note="口語常用", appeared=2, due="2026-08-01"),
    ])

    out = notion_db.phrases_load("英文")

    assert out == [{
        "page_id": "p1",
        "sentence": "Play it by ear.",
        "meaning": "再看情況決定吧",
        "note": "口語常用",
        "appeared": 2,
        "due": "2026-08-01",
    }]


def test_phrases_load_defaults_missing_count_to_zero(monkeypatch):
    """使用者手貼的句子不會填「出現次數」,讀回來必須是 0 不是 None。"""
    _install(monkeypatch, [_phrase_page("p1", "hi")])

    out = notion_db.phrases_load("英文")

    assert out[0]["appeared"] == 0
    assert out[0]["due"] is None


def test_phrases_load_paginates(monkeypatch):
    """Notion 單頁上限 100。只查一次就回,limit 傳再大也只拿得到 100 筆,
    而且不會報錯 —— 句子會安靜地少一半。
    """
    dbs, _ = _install(monkeypatch, [_phrase_page("p1", "hi")])
    calls = {"n": 0}

    def paged(**kwargs):
        dbs.queries.append(kwargs)
        calls["n"] += 1
        more = calls["n"] < 2
        return {
            "results": [_phrase_page(f"p{calls['n']}", "hi")],
            "has_more": more,
            "next_cursor": "cur" if more else None,
        }

    dbs.query = paged

    out = notion_db.phrases_load("英文")

    assert len(out) == 2
    assert dbs.queries[1]["start_cursor"] == "cur"


def test_phrases_load_survives_notion_failure(monkeypatch):
    """Notion 掛掉回空清單,不 raise —— 上層據此走 AI 補位。"""
    dbs, _ = _install(monkeypatch, [])

    def boom(**kwargs):
        raise RuntimeError("notion down")

    dbs.query = boom

    assert notion_db.phrases_load("英文") == []


def test_phrases_load_returns_empty_when_not_configured(monkeypatch):
    monkeypatch.setattr(notion_db, "_TOKEN", "")
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", "")

    assert notion_db.phrases_load("英文") == []


# ── 推進排程 ──────────────────────────────────────────────

def test_phrase_advance_writes_all_three_fields(monkeypatch):
    _, pages = _install(monkeypatch, [])

    ok = notion_db.phrase_advance("p1", {
        "appeared": 3, "last_seen": D, "due": date(2026, 10, 1),
    })

    assert ok is True
    assert pages.updated[0]["page_id"] == "p1"
    assert pages.updated[0]["properties"] == {
        "出現次數": {"number": 3},
        "上次出現": {"date": {"start": "2026-09-01"}},
        "下次出現": {"date": {"start": "2026-10-01"}},
    }


def test_phrase_advance_survives_notion_failure(monkeypatch):
    """寫不回去只是排程沒推進,信已經寄了 —— 不能因此炸掉整封。"""
    _, pages = _install(monkeypatch, [])

    def boom(**kwargs):
        raise RuntimeError("notion down")

    pages.update = boom

    assert notion_db.phrase_advance("p1", {
        "appeared": 1, "last_seen": D, "due": D,
    }) is False


# ── 寫回 AI 生成的句子 ────────────────────────────────────

def test_phrase_add_marks_source_and_schedules_next(monkeypatch):
    """AI 生的要進複習循環,否則生完就丟,庫永遠長不大。"""
    _, pages = _install(monkeypatch, [])

    ok = notion_db.phrase_add(
        "Me da igual.", "西班牙文",
        meaning="我都可以", note="口語",
        source="AI生成", day=D, due=date(2026, 9, 2),
    )

    props = pages.created[0]["properties"]
    assert ok is True
    assert props["句子"]["title"][0]["text"]["content"] == "Me da igual."
    assert props["語言"]["select"]["name"] == "西班牙文"
    assert props["來源"]["select"]["name"] == "AI生成"
    assert props["出現次數"]["number"] == 1
    assert props["上次出現"]["date"]["start"] == "2026-09-01"
    assert props["下次出現"]["date"]["start"] == "2026-09-02"


def test_phrase_add_refuses_empty_sentence(monkeypatch):
    """AI 回空字串時不要在庫裡留一筆空白。"""
    _, pages = _install(monkeypatch, [])

    assert notion_db.phrase_add("", "英文", day=D, due=D) is False
    assert pages.created == []


# ── 金句 ──────────────────────────────────────────────────

def test_quotes_load_maps_fields(monkeypatch):
    _install(monkeypatch, [
        _quote_page("q1", "你以為的極限", source="佚名", last_seen="2026-08-01"),
    ])

    assert notion_db.quotes_load() == [{
        "page_id": "q1",
        "sentence": "你以為的極限",
        "source": "佚名",
        "last_seen": "2026-08-01",
    }]


def test_quotes_load_keeps_unseen_as_none(monkeypatch):
    """沒講過的 last_seen 必須是 None —— phrasebook.pick_quote 靠它分類。"""
    _install(monkeypatch, [_quote_page("q1", "沒講過")])

    assert notion_db.quotes_load()[0]["last_seen"] is None


def test_quote_mark_seen_writes_date(monkeypatch):
    _, pages = _install(monkeypatch, [])

    ok = notion_db.quote_mark_seen("q1", D)

    assert ok is True
    assert pages.updated[0]["properties"] == {
        "上次出現": {"date": {"start": "2026-09-01"}},
    }


def test_phrases_load_stops_on_empty_page(monkeypatch):
    """空頁但宣稱 has_more → 不能無限迴圈。

    真實的 Notion 不會這樣回,但這段每天早上在 Railway 上跑,
    卡死比報錯難查太多。
    """
    dbs, _ = _install(monkeypatch, [])

    def always_more(**kwargs):
        return {"results": [], "has_more": True, "next_cursor": "cur"}

    dbs.query = always_more

    assert notion_db.phrases_load("英文") == []
