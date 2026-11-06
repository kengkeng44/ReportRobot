"""TimeTree 行程手動匯入 Notion「TimeTree 行程」表（共同記帳底下）。

使用者的 TimeTree 是 Google 登入、沒有密碼，機器人自己登不進去。
改成：Claude 用 Claude in Chrome 在使用者已登入的分頁讀 API，整理成精簡 rows
（tt_rows.json），POST 到 /admin/timetree-import，這裡用「TimeTree ID」upsert。

row 格式（Claude 端產生）：
  t 標題、c 行事曆名、id、u 更新日 YYYY-MM-DD、a=1 整天、s 開始、e 結束（可無）、
  l 地點、n 備註、r 重複規則（RRULE 用空白串起來）
"""

import os
import threading
import time

TIMETREE_DB_ID = os.environ.get("TIMETREE_DB_ID", "ac9b55d4fccb4e7aaf4e6557750722b1")

_status = {"state": "idle"}
_lock = threading.Lock()


def _text(v):
    return {"rich_text": [{"text": {"content": v[:2000]}}]} if v else {"rich_text": []}


def row_to_props(r):
    date = {"start": r["s"]}
    if r.get("e"):
        date["end"] = r["e"]
    return {
        "標題": {"title": [{"text": {"content": (r.get("t") or "（沒有標題）")[:2000]}}]},
        "日期": {"date": date},
        "整天": {"checkbox": bool(r.get("a"))},
        "行事曆": {"select": {"name": r["c"]}} if r.get("c") else {"select": None},
        "地點": _text(r.get("l")),
        "備註": _text(r.get("n")),
        "重複": _text(r.get("r")),
        "TimeTree ID": _text(r["id"]),
        "TimeTree 更新時間": {"date": {"start": r["u"]}} if r.get("u") else {"date": None},
    }


def _existing(client):
    """TimeTree ID → (page_id, 更新日)。整張表撈一次，不逐筆查。"""
    out, cursor = {}, None
    while True:
        kw = {"database_id": TIMETREE_DB_ID, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        resp = client.databases.query(**kw)
        for p in resp["results"]:
            props = p["properties"]
            rt = props.get("TimeTree ID", {}).get("rich_text") or []
            tid = "".join(x.get("plain_text", "") for x in rt)
            upd = (props.get("TimeTree 更新時間", {}).get("date") or {}).get("start")
            if tid:
                out[tid] = (p["id"], upd)
        if not resp.get("has_more"):
            return out
        cursor = resp["next_cursor"]


def upsert(rows, client=None, sleep=0.35):
    """回 {created, updated, unchanged, failed}。更新日沒變的不動（重抓省 API）。"""
    import notion_db
    client = client or notion_db._get_client()
    if client is None:
        raise RuntimeError("Notion 沒設定")
    have = _existing(client)
    stats = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    for r in rows:
        try:
            hit = have.get(r["id"])
            if hit and hit[1] == r.get("u"):
                stats["unchanged"] += 1
                continue
            props = row_to_props(r)
            if hit:
                client.pages.update(page_id=hit[0], properties=props)
                stats["updated"] += 1
            else:
                client.pages.create(parent={"database_id": TIMETREE_DB_ID}, properties=props)
                stats["created"] += 1
        except Exception as e:
            stats["failed"] += 1
            print(f"[timetree_import] {r.get('id')} 失敗：{str(e)[:150]}")
        if sleep:
            time.sleep(sleep)  # Notion 約 3 req/s
    return stats


def start(rows):
    """背景跑（437 筆約 3 分鐘，超過 HTTP 逾時）。已在跑就拒絕。"""
    with _lock:
        if _status.get("state") == "running":
            return False
        _status.clear()
        _status.update({"state": "running", "total": len(rows), "started": time.time()})

    def work():
        try:
            stats = upsert(rows)
            _status.update({"state": "done", **stats})
        except Exception as e:
            _status.update({"state": "error", "error": str(e)[:200]})
        _status["seconds"] = round(time.time() - _status["started"])

    threading.Thread(target=work, daemon=True).start()
    return True


def status():
    return dict(_status)
