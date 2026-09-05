"""
個人功能：待辦清單 + 提醒。

待辦：Notion 持久化（NOTION_TOKEN 設定時）+ in-memory cache 加速。
      未設 Notion 退回純 in-memory（redeploy 歸零）。
提醒：仍純 in-memory（下一輪做持久化 + APScheduler reschedule on startup）。

所有函式以 user_id 隔離。
"""

import re
import threading
from datetime import datetime, timedelta

from security_utils import mask
from tz_utils import now_tpe


_LOCK = threading.Lock()

# user_id → list of {id, text, done, created_at, page_id}
_TODOS = {}
_TODO_NEXT_ID = {}
_TODOS_LOADED_USERS = set()  # 已從 Notion load 過的 user_id

# user_id → list of {id, text, fire_at}
_REMINDERS = {}
_REMINDER_NEXT_ID = {}

# ────────────────────────────────────────
# 待辦「待命」狀態：使用者按了 ➕，下一句話就是待辦內容
#
# user_id → 按下 ➕ 的時間。**不進 Notion**：壽命只有幾秒，
# 為它多一次 API 往返不划算，Railway 重啟丟掉的代價只是重按一次。
# ────────────────────────────────────────

_PENDING_TODO = {}

# 沒有逾時的話，一個忘掉的待命狀態會把使用者隔天隨口講的
# 任何一句話變成待辦。
PENDING_TODO_TIMEOUT_MINUTES = 10


def _notion_enabled():
    try:
        import notion_db
        return notion_db.is_configured()
    except Exception:
        return False


def _ensure_todos_loaded(user_id):
    """從 Notion load 該 user 未完成待辦到 in-memory cache。每 user 只 load 一次。"""
    if user_id in _TODOS_LOADED_USERS:
        return
    if not _notion_enabled():
        _TODOS_LOADED_USERS.add(user_id)
        return
    try:
        import notion_db
        rows = notion_db.todos_load_for_user(user_id)
        with _LOCK:
            _TODOS[user_id] = []
            max_local_id = 0
            for r in rows:
                _TODOS[user_id].append({
                    "id": r["local_id"],
                    "text": r["text"],
                    "done": False,
                    "created_at": datetime.now(),
                    "page_id": r["page_id"],
                    "start": r.get("start"),
                    "end": r.get("end"),
                    "priority": r.get("priority"),
                })
                max_local_id = max(max_local_id, r["local_id"])
            _TODO_NEXT_ID[user_id] = max_local_id
            _TODOS_LOADED_USERS.add(user_id)
        if rows:
            print(f"[personal/todos] 從 Notion load {len(rows)} 筆給 {mask(user_id)}")
    except Exception as e:
        print(f"[personal/todos] load Notion 失敗：{e}")
        _TODOS_LOADED_USERS.add(user_id)


# ════════════════════════════════════════
# 待辦清單
# ════════════════════════════════════════

def start_pending_todo(user_id):
    """進入待命：下一句話當待辦內容。按兩次就重新計時，不報錯。"""
    with _LOCK:
        _PENDING_TODO[user_id] = now_tpe()


def clear_pending_todo(user_id):
    """離開待命。不在待命中也不會炸。"""
    with _LOCK:
        _PENDING_TODO.pop(user_id, None)


def is_pending_todo(user_id):
    """待命中且未逾時回 True。逾時的順手掃掉，不然 dict 會一直長。"""
    with _LOCK:
        started = _PENDING_TODO.get(user_id)
        if not started:
            return False
        if now_tpe() - started > timedelta(minutes=PENDING_TODO_TIMEOUT_MINUTES):
            _PENDING_TODO.pop(user_id, None)
            return False
        return True


def add_todo(user_id, text, start=None, end=None, priority=None):
    """新增一筆待辦，回 local id。

    start/end/priority 都是選填 —— 既有的 `/待辦 加 X` 不帶它們。
    沒給就留 None，**不補預設值**：隨手記的事被預設成今天到期，
    隔天信裡就是一行紅字。
    """
    _ensure_todos_loaded(user_id)
    with _LOCK:
        next_id = _TODO_NEXT_ID.get(user_id, 0) + 1
        _TODO_NEXT_ID[user_id] = next_id
        item = {
            "id": next_id, "text": text, "done": False,
            "created_at": datetime.now(),
            "page_id": None,
            "start": start, "end": end, "priority": priority,
        }
        _TODOS.setdefault(user_id, []).append(item)
    # Notion 寫在 lock 外（避免持鎖打網路 IO）
    if _notion_enabled():
        try:
            import notion_db
            page_id = notion_db.todos_create(
                user_id, text, next_id,
                start=start, end=end, priority=priority,
            )
            if page_id:
                with _LOCK:
                    item["page_id"] = page_id
        except Exception as e:
            print(f"[personal/todos] Notion create 失敗：{e}")
    return next_id


def set_todo_due(user_id, todo_id, start, end=None):
    """事後補上截止日（防呆按鈕走這裡）。找不到編號回 False。

    記憶體先更新再打 Notion —— 跟 add_todo 同一套：Notion 掛掉時
    使用者這一輪看到的東西仍然是對的。
    """
    _ensure_todos_loaded(user_id)
    page_id = None
    with _LOCK:
        for t in _TODOS.get(user_id, []):
            if t["id"] == todo_id:
                t["start"], t["end"] = start, end
                page_id = t.get("page_id")
                break
        else:
            return False
    if page_id and _notion_enabled():
        try:
            import notion_db
            notion_db.todos_update_fields(page_id, start=start, end=end)
        except Exception as e:
            print(f"[personal/todos] Notion 補截止日失敗：{e}")
    return True


def list_todos(user_id):
    _ensure_todos_loaded(user_id)
    with _LOCK:
        return list(_TODOS.get(user_id, []))


def complete_todo(user_id, todo_id):
    """目前 UI 設計：完成 = 直接刪除（沒有 done 待保留）。
    保留此函式做相容，內部呼叫 delete_todo。"""
    return delete_todo(user_id, todo_id)


def delete_todo(user_id, todo_id):
    _ensure_todos_loaded(user_id)
    page_id = None
    with _LOCK:
        items = _TODOS.get(user_id, [])
        for i, t in enumerate(items):
            if t["id"] == todo_id:
                page_id = t.get("page_id")
                del items[i]
                break
        else:
            return False
    if page_id and _notion_enabled():
        try:
            import notion_db
            notion_db.todos_delete(page_id)
        except Exception as e:
            print(f"[personal/todos] Notion delete 失敗：{e}")
    return True


def clear_done(user_id):
    _ensure_todos_loaded(user_id)
    with _LOCK:
        items = _TODOS.get(user_id, [])
        done_pages = [t.get("page_id") for t in items if t["done"] and t.get("page_id")]
        before = len(items)
        _TODOS[user_id] = [t for t in items if not t["done"]]
        removed = before - len(_TODOS[user_id])
    if _notion_enabled():
        try:
            import notion_db
            for pid in done_pages:
                notion_db.todos_delete(pid)
        except Exception as e:
            print(f"[personal/todos] Notion clear_done 失敗：{e}")
    return removed


def format_todos(user_id):
    items = list_todos(user_id)
    if not items:
        return ("📋 目前沒有待辦事項。\n"
                "用法：\n"
                "  /待辦 加 [內容]\n"
                "  /待辦 完成 [編號]   ← 完成會直接移除\n"
                "  /待辦 刪 [編號]\n"
                "  /待辦 清空           ← 清掉全部")
    lines = ["<b>📋 待辦清單</b>"]
    for t in items:
        lines.append(f"  ⬜ [{t['id']}] {t['text']}")
    return "\n".join(lines)


def _deadline(item):
    """待辦的截止日：end 有值就用 end，沒有就用 start。

    單日待辦（只講「明天交」）不需要被迫填兩個日期，所以 end 常常是
    None；一段期間的待辦則是 end 那天才到期。
    """
    return item.get("end") or item.get("start")


def todos_due_today(user_id, today):
    """每日信要顯示的待辦：**截止日 <= 今天 或 優先度 = P0**。

    兩個維度刻意不合併：截止日回答「什麼時候該做」，P0 回答「不管什麼
    時候都得盯著」。只用日期篩，沒設日期的重要事情會消失；只用優先度篩，
    時效性就沒了。

    排序：逾期最久的最前面 → 今天到期 → P0 且沒設日期。
    """
    out = []
    for t in list_todos(user_id):
        due = _deadline(t)
        if due and due <= today:
            out.append(t)
        elif t.get("priority") == "P0":
            out.append(t)

    def _key(t):
        due = _deadline(t)
        # 沒設日期的 P0 排最後：它沒有時效性，只是重要
        return (0, due) if due else (1, today)

    out.sort(key=_key)
    return out


def format_today_todos(user_id, today=None):
    """每日信的待辦區塊。沒東西回 None（呼叫端據此整塊不放）。

    刻意跟 format_todos 分開：那支給 LINE 的「待辦」指令用，要顯示全部。
    共用一支會讓「信裡安靜」與「查詢看得到」互相打架。
    """
    if today is None:
        today = now_tpe().date()

    items = todos_due_today(user_id, today)
    if not items:
        return None

    lines = []
    for t in items:
        due = _deadline(t)
        priority = t.get("priority")
        if due and due < today:
            mark = f"⚠️ 逾期 {(today - due).days} 天"
        elif due:
            mark = "今天"
        else:
            # 沒日期的一定是 P0（否則不會被 todos_due_today 選進來）
            mark = priority or ""
        suffix = f"（{mark}）" if mark else ""
        # 逾期 / 今天的行才另外標優先度；沒日期那行的 mark 已經是 P0 了
        if priority and due:
            suffix += f"　{priority}"
        lines.append(f"⬜ [{t['id']}] {t['text']}{suffix}")

    return chr(10).join(lines)


# ════════════════════════════════════════
# 提醒
# ════════════════════════════════════════

# 中文數字 → int（一二...十、半小時的「半」、十一、二十等較複雜的不接，要的話自己用阿拉伯數字）
_CN_NUM = {
    "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _parse_int(s):
    """阿拉伯數字 / 中文數字一~十 → int；無法解析回 None。"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    return None


# 「N 分鐘/小時/天 後 [內容]」N 接受阿拉伯或中文「一二三四五六七八九十」
_RE_RELATIVE_MIN = re.compile(r"^([一二兩三四五六七八九十\d]+)\s*分鐘?後\s+(.+)$")
_RE_RELATIVE_HOUR = re.compile(r"^([一二兩三四五六七八九十\d]+)\s*(?:個)?小時後\s+(.+)$")
_RE_RELATIVE_DAY = re.compile(r"^([一二兩三四五六七八九十\d]+)\s*天後\s+(.+)$")
# 「今天/明天/後天 HH:MM [內容]」
_RE_REL_DAY_TIME = re.compile(r"^(今天|明天|後天)\s*(\d{1,2}):?(\d{2})?\s+(.+)$")


def parse_reminder_input(text):
    """嘗試把「時間 + 內容」字串解析出 (fire_at, content)。失敗回 None。"""
    if not text:
        return None
    s = text.strip()
    # 用台北時區的 aware datetime：容器預設 UTC，用 naive datetime.now() 會讓
    # 「今天 18:00」被 APScheduler 當成 18:00 UTC(=台北凌晨2點)、且存進 Notion
    # 被硬補 +08:00 後差 8 小時。fire_at 帶 +08:00 後 UTC/台北容器都正確
    # (notion_db 也約定 fire_at 一律 tz-aware TPE)。
    now = now_tpe()

    m = _RE_RELATIVE_MIN.match(s)
    if m:
        n = _parse_int(m.group(1))
        if n:
            return now + timedelta(minutes=n), m.group(2).strip()

    m = _RE_RELATIVE_HOUR.match(s)
    if m:
        n = _parse_int(m.group(1))
        if n:
            return now + timedelta(hours=n), m.group(2).strip()

    m = _RE_RELATIVE_DAY.match(s)
    if m:
        n = _parse_int(m.group(1))
        if n:
            return now + timedelta(days=n), m.group(2).strip()

    m = _RE_REL_DAY_TIME.match(s)
    if m:
        days_map = {"今天": 0, "明天": 1, "後天": 2}
        days = days_map[m.group(1)]
        target_date = (now + timedelta(days=days)).date()
        target_dt = datetime.combine(
            target_date,
            datetime.min.time(),
            tzinfo=now.tzinfo,  # 保持 +08:00，與 now 同為 aware 才能比較/正確排程
        ).replace(hour=int(m.group(2)), minute=int(m.group(3) or 0))
        if target_dt <= now:
            return None  # 過去的時間
        return target_dt, m.group(4).strip()

    return None


def add_reminder(user_id, text, fire_at, push_func):
    """登記提醒並 schedule。push_func(user_id, text) 在到時被呼叫（同步函式）。
    Notion 持久化：提醒同步寫 Notion，redeploy 後 restore_reminders_from_notion()
    會把未到期的重新 schedule。"""
    from app_state import get_scheduler
    scheduler = get_scheduler()
    if not scheduler:
        return None

    with _LOCK:
        next_id = _REMINDER_NEXT_ID.get(user_id, 0) + 1
        _REMINDER_NEXT_ID[user_id] = next_id
        item = {
            "id": next_id, "text": text, "fire_at": fire_at,
            "page_id": None,
        }
        _REMINDERS.setdefault(user_id, []).append(item)

    # 寫 Notion（lock 外）
    if _notion_enabled():
        try:
            import notion_db
            page_id = notion_db.reminders_create(user_id, text, fire_at, next_id)
            if page_id:
                with _LOCK:
                    item["page_id"] = page_id
        except Exception as e:
            print(f"[personal/reminders] Notion create 失敗：{e}")

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
    """到時觸發：找出該筆內容、push 給 user、從清單移除、Notion 標 fired。"""
    target = None
    with _LOCK:
        items = _REMINDERS.get(user_id, [])
        for t in items:
            if t["id"] == reminder_id:
                target = t
                break
        if target:
            items.remove(target)
    if not target:
        return
    try:
        push_func(user_id, f"⏰ 提醒：{target['text']}")
    except Exception as e:
        print(f"提醒 push 失敗 {mask(user_id)}/{reminder_id}: {e}")
    # Notion 標 fired
    if target.get("page_id") and _notion_enabled():
        try:
            import notion_db
            notion_db.reminders_set_status(target["page_id"], "fired")
        except Exception as e:
            print(f"[personal/reminders] Notion set fired 失敗：{e}")


def list_reminders(user_id):
    with _LOCK:
        return sorted(_REMINDERS.get(user_id, []), key=lambda x: x["fire_at"])


def cancel_reminder(user_id, reminder_id):
    from app_state import get_scheduler
    scheduler = get_scheduler()
    page_id = None
    with _LOCK:
        items = _REMINDERS.get(user_id, [])
        for i, t in enumerate(items):
            if t["id"] == reminder_id:
                page_id = t.get("page_id")
                del items[i]
                if scheduler:
                    try:
                        scheduler.remove_job(f"reminder-{user_id}-{reminder_id}")
                    except Exception:
                        pass
                # Notion 標 cancelled
                if page_id and _notion_enabled():
                    try:
                        import notion_db
                        notion_db.reminders_set_status(page_id, "cancelled")
                    except Exception as e:
                        print(f"[personal/reminders] Notion set cancelled 失敗：{e}")
                return True
    return False


def restore_reminders_from_notion(push_func):
    """startup 時從 Notion 載入所有 pending reminders 並 reschedule。
    過期的（fire_at < now）標 fired，不 reschedule。"""
    if not _notion_enabled():
        return 0
    try:
        import notion_db
        rows = notion_db.reminders_load_pending_all()
    except Exception as e:
        print(f"[personal/reminders] restore load 失敗：{e}")
        return 0

    if not rows:
        return 0

    from app_state import get_scheduler
    scheduler = get_scheduler()
    if not scheduler:
        print("[personal/reminders] scheduler 未就緒，restore skip")
        return 0

    now = datetime.now(rows[0]["fire_at"].tzinfo) if rows[0]["fire_at"].tzinfo else datetime.now()
    restored = 0
    expired = 0

    for r in rows:
        user_id = r["user_id"]
        local_id = r["local_id"]
        text = r["text"]
        fire_at = r["fire_at"]
        page_id = r["page_id"]

        if fire_at <= now:
            # 已過期，Notion 標 fired，不 reschedule
            try:
                notion_db.reminders_set_status(page_id, "fired")
            except Exception:
                pass
            expired += 1
            continue

        # 還原 in-mem state
        with _LOCK:
            _REMINDERS.setdefault(user_id, []).append({
                "id": local_id, "text": text, "fire_at": fire_at,
                "page_id": page_id,
            })
            cur_max = _REMINDER_NEXT_ID.get(user_id, 0)
            if local_id > cur_max:
                _REMINDER_NEXT_ID[user_id] = local_id

        job_id = f"reminder-{user_id}-{local_id}"
        try:
            scheduler.add_job(
                _fire_reminder,
                "date",
                run_date=fire_at,
                args=[user_id, local_id, push_func],
                id=job_id,
                replace_existing=True,
            )
            restored += 1
        except Exception as e:
            print(f"[personal/reminders] reschedule 失敗 {job_id}：{e}")

    print(f"[personal/reminders] restore 完成：reschedule {restored} 個 / 過期清理 {expired} 個")
    return restored


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
