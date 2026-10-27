"""LINE 聊天截圖讀出的每日摘要 → 每日紀錄「今天做了什麼」（只填空白的）。"""

from datetime import date
from pathlib import Path

import owntracks

SERVER = Path(__file__).resolve().parent.parent / "server.py"


class FakeClient:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.created, self.updated = [], []
        outer = self

        class DB:
            def query(self, database_id, filter, page_size):
                day = filter["date"]["equals"]
                return {"results": [outer.rows[day]] if day in outer.rows else []}

        class Pages:
            def create(self, parent, properties):
                outer.created.append(properties)
                return {"id": "new", "properties": {}}

            def update(self, page_id, properties):
                outer.updated.append((page_id, properties))

        self.databases, self.pages = DB(), Pages()


def _row(note):
    rt = [{"plain_text": note}] if note else []
    return {"id": "r1", "properties": {"今天做了什麼": {"rich_text": rt}}}


def test_fills_empty_day(monkeypatch):
    c = FakeClient({"2026-10-26": _row("")})
    monkeypatch.setattr(owntracks.notion_db, "_get_client", lambda: c)
    assert owntracks.set_daily_note(date(2026, 10, 26), "去古坑；小螢感冒") == "written"
    pid, props = c.updated[0]
    assert pid == "r1" and props["今天做了什麼"]["rich_text"][0]["text"]["content"] == "去古坑；小螢感冒"


def test_never_overwrites_existing_note(monkeypatch):
    c = FakeClient({"2026-05-23": _row("在淡水家，下午去剪頭髮")})
    monkeypatch.setattr(owntracks.notion_db, "_get_client", lambda: c)
    assert owntracks.set_daily_note(date(2026, 5, 23), "別的摘要") == "kept"
    assert c.updated == []


def test_creates_missing_day_row(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(owntracks.notion_db, "_get_client", lambda: c)
    assert owntracks.set_daily_note(date(2026, 10, 25), "在家") == "written"
    assert c.created[0]["日期"]["date"]["start"] == "2026-10-25"
    assert c.updated[0][0] == "new"


def test_empty_summary_is_noop(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(owntracks.notion_db, "_get_client", lambda: c)
    assert owntracks.set_daily_note(date(2026, 10, 25), "  ") == "error"
    assert c.created == [] and c.updated == []


def test_endpoint_requires_token_and_refuses_today():
    src = SERVER.read_text(encoding="utf-8")
    start = src.index("async def daily_notes_upload(")
    body = src[start: src.index("\n@app.", start)]
    assert '@app.post("/admin/daily-notes")' in src
    assert 'request.headers.get("X-Admin-Token") != admin_token' in body
    assert "day >= today_tpe()" in body and "owntracks.set_daily_note" in body
