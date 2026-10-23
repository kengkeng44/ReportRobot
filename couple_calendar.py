"""情侶群組的「明天預告」：前一晚 21:00 推 ⭐ 特別活動 + 到期待辦。

使用者 2026-10-24 定的規則：
- 只有「⭐ 特別活動」那個 Google 日曆裡的行程才推；共用日曆「我們倆」的
  一般行程不推（兩人自己看 App 的通知就夠）。
- 待辦用群組共用那份（personal.py 以群組 ID 當清單主人），只挑**有截止日**
  且 ≤ 明天的 —— 也就是明天到期 + 已逾期。沒設日期的 P0 不推：它沒有時效，
  每晚都推等於每晚扣 2 則額度。
- 兩樣都沒有就不推。推到群組是每個成員各算一則，兩人 = 每次 2 則。

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
    """回傳那一天（台北時間）跟它重疊的行程，原始 Google event dict。"""
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
    line = f"⭐ {when}　{title}"
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


def build_preview(day, events, todos):
    """組明天預告的文字。兩樣都空回 None（呼叫端據此不推）。"""
    if not events and not todos:
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
        # 日曆掛了仍照推待辦，不讓一邊拖垮另一邊
        print(f"[couple_calendar] 讀日曆失敗（非致命）：{e}")
        events = []

    text = build_preview(tomorrow, events, todos_due_by(group_id, tomorrow))
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
