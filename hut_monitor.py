"""山屋名額監控：抓 → 跟上次比 → 有變化寄 Gmail；LINE「山屋」回最新摘要。

狀態只放記憶體。Railway 重新部署後第一次檢查當基準、不寄信，最多漏一次變化。
一站式由本機 tools/hut_browser.py 用真瀏覽器抓，POST /admin/hut-ingest 送進來，
60 分鐘內的才算數（本機比伺服器早 10 分鐘跑）。
HUT_FOREST_ON_SERVER=0 時林業署三站也改由本機送（Railway 國外 IP 被擋時的備案）。

設計：docs/superpowers/specs/2026-11-02-hut-monitor-design.md
"""

import os
import threading
from datetime import timedelta

import hut_calendar
import hut_onestop
import hut_report
import hut_sources
from hut_sources import Slot
from tz_utils import now_tpe, today_tpe

KEYWORDS = {"山屋", "山屋空位"}
INGEST_FRESH = timedelta(minutes=60)
FAIL_ALERT = 3
LOTTERY_SITES = {"jmlnt", "kgonline", "onestop_paiyun"}  # 抽籤前的「剩餘」只是還沒分配
MIN_AHEAD, LOTTERY_MAX_AHEAD, MAX_AHEAD = 5, 30, 61
MISSING = "本次沒查到（電腦沒開）"

_LOCK = threading.Lock()
_snapshot = {}       # Slot.key() -> remaining
_baselined = set()   # 有過快照的 site；第一次只記不報
_fails = {}          # site -> 連續失敗次數
_latest = {}         # site -> [Slot]（給 LINE 查詢；失敗的站保留上次的）
_ingest = {}         # site -> (收到時間, [Slot])
_ingest_errors = {}  # site -> (收到時間, 訊息)
_status = {"checked_at": None, "errors": {}}


def reset():
    """測試用。"""
    with _LOCK:
        for d in (_snapshot, _fails, _latest, _ingest, _ingest_errors):
            d.clear()
        _baselined.clear()
        _status.update(checked_at=None, errors={})


def status():
    with _LOCK:
        return {"checked_at": _status["checked_at"], "errors": dict(_status["errors"])}


def watchable(slot, today):
    ahead = (slot.day - today).days
    limit = LOTTERY_MAX_AHEAD if slot.site in LOTTERY_SITES else MAX_AHEAD
    return MIN_AHEAD <= ahead <= limit


def _known_sites():
    return set(hut_sources.SITES) | set(hut_onestop.ONESTOP)


def _forest_on_server():
    return os.environ.get("HUT_FOREST_ON_SERVER", "1") != "0"


def ingest(payload, now=None):
    """本機送來的 {"slots": [Slot.to_dict()], "errors": {site: 訊息}}。"""
    now = now or now_tpe()
    slots = [Slot.from_dict(d) for d in payload.get("slots") or []]
    errors = {str(k): str(v)[:200] for k, v in (payload.get("errors") or {}).items()}
    unknown = ({s.site for s in slots} | set(errors)) - _known_sites()
    if unknown:
        raise ValueError(f"不認得的 site：{sorted(unknown)}")
    by_site = {}
    for s in slots:
        by_site.setdefault(s.site, []).append(s)
    with _LOCK:
        for site, ss in by_site.items():
            _ingest[site] = (now, ss)
        for site, msg in errors.items():
            _ingest_errors[site] = (now, msg)
    return {"slots": len(slots), "sites": sorted(by_site), "errors": sorted(errors)}


def _merge_ingest(now, forest_on_server):
    expected = list(hut_onestop.ONESTOP) + ([] if forest_on_server else list(hut_sources.SITES))
    slots, errors = [], {}
    for site in expected:
        got = _ingest.get(site)
        if got and now - got[0] <= INGEST_FRESH:
            slots += got[1]
            continue
        err = _ingest_errors.get(site)
        errors[site] = err[1] if err and now - err[0] <= INGEST_FRESH else MISSING
    return slots, errors


def _diff(slots, today):
    changes = []
    for s in slots:
        if s.site not in _baselined or not watchable(s, today):
            continue
        if s.remaining is None or s.remaining <= 0:
            continue
        before = _snapshot.get(s.key())
        if before is None or s.remaining > before:
            changes.append(hut_report.Change(s, before))
    return changes


def _send_email(subject, text):
    import mailer
    return mailer.send_email(subject, text, html=mailer.to_html(text))


def _notify(err, ctx):
    from admin_notify import notify_admin
    notify_admin(err, ctx)


def run_check(send_events_today=False, now=None, fetch=None, send=None, notify=None):
    now = now or now_tpe()
    today = now.date()
    fetch = fetch or hut_sources.fetch_all
    send = send or _send_email
    notify = notify or _notify

    on_server = _forest_on_server()
    slots, errors = fetch(today) if on_server else ([], {})
    with _LOCK:
        in_slots, in_errors = _merge_ingest(now, on_server)
        slots = slots + in_slots
        errors = {**errors, **in_errors}
        ok_sites = {s.site for s in slots}
        changes = _diff(slots, today)
        for s in slots:
            _snapshot[s.key()] = s.remaining
        _baselined.update(ok_sites)
        for site in ok_sites:
            _fails[site] = 0
            _latest[site] = [s for s in slots if s.site == site]
        alerts = []
        for site in errors:
            _fails[site] = _fails.get(site, 0) + 1
            # 電腦沒開不是故障，不推 LINE（5 個一站式地點會各推一次，吃額度）
            if _fails[site] == FAIL_ALERT and errors[site] != MISSING:
                alerts.append(site)
        _status.update(checked_at=now, errors=dict(errors))

    for site in alerts:
        notify(RuntimeError(f"{site} 連續 {FAIL_ALERT} 次沒查到：{errors[site]}"),
               {"module": "hut_monitor", "section": site})

    events = hut_calendar.upcoming_events(today)
    has_events_today = any(e[0] == today for e in events)
    sent = False
    if changes or (send_events_today and has_events_today):
        subject, text = hut_report.build_email(changes, events, errors, today)
        sent = bool(send(subject, text))
    return {"changes": len(changes), "errors": errors, "sent": sent}


def line_reply(today=None):
    today = today or today_tpe()
    with _LOCK:
        checked_at = _status["checked_at"]
        errors = dict(_status["errors"])
        slots = [s for ss in _latest.values() for s in ss if watchable(s, today)]
    if checked_at is None:
        return "山屋還沒查過，下一次 07:00／16:30／23:30 會查。"
    return hut_report.line_summary(slots, checked_at, errors)
