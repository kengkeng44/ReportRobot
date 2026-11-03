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
