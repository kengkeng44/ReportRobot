"""手機桌面小工具的資料：緊急的事、各專案進度。

iPhone 的 Scriptable 小工具打 GET /widget/todos 拿這份 JSON 自己畫
（腳本在 docs/widget/todo_widget.js）。小工具不能跑網頁，所以不能直接
用儀表板，只能給它一份整理好的資料。

「緊急」＝ 優先度 P0／P1，或今天到期、已經逾期。
build() 是純函式：輸入沒做完的待辦和已完成的 (專案, 歸屬)，輸出 dict，
不碰 Notion 也就能直接測。
"""

import time

from tz_utils import now_tpe, today_tpe

# 已完成的數量只拿來算進度條，不用即時：10 分鐘查一次 Notion 就好
_DONE_CACHE = {"at": 0.0, "rows": None}
_DONE_TTL = 600

URGENT_PRIORITIES = ("P0", "P1")


def _deadline(t):
    return t.get("end") or t.get("start")


def _mine(owner, who):
    """切到某人時，看他自己的＋共同的（沒標歸屬的當共同）。"""
    if not who:
        return True
    return (owner or "共同") in (who, "共同")


def _urgent_reason(t, today):
    due = _deadline(t)
    if due and due < today:
        return f"逾期 {(today - due).days} 天"
    if due and due == today:
        return "今天"
    if t.get("priority") in URGENT_PRIORITIES:
        return t["priority"]
    return None


def build(open_todos, done_rows, today, who=None, limit=8):
    """open_todos：personal.list_todos 的項目；done_rows：[(專案, 歸屬), ...]。"""
    open_todos = [t for t in open_todos if _mine(t.get("owner"), who)]
    done_rows = [(p, o) for p, o in done_rows if _mine(o, who)]

    urgent = []
    for t in open_todos:
        reason = _urgent_reason(t, today)
        if reason:
            urgent.append((t, reason))

    def rank(item):
        t, _ = item
        due = _deadline(t)
        overdue = 0 if (due and due <= today) else 1
        prio = {"P0": 0, "P1": 1}.get(t.get("priority"), 2)
        return (overdue, prio, due or today.replace(year=today.year + 10), t["id"])

    urgent.sort(key=rank)

    stats = {}
    for t in open_todos:
        name = t.get("project")
        if not name:
            continue
        s = stats.setdefault(name, {"name": name, "open": 0, "done": 0, "urgent": 0})
        s["open"] += 1
        if _urgent_reason(t, today):
            s["urgent"] += 1
    for name, _ in done_rows:
        if name:
            stats.setdefault(name, {"name": name, "open": 0, "done": 0, "urgent": 0})["done"] += 1
    projects = [s for s in stats.values() if s["open"]]
    for s in projects:
        s["progress"] = round(s["done"] / (s["open"] + s["done"]), 2)
    projects.sort(key=lambda s: (-s["urgent"], -s["open"], s["name"]))

    return {
        "who": who or "全部",
        "updated": now_tpe().strftime("%H:%M"),
        "open": len(open_todos),
        "urgent_count": len(urgent),
        "urgent": [
            {"id": t["id"], "text": t["text"], "why": why,
             "project": t.get("project") or "", "owner": t.get("owner") or "共同"}
            for t, why in urgent[:limit]
        ],
        "projects": projects,
    }


def _done_rows(owner_key):
    import notion_db
    now = time.monotonic()
    if _DONE_CACHE["rows"] is None or now - _DONE_CACHE["at"] > _DONE_TTL:
        _DONE_CACHE["rows"] = notion_db.todos_done_counts(owner_key)
        _DONE_CACHE["at"] = now
    return _DONE_CACHE["rows"]


def payload(who=None):
    """server 用：拿共同待辦那份清單組小工具資料。"""
    import os
    import personal
    key = os.environ.get("COUPLE_GROUP_ID", "").strip() or os.environ.get(
        "ADMIN_LINE_USER_ID", "").strip()
    who = who if who in ("仁和", "小螢", "共同") else None
    return build(personal.list_todos(key), _done_rows(key), today_tpe(), who=who)
