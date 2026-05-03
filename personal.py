"""
個人功能：待辦清單 + 提醒。
in-memory 儲存，Railway redeploy 會清空（v2 會搬到 Notion / Postgres）。
所有函式以 user_id 隔離（不同人各自一份清單）。
"""

import re
import threading
from datetime import datetime, timedelta


_LOCK = threading.Lock()

# user_id → list of {id, text, done, created_at}
_TODOS = {}
_TODO_NEXT_ID = {}

# user_id → list of {id, text, fire_at}
_REMINDERS = {}
_REMINDER_NEXT_ID = {}


# ════════════════════════════════════════
# 待辦清單
# ════════════════════════════════════════

def add_todo(user_id, text):
    with _LOCK:
        next_id = _TODO_NEXT_ID.get(user_id, 0) + 1
        _TODO_NEXT_ID[user_id] = next_id
        _TODOS.setdefault(user_id, []).append({
            "id": next_id, "text": text, "done": False,
            "created_at": datetime.now(),
        })
        return next_id


def list_todos(user_id):
    with _LOCK:
        return list(_TODOS.get(user_id, []))


def complete_todo(user_id, todo_id):
    with _LOCK:
        for t in _TODOS.get(user_id, []):
            if t["id"] == todo_id:
                t["done"] = True
                return True
    return False


def delete_todo(user_id, todo_id):
    with _LOCK:
        items = _TODOS.get(user_id, [])
        for i, t in enumerate(items):
            if t["id"] == todo_id:
                del items[i]
                return True
    return False


def clear_done(user_id):
    with _LOCK:
        items = _TODOS.get(user_id, [])
        before = len(items)
        _TODOS[user_id] = [t for t in items if not t["done"]]
        return before - len(_TODOS[user_id])


def format_todos(user_id):
    items = list_todos(user_id)
    if not items:
        return ("📋 目前沒有待辦事項。\n"
                "用法：\n"
                "  /待辦 加 [內容]\n"
                "  /待辦 完成 [編號]\n"
                "  /待辦 刪 [編號]\n"
                "  /待辦 清完成   ← 砍掉所有已完成")
    lines = ["<b>📋 待辦清單</b>"]
    for t in items:
        check = "✅" if t["done"] else "⬜"
        lines.append(f"  {check} [{t['id']}] {t['text']}")
    return "\n".join(lines)


# ════════════════════════════════════════
# 提醒
# ════════════════════════════════════════

# 「N 分鐘/小時/天 後 [內容]」
_RE_RELATIVE_MIN = re.compile(r"^(\d+)\s*分鐘?後\s+(.+)$")
_RE_RELATIVE_HOUR = re.compile(r"^(\d+)\s*小時?後\s+(.+)$")
_RE_RELATIVE_DAY = re.compile(r"^(\d+)\s*天後\s+(.+)$")
# 「今天/明天/後天 HH:MM [內容]」
_RE_REL_DAY_TIME = re.compile(r"^(今天|明天|後天)\s*(\d{1,2}):?(\d{2})?\s+(.+)$")


def parse_reminder_input(text):
    """嘗試把「時間 + 內容」字串解析出 (fire_at, content)。失敗回 None。"""
    if not text:
        return None
    s = text.strip()
    now = datetime.now()

    m = _RE_RELATIVE_MIN.match(s)
    if m:
        return now + timedelta(minutes=int(m.group(1))), m.group(2).strip()

    m = _RE_RELATIVE_HOUR.match(s)
    if m:
        return now + timedelta(hours=int(m.group(1))), m.group(2).strip()

    m = _RE_RELATIVE_DAY.match(s)
    if m:
        return now + timedelta(days=int(m.group(1))), m.group(2).strip()

    m = _RE_REL_DAY_TIME.match(s)
    if m:
        days_map = {"今天": 0, "明天": 1, "後天": 2}
        days = days_map[m.group(1)]
        target_date = (now + timedelta(days=days)).date()
        target_dt = datetime.combine(
            target_date,
            datetime.min.time(),
        ).replace(hour=int(m.group(2)), minute=int(m.group(3) or 0))
        if target_dt <= now:
            return None  # 過去的時間
        return target_dt, m.group(4).strip()

    return None


def add_reminder(user_id, text, fire_at, push_func):
    """登記提醒並 schedule。push_func(user_id, text) 在到時被呼叫（同步函式）。"""
    from app_state import get_scheduler
    scheduler = get_scheduler()
    if not scheduler:
        return None

    with _LOCK:
        next_id = _REMINDER_NEXT_ID.get(user_id, 0) + 1
        _REMINDER_NEXT_ID[user_id] = next_id
        _REMINDERS.setdefault(user_id, []).append({
            "id": next_id, "text": text, "fire_at": fire_at,
        })

    job_id = f"reminder-{user_id}-{next_id}"
    scheduler.add_job(
        _fire_reminder,
        "date",
        run_date=fire_at,
        args=[user_id, next_id, push_func],
        id=job_id,
        replace_existing=True,
    )
    return next_id


def _fire_reminder(user_id, reminder_id, push_func):
    """到時觸發：找出該筆內容、push 給 user、從清單移除。"""
    target = None
    with _LOCK:
        items = _REMINDERS.get(user_id, [])
        for t in items:
            if t["id"] == reminder_id:
                target = t
                break
        if target:
            items.remove(target)
    if target:
        try:
            push_func(user_id, f"⏰ 提醒：{target['text']}")
        except Exception as e:
            print(f"提醒 push 失敗 {user_id}/{reminder_id}: {e}")


def list_reminders(user_id):
    with _LOCK:
        return sorted(_REMINDERS.get(user_id, []), key=lambda x: x["fire_at"])


def cancel_reminder(user_id, reminder_id):
    from app_state import get_scheduler
    scheduler = get_scheduler()
    with _LOCK:
        items = _REMINDERS.get(user_id, [])
        for i, t in enumerate(items):
            if t["id"] == reminder_id:
                del items[i]
                if scheduler:
                    try:
                        scheduler.remove_job(f"reminder-{user_id}-{reminder_id}")
                    except Exception:
                        pass
                return True
    return False


def format_reminders(user_id):
    items = list_reminders(user_id)
    if not items:
        return ("⏰ 目前沒有提醒。\n"
                "用法：\n"
                "  /提醒 30 分鐘後 喝水\n"
                "  /提醒 2 小時後 開會\n"
                "  /提醒 明天 9:30 會議\n"
                "  /提醒 今天 18:00 倒垃圾\n"
                "  /取消提醒 [編號]")
    lines = ["<b>⏰ 進行中的提醒</b>"]
    for t in items:
        when = t["fire_at"].strftime("%m/%d %H:%M")
        lines.append(f"  [{t['id']}] {when} → {t['text']}")
    return "\n".join(lines)
