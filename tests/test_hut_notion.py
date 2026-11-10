from datetime import date, datetime, timedelta, timezone

import pytest

import hut_notion as hn
import notion_db
from hut_sources import Slot

TPE = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 3, 16, 30, tzinfo=TPE)
FRI = date(2026, 11, 27)
THU = date(2026, 11, 26)


class FakePages:
    def __init__(self, store):
        self.store = store          # page_id -> {"db": db_id, "properties": {...}}
        self.creates, self.updates = [], []

    def create(self, parent, properties):
        pid = f"p{len(self.store) + 1}"
        self.store[pid] = {"db": parent["database_id"], "properties": properties}
        self.creates.append((parent["database_id"], properties))
        return {"id": pid}

    def update(self, page_id, properties):
        self.store[page_id]["properties"].update(properties)
        self.updates.append((page_id, properties))
        return {"id": page_id}


def _as_read(props):
    """把寫入格式轉成 Notion 讀回來的格式（title 要 plain_text）。"""
    out = dict(props)
    if "鍵" in props:
        text = props["鍵"]["title"][0]["text"]["content"]
        out["鍵"] = {"title": [{"plain_text": text}]}
    if "備註" in props:
        text = props["備註"]["rich_text"][0]["text"]["content"]
        out["備註"] = {"rich_text": [{"plain_text": text}]}
    return out


class FakeDatabases:
    def __init__(self, store):
        self.store = store
        self.filters = []

    def query(self, database_id, page_size, filter=None, start_cursor=None):
        self.filters.append(filter)
        rows = [{"id": pid, "properties": _as_read(p["properties"])}
                for pid, p in self.store.items() if p["db"] == database_id]
        return {"results": rows, "has_more": False}


class FakeClient:
    def __init__(self):
        self.store = {}
        self.pages = FakePages(self.store)
        self.databases = FakeDatabases(self.store)


@pytest.fixture
def client(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(notion_db, "is_configured", lambda: True)
    monkeypatch.setattr(notion_db, "_get_client", lambda: c)
    monkeypatch.setattr(notion_db, "get_or_create_db",
                        lambda name: {"山屋空位": "db_slots", "山屋變化紀錄": "db_log"}[name])
    return c


def _s(day, remaining, applicants=5, site="onestop_99", unit="九九山莊"):
    return Slot(site, unit, "bed", day, remaining, applicants)


def _sync(slots, errors=None, now=NOW):
    return hn.sync(slots, errors or {}, now, pause=lambda s: None)


def _log_rows(client):
    return [p for db, p in client.pages.creates if db == "db_log"]


def test_first_sync_creates_rows_and_status_without_log(client):
    out = _sync([_s(FRI, 0), _s(THU, 2)], {"jmlnt": "HTTP 500"})
    assert out == {"created": 2, "updated": 0, "changes": 0}
    keys = [p["鍵"]["title"][0]["text"]["content"] for db, p in client.pages.creates]
    assert "onestop_99|九九山莊|2026-11-27" in keys
    assert "onestop_99|站況" in keys and "jmlnt|站況" in keys
    fri = next(p for db, p in client.pages.creates if db == "db_slots"
               and p["鍵"]["title"][0]["text"]["content"].endswith("2026-11-27"))
    assert fri["地點"] == {"select": {"name": "九九"}}
    assert fri["星期"] == {"select": {"name": "五"}}
    assert fri["週末"] == {"checkbox": True}
    assert fri["更新時間"] == {"date": {"start": NOW.isoformat()}}
    err = next(p for db, p in client.pages.creates
               if p["鍵"]["title"][0]["text"]["content"] == "jmlnt|站況")
    assert err["備註"]["rich_text"][0]["text"]["content"] == "HTTP 500"
    assert _log_rows(client) == []


def test_unchanged_row_not_written(client):
    _sync([_s(FRI, 0)])
    client.pages.updates.clear()
    out = _sync([_s(FRI, 0)])
    assert out == {"created": 0, "updated": 0, "changes": 0}
    # 只有站況列被更新
    assert len(client.pages.updates) == 1


def test_remaining_change_updates_and_logs(client):
    _sync([_s(FRI, 0)])
    out = _sync([_s(FRI, 2)])
    assert out == {"created": 0, "updated": 1, "changes": 1}
    log = _log_rows(client)[0]
    assert log["摘要"]["title"][0]["text"]["content"] == "九九 11/27(五) 0→2"
    assert log["之前"] == {"number": 0} and log["之後"] == {"number": 2}
    assert log["週末"] == {"checkbox": True}


def test_applicants_only_change_updates_without_log(client):
    _sync([_s(FRI, 0, applicants=5)])
    out = _sync([_s(FRI, 0, applicants=9)])
    assert out == {"created": 0, "updated": 1, "changes": 0}
    assert _log_rows(client) == []


def test_status_row_says_ok(client):
    _sync([_s(FRI, 0)])
    ok = next(p for db, p in client.pages.creates
              if p["鍵"]["title"][0]["text"]["content"] == "onestop_99|站況")
    assert ok["類型"] == {"select": {"name": "站況"}}
    assert ok["備註"]["rich_text"][0]["text"]["content"] == "正常"
    assert "住宿日" not in ok


def test_load_rows_filters_future_or_status(client):
    _sync([_s(FRI, 3)])
    rows = hn.load_rows(NOW.date())
    assert rows["onestop_99|九九山莊|2026-11-27"][1:] == (3, 5)
    flt = client.databases.filters[-1]
    assert {"property": "住宿日", "date": {"on_or_after": "2026-11-03"}} in flt["or"]
    assert {"property": "類型", "select": {"equals": "站況"}} in flt["or"]


def test_not_configured_returns_none(monkeypatch):
    monkeypatch.setattr(notion_db, "is_configured", lambda: False)
    assert hn.sync([_s(FRI, 1)], {}, NOW) is None
    assert hn.load_rows(NOW.date()) is None


def test_tables_missing_raises(monkeypatch, client):
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: None)
    with pytest.raises(RuntimeError):
        _sync([_s(FRI, 1)])


def test_load_failing_sites_only_returns_broken(client):
    _sync([_s(FRI, 3)], {"jmlnt": "HTTPError: 500"})
    assert hn.load_failing_sites() == {"jmlnt": "HTTPError: 500"}
    assert client.databases.filters[-1] == {"property": "類型", "select": {"equals": "站況"}}


def test_load_failing_sites_not_configured(monkeypatch):
    monkeypatch.setattr(notion_db, "is_configured", lambda: False)
    assert hn.load_failing_sites() is None
