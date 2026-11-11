"""山屋名額監控：抓 → 跟上次比 → 有變化寄 Gmail；LINE「山屋」回最新摘要。

狀態放記憶體；開機先 restore_from_notion 從 Notion 補回快照，重新部署後第一次檢查就能比對。
Notion 讀不到才退回舊行為：第一次檢查當基準、不寄信。
一站式由本機 tools/hut_browser.py 用真瀏覽器抓，POST /admin/hut-ingest 送進來，
60 分鐘內的才算數（本機比伺服器早 10 分鐘跑）。
HUT_FOREST_ON_SERVER=0 時林業署三站也改由本機送（Railway 國外 IP 被擋時的備案）。
寄信只寄住宿日是週五、六、日的增加；全部空位（含平日）寫進 Notion（hut_notion，背景執行緒）。

設計：docs/superpowers/specs/2026-11-02-hut-monitor-design.md
"""

import os
import threading
from datetime import timedelta

import hut_calendar
import hut_notion
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
_NOTION_BUSY = threading.Lock()  # 上一次同步還沒寫完（第一次約 2 分鐘）就跳過這次

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
    """只看週五六日晚上；先到先得看 5～61 天後，抽籤制抽完才準，看 5～30 天後。"""
    if not hut_calendar.is_good_night(slot.day):
        return False
    ahead = (slot.day - today).days
    limit = LOTTERY_MAX_AHEAD if slot.site in LOTTERY_SITES else MAX_AHEAD
    return MIN_AHEAD <= ahead <= limit


def _known_sites():
    return set(hut_sources.SITES) | set(hut_onestop.ONESTOP)


def paused():
    """山屋監控暫停（2026-11-11 使用者：目前用不到）。預設暫停；HUT_MONITOR_PAUSED=0 才恢復。"""
    return os.environ.get("HUT_MONITOR_PAUSED", "1") == "1"


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


def run_check(send_events_today=False, now=None, fetch=None, send=None, notify=None, sync=None):
    now = now or now_tpe()
    today = now.date()
    fetch = fetch or hut_sources.fetch_all
    send = send or _send_email
    notify = notify or _notify
    sync = sync or _sync_in_background

    on_server = _forest_on_server()
    slots, errors = fetch(today) if on_server else ([], {})
    with _LOCK:
        in_slots, in_errors = _merge_ingest(now, on_server)
        slots = slots + in_slots
        errors = {**errors, **in_errors}
        ok_sites = {s.site for s in slots}
        # 平日不在 watchable 裡，只記 Notion 不寄信
        changes = _diff(slots, today)
        for s in slots:
            _snapshot[s.key()] = s.remaining
        cutoff = today.isoformat()
        for k in [k for k in _snapshot if k[2] < cutoff]:
            del _snapshot[k]
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

    # 同一個原因（例如一站式逾時整批停）合併成一則，不要 5 個地點各推一次
    by_msg = {}
    for site in alerts:
        by_msg.setdefault(errors[site], []).append(site)
    for msg, sites in by_msg.items():
        notify(RuntimeError(f"{'、'.join(sites)} 連續 {FAIL_ALERT} 次沒查到：{msg}"),
               {"module": "hut_monitor", "section": ",".join(sites)})

    sync(slots, errors, now, notify)

    sent = False
    # 只有週六晚上有空位才寄；抽籤／開放日不再單獨寄，夾在週六的信裡
    # （send_events_today 留著給 server.py 的排程參數，已不影響寄信）
    mail = [c for c in changes if hut_calendar.is_notify_night(c.slot.day)]
    try:
        events = [e for e in hut_calendar.upcoming_events(today)
                  if hut_calendar.is_notify_night(e[2])]
        if any(hut_report.is_highlight(c) for c in mail):
            subject, text = hut_report.build_email(mail, events, errors, today)
            sent = bool(send(subject, text))
            if not sent:
                print(f"[hut_monitor] 信沒寄出（可能沒設信箱）：{subject}")
    except Exception as e:
        # 寄信失敗：把沒寄出的變化退回快照，下次還會再報
        with _LOCK:
            for c in mail:
                if c.before is None:
                    _snapshot.pop(c.slot.key(), None)
                else:
                    _snapshot[c.slot.key()] = c.before
        notify(e, {"module": "hut_monitor", "section": "send"})
        sent = False
    return {"changes": len(changes), "errors": errors, "sent": sent}


def line_reply(today=None):
    if paused():
        return "山屋監控暫停中（HUT_MONITOR_PAUSED），目前不會查空位。"
    today = today or today_tpe()
    with _LOCK:
        checked_at = _status["checked_at"]
        errors = dict(_status["errors"])
        slots = [s for ss in _latest.values() for s in ss if watchable(s, today)]
    if checked_at is None:
        return "山屋還沒查過，下一次 07:00／16:30／23:30 會查。"
    return hut_report.line_summary(slots, checked_at, errors)
