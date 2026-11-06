"""情侶群組的「明天預告」：前一晚 21:00 推 ⭐ 特別活動 + 到期待辦。

使用者 2026-10-24 定的規則：
- 只有「⭐ 特別活動」那個 Google 日曆裡的行程才推；共用日曆「我們倆」的
  一般行程不推（兩人自己看 App 的通知就夠）。
- 待辦用群組共用那份（personal.py 以群組 ID 當清單主人），只挑**有截止日**
  且 ≤ 明天的 —— 也就是明天到期 + 已逾期。沒設日期的 P0 不帶。
- 2026-10-24 使用者改規則：**明天有特別活動才推**，待辦只是順便帶上；
  只有待辦、沒有活動 → 不推。推到群組是每個成員各算一則，兩人 = 每次 2 則。

日曆用獨立的 token（CAL_TOKEN_PICKLE_B64，scope 只有 calendar），
完全不碰財務同步在用的 TOKEN_PICKLE_B64 —— 理由同 setup_send_token.py。
"""

import base64
import os
import pickle
import tempfile
from datetime import date, datetime, timedelta

import personal
from tz_utils import now_tpe

CAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]
CAL_TOKEN_FILE = "token_calendar.pickle"

_WEEKDAYS = "一二三四五六日"


def special_calendar_id():
    return os.environ.get("SPECIAL_CALENDAR_ID", "").strip()


def _load_creds():
    b64 = os.environ.get("CAL_TOKEN_PICKLE_B64")
    if b64:
        return pickle.loads(base64.b64decode(b64))
    if os.path.exists(CAL_TOKEN_FILE):
        with open(CAL_TOKEN_FILE, "rb") as f:
            return pickle.load(f)
    return None


def get_calendar_service():
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = _load_creds()
    if creds is None:
        raise RuntimeError("沒有日曆授權：先跑 setup_calendar.py，再把 CAL_TOKEN_PICKLE_B64 設到 Infisical")
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch_events(day, service=None, calendar_id=None):
    """那一天（台北時間）的 ⭐ 特別活動 + TimeTree 共用行事曆行程，時間早的在前。

    兩邊各自吞錯誤：一邊掛了另一邊照推。
    """
    import timetree_client

    try:
        events = _fetch_google(day, service, calendar_id)
    except Exception as e:
        print(f"[couple_calendar] 讀 Google 日曆失敗（非致命）：{e}")
        events = []
    events = events + timetree_client.events_on(day)
    events.sort(key=lambda ev: ev.get("start", {}).get("dateTime", ""))  # 整天的排最前
    return events


def _fetch_google(day, service=None, calendar_id=None):
    calendar_id = calendar_id or special_calendar_id()
    if not calendar_id:
        return []
    service = service or get_calendar_service()
    start = datetime(day.year, day.month, day.day, tzinfo=now_tpe().tzinfo)
    end = start + timedelta(days=1)
    resp = service.events().list(
        calendarId=calendar_id,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,           # 重複行程展開成單次
        orderBy="startTime",
    ).execute()
    return resp.get("items", [])


def _event_line(ev):
    title = ev.get("summary") or "（沒有標題）"
    start = ev.get("start", {})
    if "dateTime" in start:
        when = datetime.fromisoformat(start["dateTime"]).strftime("%H:%M")
    else:
        when = "整天"
    place = ev.get("location")
    icon = "🌳" if ev.get("source") == "timetree" else "⭐"
    line = f"{icon} {when}　{title}"
    if place:
        line += f"（{place.split(',')[0]}）"
    return line


def todos_due_by(group_id, day):
    """有截止日且 ≤ day 的群組待辦，逾期最久的在前。"""
    out = []
    for t in personal.list_todos(group_id):
        due = personal._deadline(t)
        if due and due <= day:
            out.append(t)
    out.sort(key=personal._deadline)
    return out


def _todo_line(t, day):
    due = personal._deadline(t)
    if due < day - timedelta(days=1):
        # 推播是前一晚發的，「今天」是 day-1；比今天還早才叫逾期
        mark = f"⚠️ 逾期 {(day - timedelta(days=1) - due).days} 天"
    elif due < day:
        mark = "今天"
    else:
        mark = "明天"
    return f"⬜ [{t['id']}] {t['text']}（{mark}）"


def build_preview(day, events, todos, todo_trigger=False):
    """組明天預告的文字。明天沒有特別活動、也沒有明天到期的待辦回 None
    （呼叫端據此不推）。只有逾期的不推 —— 逾期的交給早上 9 點那次提醒，
    不然同一件事一天被推兩次。"""
    if not events and not todo_trigger:
        return None
    lines = [f"📅 明天 {day.month}/{day.day}（{_WEEKDAYS[day.weekday()]}）"]
    if events:
        lines.append("")
        lines.extend(_event_line(ev) for ev in events)
    if todos:
        lines.append("")
        lines.append("📝 待辦")
        lines.extend(_todo_line(t, day) for t in todos)
    return chr(10).join(lines)


def _flag_path(day):
    return os.path.join(tempfile.gettempdir(), f"couple_preview_{day:%Y%m%d}.flag")


TOMORROW_KEYWORDS = {"明天行程", "明天", "明日行程"}


def tomorrow_reply(today=None):
    """私訊「明天行程」：回覆明天的 ⭐ 特別活動與到期群組待辦（reply 不吃 push 額度）。"""
    import couple_ledger
    today = today or now_tpe().date()
    tomorrow = today + timedelta(days=1)
    try:
        events = fetch_events(tomorrow)
    except Exception as e:
        print(f"[couple_calendar] 讀日曆失敗：{e}")
        events = []
    gid = couple_ledger.couple_group_id()
    todos = todos_due_by(gid, tomorrow) if gid else []
    text = build_preview(tomorrow, events, todos)
    if not text and todos:
        text = build_preview(tomorrow, [{"summary": "（沒有特別活動）", "start": {"date": tomorrow.isoformat()}}], todos)
    return text or f"📅 明天（{tomorrow.month}/{tomorrow.day}）沒有特別活動，也沒有到期的待辦。"


def run_evening_preview(push_func=None, today=None):
    """排程進入點（21:00 TPE）。回傳推出去的文字，沒推回 None。"""
    import couple_ledger

    group_id = couple_ledger.couple_group_id()
    if not group_id:
        print("[couple_calendar] 沒設 COUPLE_GROUP_ID，跳過")
        return None

    today = today or now_tpe().date()
    tomorrow = today + timedelta(days=1)

    # 冪等：同一晚重啟補跑不會推第二次（每次 2 則額度）
    flag = _flag_path(tomorrow)
    if os.path.exists(flag):
        print(f"[couple_calendar] 今晚已推過（{flag}）")
        return None

    try:
        events = fetch_events(tomorrow)
    except Exception as e:
        # 讀不到日曆就等於沒有特別活動 → 不推（只有待辦不推）
        print(f"[couple_calendar] 讀日曆失敗（非致命）：{e}")
        events = []

    todos = todos_due_by(group_id, tomorrow)
    trigger = any(personal._deadline(t) == tomorrow for t in todos)
    text = build_preview(tomorrow, events, todos, todo_trigger=trigger)
    if not text:
        print("[couple_calendar] 明天沒有特別活動也沒有到期待辦，不推")
        return None

    if push_func is None:
        from line_sender import push_to_user_sync as push_func
    push_func(group_id, text)
    try:
        with open(flag, "w") as f:
            f.write(now_tpe().isoformat())
    except Exception as e:
        print(f"[couple_calendar] flag 寫入失敗（非致命）：{e}")
    return text


# ─────────────────────────────────────────────────────────
# 待辦提醒：晚上 21:00 預告明天到期的（上面的 run_evening_preview 帶），
# 早上 09:00 再推今天到期與逾期的。記帳群組推到群組，你自己的私訊給你。
# 沒有到期的就不推 —— 每則群組推播吃 2 則月額度（200 則/月）。
# ─────────────────────────────────────────────────────────

def build_morning(day, todos):
    if not todos:
        return None
    lines = [f"⏰ 今天 {day.month}/{day.day}（{_WEEKDAYS[day.weekday()]}）要做的事"]
    for t in todos:
        due = personal._deadline(t)
        mark = "今天" if due == day else f"⚠️ 逾期 {(day - due).days} 天"
        who = f"　👤 {t['for']}" if t.get("for") else ""
        lines.append(f"⬜ [{t['id']}] {t['text']}（{mark}）{who}")
    lines.append("")
    lines.append("做完打「待辦」按完成就會消失。")
    return chr(10).join(lines)


def _owner_flag(kind, owner, day):
    return os.path.join(tempfile.gettempdir(),
                        f"todo_{kind}_{owner[-6:]}_{day:%Y%m%d}.flag")


def _push_once(kind, owner, day, text, push_func):
    flag = _owner_flag(kind, owner, day)
    if not text or os.path.exists(flag):
        return None
    push_func(owner, text)
    try:
        with open(flag, "w") as f:
            f.write(now_tpe().isoformat())
    except Exception as e:
        print(f"[todo_remind] flag 寫入失敗（非致命）：{e}")
    return text


def run_morning_todos(push_func=None, today=None):
    """排程進入點（09:00 TPE）：今天到期與逾期的待辦。回推出去的文字 list。"""
    import couple_ledger

    if push_func is None:
        from line_sender import push_to_user_sync as push_func
    today = today or now_tpe().date()
    sent = []
    owners = [couple_ledger.couple_group_id()] + _people()
    for owner in [o for o in owners if o]:
        try:
            text = build_morning(today, todos_due_by(owner, today))
        except Exception as e:
            print(f"[todo_remind] 讀待辦失敗（非致命）：{e}")
            continue
        try:
            if _push_once("am", owner, today, text, push_func):
                sent.append(text)
        except Exception as e:
            print(f"[todo_remind] {owner[-6:]} 早上提醒推送失敗（非致命）：{e}")
    return sent


def _people():
    """私訊提醒的對象：你和小螢，各推自己私人清單裡的事。"""
    import notion_db

    out = []
    for uid in (os.environ.get("ADMIN_LINE_USER_ID", "").strip(), notion_db.partner_line_id()):
        # 已併進共同清單的人不另外私訊：群組那則已經帶到同一批事，再推就重複
        if uid and personal.canonical_owner(uid) != uid:
            continue
        if uid and uid not in out:
            out.append(uid)
    return out


def run_evening_personal(push_func=None, today=None):
    """21:00：你和小螢各自明天到期的私人待辦，私訊預告（群組的由 run_evening_preview 帶）。

    回這次推出去的人數。一個人推失敗（例如小螢沒加喵管家好友）不影響另一個。
    """
    if push_func is None:
        from line_sender import push_to_user_sync as push_func
    today = today or now_tpe().date()
    tomorrow = today + timedelta(days=1)
    sent = 0
    for owner in _people():
        try:
            due = [t for t in personal.list_todos(owner) if personal._deadline(t) == tomorrow]
            if not due:
                continue
            lines = [f"📅 明天 {tomorrow.month}/{tomorrow.day} 到期"]
            lines.extend(f"⬜ [{t['id']}] {t['text']}" for t in due)
            if _push_once("pm", owner, tomorrow, chr(10).join(lines), push_func):
                sent += 1
        except Exception as e:
            print(f"[todo_remind] {owner[-6:]} 明天預告失敗（非致命）：{e}")
    return sent
