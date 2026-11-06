"""TimeTree 共用行事曆（只讀）：併進情侶群組 21:00 的明天預告。

TimeTree 官方 API 已關，這裡走網頁版的登入流程（跟開源 timetree-exporter
同一組端點）：email/密碼登入拿 `_session_id` cookie，再用 sync 端點拉整本
行事曆。非官方介面，隨時可能改 —— 所以任何錯誤都只印 log、回空清單，
不拖垮 Google 日曆那條。

環境變數：
- TIMETREE_EMAIL / TIMETREE_PASSWORD：帳密（使用者自己放 Infisical）
- TIMETREE_CALENDAR：要讀的行事曆名稱；沒設就不讀（先打 /admin/timetree-probe
  看有哪些名稱）
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

API_V1 = "https://timetreeapp.com/api/v1"
API_V2 = "https://timetreeapp.com/api/v2"
_HEADERS = {"Content-Type": "application/json", "X-Timetreea": "web/2.1.0/zh-TW"}
_TPE = ZoneInfo("Asia/Taipei")

_session = None  # 同一個 process 重用登入


def enabled():
    return bool(os.environ.get("TIMETREE_EMAIL") and os.environ.get("TIMETREE_PASSWORD"))


def calendar_name():
    return os.environ.get("TIMETREE_CALENDAR", "").strip()


def login():
    """登入並回傳帶 cookie 的 requests.Session。失敗 raise（訊息不含帳密）。"""
    s = requests.Session()
    s.headers.update(_HEADERS)
    resp = s.put(f"{API_V1}/auth/email/signin", json={
        "uid": os.environ["TIMETREE_EMAIL"],
        "password": os.environ["TIMETREE_PASSWORD"],
        "uuid": uuid.uuid4().hex,
    }, timeout=15)
    if resp.status_code != 200 or "_session_id" not in s.cookies:
        raise RuntimeError(f"TimeTree 登入失敗：HTTP {resp.status_code} {resp.text[:120]}")
    return s


def _get(path_url, relogin=True):
    global _session
    if _session is None:
        _session = login()
    resp = _session.get(path_url, timeout=20)
    if resp.status_code == 401 and relogin:
        _session = None
        return _get(path_url, relogin=False)
    resp.raise_for_status()
    return resp.json()


def list_calendars():
    data = _get(f"{API_V2}/calendars?since=0")
    return [c for c in data.get("calendars", []) if not c.get("deactivated_at")]


def _find_calendar(name):
    for c in list_calendars():
        if c.get("name") == name:
            return c
    return None


def fetch_raw_events(calendar_id):
    """整本行事曆的事件（sync 端點分塊回，chunk=true 就帶 since 繼續拉）。"""
    url = f"{API_V1}/calendar/{calendar_id}/events/sync"
    events = []
    for _ in range(50):  # 防無限迴圈
        data = _get(url)
        events.extend(data.get("events", []))
        if not data.get("chunk"):
            break
        url = f"{API_V1}/calendar/{calendar_id}/events/sync?since={data['since']}"
    return [e for e in events if not e.get("deactivated_at")]


def _tz(name):
    try:
        return ZoneInfo(name or "Asia/Taipei")
    except Exception:
        return _TPE


def _start_end(ev):
    """回 (start, end)：整天行程是 date，其他是台北時間 datetime。"""
    s = datetime.fromtimestamp(ev["start_at"] / 1000, tz=timezone.utc)
    e = datetime.fromtimestamp(ev.get("end_at", ev["start_at"]) / 1000, tz=timezone.utc)
    if ev.get("all_day"):
        return s.date(), e.date()
    return s.astimezone(_tz(ev.get("start_timezone"))), e.astimezone(_tz(ev.get("end_timezone")))


def _occurs_on(ev, day):
    """這個事件（含重複規則）有沒有跟 day 重疊。"""
    start, end = _start_end(ev)
    all_day = ev.get("all_day")
    length = max(end - start, timedelta(0))

    def overlaps(s):
        if all_day:
            return s <= day <= s + length
        s_day = s.astimezone(_TPE).date()
        e_day = (s + length).astimezone(_TPE).date()
        return s_day <= day <= e_day

    rules = [r for r in ev.get("recurrences") or [] if r]
    if not rules:
        return overlaps(start)

    from dateutil.rrule import rrulestr
    dtstart = datetime(start.year, start.month, start.day) if all_day else start.replace(tzinfo=None)
    try:
        rs = rrulestr("\n".join(rules), dtstart=dtstart, forceset=True, ignoretz=True)
    except Exception:
        # EXDATE 之類格式不合，退回只吃 RRULE
        rr = [r for r in rules if r.startswith("RRULE")]
        if not rr:
            return overlaps(start)
        rs = rrulestr(rr[0], dtstart=dtstart, forceset=True, ignoretz=True)
    window_start = datetime(day.year, day.month, day.day) - length - timedelta(days=1)
    window_end = datetime(day.year, day.month, day.day) + timedelta(days=1)
    for occ in rs.between(window_start, window_end, inc=True):
        s = occ.date() if all_day else occ.replace(tzinfo=start.tzinfo)
        if overlaps(s):
            return True
    return False


def _to_google_shape(ev, day):
    """轉成 couple_calendar._event_line 吃的 Google event 形狀。"""
    start, _ = _start_end(ev)
    if ev.get("all_day"):
        st = {"date": day.isoformat()}
    else:
        when = datetime.combine(day, start.astimezone(_TPE).timetz())
        st = {"dateTime": when.isoformat()}
    out = {"summary": ev.get("title") or "（沒有標題）", "start": st, "source": "timetree"}
    if ev.get("location"):
        out["location"] = ev["location"]
    return out


def events_on(day):
    """那一天（台北時間）的 TimeTree 行程，Google event 形狀。沒設定或失敗回 []。"""
    name = calendar_name()
    if not (enabled() and name):
        return []
    try:
        cal = _find_calendar(name)
        if cal is None:
            print(f"[timetree] 找不到名為「{name}」的行事曆")
            return []
        raw = fetch_raw_events(cal["id"])
        return [_to_google_shape(e, day) for e in raw if _occurs_on(e, day)]
    except Exception as e:
        print(f"[timetree] 讀取失敗（非致命）：{str(e)[:200]}")
        return []


def probe():
    """給 /admin/timetree-probe：從 Railway 試登入，列出行事曆名稱與明天的筆數。"""
    if not enabled():
        return {"ok": False, "error": "沒設 TIMETREE_EMAIL / TIMETREE_PASSWORD"}
    global _session
    _session = None
    try:
        cals = list_calendars()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    tomorrow = datetime.now(_TPE).date() + timedelta(days=1)
    return {
        "ok": True,
        "calendars": [c.get("name") for c in cals],
        "selected": calendar_name() or None,
        "tomorrow": tomorrow.isoformat(),
        "tomorrow_count": len(events_on(tomorrow)),
    }
