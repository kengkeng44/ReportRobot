# 山屋空位存 Notion＋儀表板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每次山屋檢查把空位寫進 Notion（最新狀態＋變化紀錄），通知只寄週五六日，並做一個讀 Notion 的 artifact 儀表板。

**Architecture:** 新檔 `hut_notion.py` 只負責 Notion 讀寫（upsert「山屋空位」、新增「山屋變化紀錄」）；`hut_monitor.run_check` 加週末過濾，結尾在背景執行緒呼叫 `hut_notion.sync`；開機先用 `hut_notion.load_rows` 補回快照。儀表板是 claude.ai artifact，用 `mcp` capability 透過檢視者的 Notion connector 讀兩個專用 view。

**Tech Stack:** Python 3、notion-client（`notion_db._get_client()`）、pytest、APScheduler；artifact 用原生 HTML/JS。

Spec：`docs/superpowers/specs/2026-11-03-hut-dashboard-notion-design.md`

工作目錄：`C:\Users\acer\projects\ReportRobot-hut`（git worktree，分支 `feat/hut-monitor`）。git 一律用 `git -C <path>`，不要 `cd`。測試指令：`python -m pytest tests/<file> -q`。

---

## 檔案

| 檔案 | 動作 | 責任 |
|---|---|---|
| `notion_db.py` | 改 | `_SECTIONS` 加「山屋」區，`_SCHEMAS` 加兩張表 |
| `hut_notion.py` | 新 | 組 Notion 欄位、`load_rows`、`sync` |
| `hut_monitor.py` | 改 | 週末過濾、背景同步、失敗計數、`restore_from_notion` |
| `server.py` | 改 | 開機那班先 `restore_from_notion` 再檢查 |
| `tests/test_hut_notion.py` | 新 | 假 Notion client 測 `hut_notion` |
| `tests/test_hut_monitor.py` | 改 | 週末過濾、背景同步、還原快照 |
| `tests/test_notion_schema.py` | 改 | 山屋兩張表在「山屋」區 |

Task 1–4 是程式（可派子 agent）。Task 5–8 要用 Artifact 工具、Notion connector、push 權限，由主 session 自己做。

---

### Task 1: Notion schema

**Files:**
- Modify: `notion_db.py`（`_SECTIONS` 約第 39–64 行；`_SCHEMAS` 字典結尾）
- Test: `tests/test_notion_schema.py`

- [ ] **Step 1: 寫失敗測試**（加在 `tests/test_notion_schema.py` 檔尾）

```python
def test_hut_tables_live_in_hut_section():
    assert notion_db._DB_SECTION["山屋空位"] == "山屋"
    assert notion_db._DB_SECTION["山屋變化紀錄"] == "山屋"
    slots = notion_db._SCHEMAS["山屋空位"]
    assert slots["鍵"] == {"title": {}}
    for name in ("地點", "單位", "類型", "住宿日", "星期", "週末", "剩餘", "報名", "更新時間", "備註"):
        assert name in slots
    log = notion_db._SCHEMAS["山屋變化紀錄"]
    assert log["摘要"] == {"title": {}}
    for name in ("地點", "單位", "住宿日", "週末", "之前", "之後", "變化時間"):
        assert name in log
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_notion_schema.py::test_hut_tables_live_in_hut_section -q`
Expected: FAIL，`KeyError: '山屋空位'`

- [ ] **Step 3: 實作**

`_SECTIONS` 在「定位」那段之後加：

```python
    # 山屋空位監控（hut_monitor 每次檢查寫入）。獨立一區，之後拿來分析各山屋釋出規律。
    "山屋": {
        "icon": "⛰️",
        "dbs": ("山屋空位", "山屋變化紀錄"),
    },
```

`_SCHEMAS` 字典最後一個 key 之後加（放在結尾 `}` 之前）：

```python
    # 山屋空位：每個「地點×單位×住宿日」一列＋每站一列站況，upsert。
    # 過期的列不刪，留著分析；讀取時用住宿日 filter 只拿今天以後的。
    # 設計：docs/superpowers/specs/2026-11-03-hut-dashboard-notion-design.md
    "山屋空位": {
        "鍵": {"title": {}},                                    # site|unit|YYYY-MM-DD；站況列是 site|站況
        "地點": _select(("嘉明湖／向陽", "blue"), ("天池", "green"), ("檜谷", "brown"),
                        ("排雲", "purple"), ("九九", "orange"), ("雪霸", "yellow"),
                        ("奇萊", "red"), ("南湖", "pink")),
        "單位": {"rich_text": {}},
        "類型": _select(("床位", "blue"), ("營地", "green"), ("站況", "gray")),
        "住宿日": {"date": {}},
        "星期": _select(("一", "default"), ("二", "default"), ("三", "default"),
                        ("四", "default"), ("五", "orange"), ("六", "red"), ("日", "red")),
        "週末": {"checkbox": {}},                                # 住宿日是週五、六、日
        "剩餘": {"number": {"format": "number"}},
        "報名": {"number": {"format": "number"}},
        "更新時間": {"date": {}},                                # 這列最後一次寫入（站況列 = 最後一次檢查）
        "備註": {"rich_text": {}},                               # 站況列：「正常」或錯誤訊息
    },
    # 剩餘數有變才新增一列，只增不改。
    "山屋變化紀錄": {
        "摘要": {"title": {}},                                   # 例「九九 11/21(五) 0→2」
        "地點": _select(("嘉明湖／向陽", "blue"), ("天池", "green"), ("檜谷", "brown"),
                        ("排雲", "purple"), ("九九", "orange"), ("雪霸", "yellow"),
                        ("奇萊", "red"), ("南湖", "pink")),
        "單位": {"rich_text": {}},
        "住宿日": {"date": {}},
        "週末": {"checkbox": {}},
        "之前": {"number": {"format": "number"}},
        "之後": {"number": {"format": "number"}},
        "變化時間": {"date": {}},
    },
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_notion_schema.py -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot-hut add notion_db.py tests/test_notion_schema.py
git -C C:/Users/acer/projects/ReportRobot-hut commit -m "notion_db：加「山屋」區與山屋空位、山屋變化紀錄兩張表"
```

---

### Task 2: `hut_notion.py`

**Files:**
- Create: `hut_notion.py`
- Test: `tests/test_hut_notion.py`

已知 helper（`notion_db.py` 裡，直接用）：
- `notion_db.is_configured()`、`notion_db._get_client()`、`notion_db.get_or_create_db(name)` → db_id 或 None
- `notion_db._query_all(db_id, client, limit, **extra)` → 分頁撈 page 清單，`extra` 會原樣傳給 `client.databases.query`（例如 `filter=...`）
- `notion_db._read_title(props, name)`、`notion_db._read_number(props, name)`
- `hut_info.place(site)` → 「九九」等顯示名；`hut_calendar.WEEKDAY = "一二三四五六日"`；`hut_calendar.fmt_day(d)` → `11/21(五)`

- [ ] **Step 1: 寫失敗測試** `tests/test_hut_notion.py`

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_hut_notion.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'hut_notion'`

- [ ] **Step 3: 實作** `hut_notion.py`

```python
"""山屋空位寫進 Notion：「山屋空位」upsert 最新狀態，「山屋變化紀錄」只增不改。

只在剩餘或報名數變了才寫那一列（Notion 每秒約 3 筆，300 列全重寫太慢）。
過期的列不刪，讀取時用住宿日 filter 只拿今天以後的，資料再多也不會變慢。
新出現的列不寫變化紀錄：第一次同步會一次建 300 列，那不是「變化」。

設計：docs/superpowers/specs/2026-11-03-hut-dashboard-notion-design.md
"""

import time

import hut_info
import notion_db
from hut_calendar import WEEKDAY, fmt_day

SLOTS_DB = "山屋空位"
LOG_DB = "山屋變化紀錄"
STATUS = "站況"
WEEKEND = frozenset({4, 5, 6})  # 週五、六、日的 weekday()
KIND = {"bed": "床位", "tent": "營地"}
WRITE_GAP = 0.35  # 秒；Notion 寫入上限約每秒 3 筆


def slot_key(s):
    return f"{s.site}|{s.unit}|{s.day.isoformat()}"


def status_key(site):
    return f"{site}|{STATUS}"


def _title(v):
    return {"title": [{"text": {"content": v}}]}


def _text(v):
    return {"rich_text": [{"text": {"content": v[:2000]}}]}


def _select(v):
    return {"select": {"name": v}}


def _when(dt):
    return {"date": {"start": dt.isoformat()}}


def slot_props(s, now):
    return {
        "鍵": _title(slot_key(s)),
        "地點": _select(hut_info.place(s.site)),
        "單位": _text(s.unit),
        "類型": _select(KIND.get(s.kind, s.kind)),
        "住宿日": _when(s.day),
        "星期": _select(WEEKDAY[s.day.weekday()]),
        "週末": {"checkbox": s.day.weekday() in WEEKEND},
        "剩餘": {"number": s.remaining},
        "報名": {"number": s.applicants},
        "更新時間": _when(now),
    }


def status_props(site, now, error=None):
    return {
        "鍵": _title(status_key(site)),
        "地點": _select(hut_info.place(site)),
        "類型": _select(STATUS),
        "更新時間": _when(now),
        "備註": _text(error or "正常"),
    }


def change_props(s, before, now):
    return {
        "摘要": _title(f"{hut_info.place(s.site)} {fmt_day(s.day)} {before}→{s.remaining}"),
        "地點": _select(hut_info.place(s.site)),
        "單位": _text(s.unit),
        "住宿日": _when(s.day),
        "週末": {"checkbox": s.day.weekday() in WEEKEND},
        "之前": {"number": before},
        "之後": {"number": s.remaining},
        "變化時間": _when(now),
    }


def load_rows(today):
    """{鍵: (page_id, 剩餘, 報名)}，只含住宿日 ≥ today 的列和站況列。Notion 沒設定回 None。"""
    if not notion_db.is_configured():
        return None
    client = notion_db._get_client()
    db_id = notion_db.get_or_create_db(SLOTS_DB)
    if not client or not db_id:
        return None
    flt = {"or": [{"property": "住宿日", "date": {"on_or_after": today.isoformat()}},
                  {"property": "類型", "select": {"equals": STATUS}}]}
    rows = {}
    for page in notion_db._query_all(db_id, client, 5000, filter=flt):
        props = page.get("properties", {})
        key = notion_db._read_title(props, "鍵")
        if key:
            rows[key] = (page["id"], notion_db._read_number(props, "剩餘"),
                         notion_db._read_number(props, "報名"))
    return rows


def sync(slots, errors, now, pause=time.sleep):
    """把這次檢查寫進 Notion。回 {"created", "updated", "changes"}；Notion 沒設定回 None。

    失敗直接 raise，由 hut_monitor 計數、連續 3 次才通知。寫到一半斷掉沒關係：
    下次 load_rows 重新比對，沒寫到的會補上。
    """
    if not notion_db.is_configured():
        return None
    client = notion_db._get_client()
    slots_db = notion_db.get_or_create_db(SLOTS_DB)
    log_db = notion_db.get_or_create_db(LOG_DB)
    if not client or not slots_db or not log_db:
        raise RuntimeError("山屋 Notion 表拿不到（client 或 DB 是 None）")
    rows = load_rows(now.date()) or {}
    out = {"created": 0, "updated": 0, "changes": 0}

    def write(fn, **kw):
        fn(**kw)
        pause(WRITE_GAP)

    for s in slots:
        old = rows.get(slot_key(s))
        if old is None:
            write(client.pages.create, parent={"database_id": slots_db}, properties=slot_props(s, now))
            out["created"] += 1
            continue
        page_id, before, applicants = old
        if before == s.remaining and applicants == s.applicants:
            continue
        write(client.pages.update, page_id=page_id, properties=slot_props(s, now))
        out["updated"] += 1
        if before != s.remaining:
            write(client.pages.create, parent={"database_id": log_db},
                  properties=change_props(s, before, now))
            out["changes"] += 1

    for site in sorted({s.site for s in slots} | set(errors)):
        props = status_props(site, now, errors.get(site))
        old = rows.get(status_key(site))
        if old:
            write(client.pages.update, page_id=old[0], properties=props)
        else:
            write(client.pages.create, parent={"database_id": slots_db}, properties=props)
    return out
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_hut_notion.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot-hut add hut_notion.py tests/test_hut_notion.py
git -C C:/Users/acer/projects/ReportRobot-hut commit -m "hut_notion：山屋空位 upsert、變化紀錄、站況列"
```

---

### Task 3: `hut_monitor` 週末過濾＋背景同步

**Files:**
- Modify: `hut_monitor.py`（import 區、常數區、`run_check`）
- Test: `tests/test_hut_monitor.py`

既有測試的日期 `SAT`（12/5 六）、`FRI`（11/27 五）、`date(2026, 12, 6)`（日）都是週末，不受過濾影響。

- [ ] **Step 1: 寫失敗測試**

把 `tests/test_hut_monitor.py` 的 autouse fixture 改成（多一行擋掉真的背景同步）：

```python
@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    hm.reset()
    monkeypatch.delenv("HUT_FOREST_ON_SERVER", raising=False)
    monkeypatch.setattr(hm, "_sync_in_background", lambda *a: None)
    yield
    hm.reset()
```

檔尾加：

```python
THU = date(2026, 12, 3)  # 31 天後，週四


def test_weekday_increase_not_mailed_and_not_rereported():
    _run([_s("tconline", "天池山莊", THU, 0)])
    out, sent, _ = _run([_s("tconline", "天池山莊", THU, 3)])
    assert out["changes"] == 0 and sent == []
    out, sent, _ = _run([_s("tconline", "天池山莊", THU, 3)])
    assert out["changes"] == 0 and sent == []


def test_weekend_increase_still_mailed_with_weekday_mixed():
    _run([_s("tconline", "天池山莊", THU, 0), _s("tconline", "天池山莊", SAT, 0)])
    out, sent, _ = _run([_s("tconline", "天池山莊", THU, 3), _s("tconline", "天池山莊", SAT, 2)])
    assert out["changes"] == 1
    assert "12/5(六)" in sent[0][0]


def test_sync_called_with_all_slots_and_errors():
    calls = []
    slots = [_s("tconline", "天池山莊", THU, 1)]
    hm.run_check(now=NOW, fetch=lambda today: (list(slots), {"jmlnt": "HTTP 500"}),
                 send=lambda s, t: True, notify=lambda e, c: None,
                 sync=lambda sl, er, now, notify: calls.append((sl, er, now)))
    sl, er, now = calls[0]
    assert sl == slots and er["jmlnt"] == "HTTP 500" and now == NOW


def test_notion_job_alerts_on_third_failure(monkeypatch):
    import hut_notion
    def boom(*a, **k):
        raise RuntimeError("notion down")
    monkeypatch.setattr(hut_notion, "sync", boom)
    alerts = []
    for _ in range(3):
        hm._notion_job([], {}, NOW, lambda e, c: alerts.append(c["section"]))
    assert alerts == ["notion"]


def test_notion_job_success_resets_count(monkeypatch):
    import hut_notion
    results = iter([RuntimeError("x"), RuntimeError("x"), None, RuntimeError("x"), RuntimeError("x")])
    def flaky(*a, **k):
        r = next(results)
        if r:
            raise r
    monkeypatch.setattr(hut_notion, "sync", flaky)
    alerts = []
    for _ in range(5):
        hm._notion_job([], {}, NOW, lambda e, c: alerts.append(c["section"]))
    assert alerts == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_hut_monitor.py -q`
Expected: 新測試 FAIL（`AttributeError: ... '_sync_in_background'` 或 `TypeError: unexpected keyword argument 'sync'`）

- [ ] **Step 3: 實作**

`hut_monitor.py` docstring 第三段後加一行：

```
寄信只寄住宿日是週五、六、日的增加；全部空位（含平日）寫進 Notion（hut_notion，背景執行緒）。
```

import 區加 `import hut_notion`（放在 `import hut_onestop` 後）。

常數區（`MISSING = ...` 下面）加：

```python
WEEKEND = hut_notion.WEEKEND
_NOTION_BUSY = threading.Lock()  # 上一次同步還沒寫完（第一次約 2 分鐘）就跳過這次
```

`run_check` 簽名改成：

```python
def run_check(send_events_today=False, now=None, fetch=None, send=None, notify=None, sync=None):
```

函式開頭 `notify = notify or _notify` 下面加：

```python
    sync = sync or _sync_in_background
```

把 `changes = _diff(slots, today)` 改成：

```python
        # 平日的增加只記 Notion 不寄信；快照照樣更新，下次不會重報
        changes = [c for c in _diff(slots, today) if c.slot.day.weekday() in WEEKEND]
```

`for site in alerts: notify(...)` 那段之後、`sent = False` 之前加：

```python
    sync(slots, errors, now, notify)
```

在 `run_check` 上方加兩個函式：

```python
def _notion_job(slots, errors, now, notify):
    """寫 Notion；失敗跟抓取失敗一樣連續 3 次才通知。"""
    try:
        hut_notion.sync(slots, errors, now)
    except Exception as e:
        with _LOCK:
            _fails["notion"] = _fails.get("notion", 0) + 1
            n = _fails["notion"]
        print(f"[hut_monitor] Notion 寫入失敗（第 {n} 次）：{type(e).__name__}: {e}")
        if n == FAIL_ALERT:
            notify(e, {"module": "hut_monitor", "section": "notion"})
        return
    with _LOCK:
        _fails["notion"] = 0


def _sync_in_background(slots, errors, now, notify):
    def work():
        if not _NOTION_BUSY.acquire(blocking=False):
            print("[hut_monitor] 上一次 Notion 同步還沒寫完，這次跳過")
            return
        try:
            _notion_job(slots, errors, now, notify)
        finally:
            _NOTION_BUSY.release()
    threading.Thread(target=work, daemon=True, name="hut-notion").start()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_hut_monitor.py tests/test_hut_report.py tests/test_hut_server.py tests/test_hut_command.py -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot-hut add hut_monitor.py tests/test_hut_monitor.py
git -C C:/Users/acer/projects/ReportRobot-hut commit -m "hut_monitor：只寄週五六日的增加，每次檢查背景寫 Notion"
```

---

### Task 4: 開機從 Notion 補回快照

**Files:**
- Modify: `hut_monitor.py`（新增 `restore_from_notion`）
- Modify: `server.py:416-421`（開機那班）
- Test: `tests/test_hut_monitor.py`

- [ ] **Step 1: 寫失敗測試**（`tests/test_hut_monitor.py` 檔尾）

```python
def test_restore_from_notion_then_first_run_reports():
    rows = {"tconline|天池山莊|2026-12-05": ("p1", 0, 5),
            "tconline|站況": ("p2", None, None),
            "onestop_99|九九山莊|2026-12-05": ("p3", None, 3)}
    n = hm.restore_from_notion(today=TODAY, load=lambda today: rows)
    assert n == 1
    out, sent, _ = _run([_s("tconline", "天池山莊", SAT, 3)])
    assert out["changes"] == 1 and sent


def test_restore_skipped_when_snapshot_exists():
    _run([_s("tconline", "天池山莊", SAT, 0)])
    n = hm.restore_from_notion(today=TODAY, load=lambda today: {"tconline|天池山莊|2026-12-05": ("p", 9, 1)})
    assert n == 0
    assert hm._snapshot[("tconline", "天池山莊", "2026-12-05")] == 0


def test_restore_failure_falls_back_to_baseline():
    def boom(today):
        raise RuntimeError("notion down")
    assert hm.restore_from_notion(today=TODAY, load=boom) == 0
    out, sent, _ = _run([_s("tconline", "天池山莊", SAT, 3)])
    assert out["changes"] == 0  # 照舊第一次只建基準
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_hut_monitor.py -q -k restore`
Expected: FAIL，`AttributeError: module 'hut_monitor' has no attribute 'restore_from_notion'`

- [ ] **Step 3: 實作**

`hut_monitor.py` docstring 第二段「狀態只放記憶體…」改成：

```
狀態放記憶體；開機先 restore_from_notion 從 Notion 補回快照，重新部署後第一次檢查就能比對。
Notion 讀不到才退回舊行為：第一次檢查當基準、不寄信。
```

在 `_notion_job` 上方加：

```python
def restore_from_notion(today=None, load=None):
    """快照是空的時候從 Notion「山屋空位」補回。回傳補回的筆數；失敗回 0。"""
    today = today or today_tpe()
    load = load or hut_notion.load_rows
    try:
        rows = load(today) or {}
    except Exception as e:
        print(f"[hut_monitor] 從 Notion 補快照失敗，照舊建基準：{type(e).__name__}: {e}")
        return 0
    n = 0
    with _LOCK:
        if _snapshot:
            return 0
        for key, (_, remaining, _) in rows.items():
            site, _, rest = key.partition("|")
            unit, _, day = rest.rpartition("|")
            if not unit or remaining is None:   # 站況列、看不出剩餘的列
                continue
            _snapshot[(site, unit, day)] = int(remaining)
            _baselined.add(site)
            n += 1
    return n
```

`server.py` 開機那班（目前 `scheduler.add_job(_run_hut_check, DateTrigger(...), id="hut_check_boot", ...)`）改成：

```python
    def _run_hut_boot():
        try:
            import hut_monitor
            print(f"[hut] 從 Notion 補回 {hut_monitor.restore_from_notion()} 筆快照")
        except Exception as e:
            print(f"[hut] 補快照失敗（非致命）：{e}")
        _run_hut_check()

    scheduler.add_job(
        _run_hut_boot,
        DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(minutes=3)),
        id="hut_check_boot",
        replace_existing=True,
    )
```

並把上方註解「開機 3 分鐘後先跑一次當基準（不寄信）」改成「開機 3 分鐘後先從 Notion 補快照再檢查（補不到才當基準、不寄信）」。

- [ ] **Step 4: 跑全部山屋與 Notion 測試**

Run: `python -m pytest tests/test_hut_monitor.py tests/test_hut_notion.py tests/test_hut_server.py tests/test_notion_schema.py tests/test_hut_report.py tests/test_hut_info.py -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot-hut add hut_monitor.py server.py tests/test_hut_monitor.py
git -C C:/Users/acer/projects/ReportRobot-hut commit -m "hut_monitor：開機從 Notion 補回快照，重新部署不必再建基準"
```

---

### Task 5: 安裝 frontend-design skill（主 session）

- [ ] **Step 1:** WebFetch `https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md`，讀完全文，確認只是設計指引、沒有要執行的腳本或外部下載。
- [ ] **Step 2:** 寫到 `C:\Users\acer\.claude\skills\frontend-design\SKILL.md`（內容原樣；若同目錄有 LICENSE 也一起放）。
- [ ] **Step 3:** 確認 skill 清單出現 `frontend-design`（新 session 才會載入；本 session 直接照檔案內容做）。

---

### Task 6: 部署＋建表（主 session，push 要先問使用者）

- [ ] **Step 1:** 跑全部測試 `python -m pytest tests -q`，全過才繼續。
- [ ] **Step 2:** 問使用者是否把 feat/hut-monitor（含 c59a7a7、02eca8b 與本計畫的 commit）併進 main 並 push（Railway 會自動部署）。
- [ ] **Step 3:** 同意後 `git -C C:/Users/acer/projects/ReportRobot merge --ff-only feat/hut-monitor`（不能 ff 就停下回報），push main。
- [ ] **Step 4:** 等部署完（約 3 分鐘＋開機檢查），用 Notion connector `notion-search` 找「山屋空位」，確認有列、站況列的更新時間是剛剛。沒有就看 Railway log（`[hut]`、`[hut_monitor]` 開頭）。

---

### Task 7: Notion 專用 view（主 session）

- [ ] **Step 1:** 讀 `notion://docs/view-dsl-spec`（Notion fetch），確認 FILTER 的日期相對值與 select 語法。
- [ ] **Step 2:** `notion-fetch`「山屋空位」拿 database_id 與 `collection://` data_source_id。
- [ ] **Step 3:** `notion-create-view` 建兩個 table view（加在 database 上，不動使用者自己的 view）：
  - 「儀表板-空位」：住宿日 ≥ 今天、週末 = 勾，依住宿日升冪。
  - 「儀表板-站況」：類型 = 站況。
- [ ] **Step 4:** 用 `notion-query-data-sources` 的 `{"data":{"mode":"view","view_url":...}}` 各查一次，確認回傳格式（欄位名稱、日期與數字長什麼樣），記下來給 Task 8 用。

---

### Task 8: 儀表板 artifact（主 session）

- [ ] **Step 1:** 讀 `artifact-design` skill、`frontend-design` SKILL.md、memory「介面不要有 AI 感」清單，以及 `artifact-capabilities/0.2.96/claude.d.ts`、`mcp.d.ts`。
- [ ] **Step 2:** 以 `C:\Users\acer\AppData\Local\Temp\claude\C--Users-acer\3b062ed2-aac7-405d-8813-df36d3e47ca8\scratchpad\hut-transport.html` 的交通資料陣列 `P` 為交通區塊來源，新檔 `...\scratchpad\hut-dashboard.html`：
  - 資料：`const mcp = await claude.use("mcp")`；`mcp.watchTool("Notion", "notion-query-data-sources", {data:{mode:"view", view_url: VIEW_SLOTS, page_size:100}}, handler)` 與站況 view 各一個；`has_more` 時用 `callTool` 帶 `start_cursor` 翻頁。實際方法名與參數以 `mcp.d.ts` 為準。
  - `mcp` 是 null 或出錯：頁首顯示「讀不到 Notion：原因」，交通區照常。
  - 頁首：資料更新時間（站況列最大的更新時間）、各站正常／失敗；超過 12 小時標「過期」。
  - 每個地點一張卡：未來 8 週的週五、六、日格子（數字＝剩餘、有空位上色、抽籤站嘉明湖／檜谷／排雲標「抽籤前」），下面接交通手冊。
- [ ] **Step 3:** 發布：`Artifact` publish，`icon: "mountain"`，`capabilities: {mcp: {servers: [{server: "claude_ai_Notion", tools: ["notion-query-data-sources"]}]}}`；依結果回報的 display name 校正頁面裡的 server 名稱，不一致就再發布一次。
- [ ] **Step 4:** Chrome 開 artifact URL，`get_page_text` 確認空位數字與更新時間有出來；截一張圖（存檔）看外觀。
- [ ] **Step 5:** 更新 memory `project_hut_monitor.md`（儀表板 URL、view URL、週末通知、Notion 兩張表），MEMORY.md 那行同步。
