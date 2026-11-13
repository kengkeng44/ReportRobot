"""
集中排程：Railway 定時任務（cron service）叫這支，跑完當班的工作就結束，不常駐。

為什麼：網頁服務常駐一整天，Railway 帳單 99% 是記憶體佔用時間（2026-11 實測）。
改成網頁閒置就休眠，定時工作集中在 4 個時段一次跑完。

時段（台北時間）：
  06:50  山屋早上提醒（07:00 開放的先到先得）、OwnTracks 整理昨天、每日報、每日維護
  09:00  待辦早上提醒、LINE 額度、找重複發票
  15:00  記帳同步 → 載具發票 → 共同帳本重掃 → 來源補標 → 大類補標
  21:00  情侶群組明天預告、山屋晚上提醒
每個時段都順便跑：到期的「提醒我」、重要 Gmail 轉發、Notion 打勾記共同帳本、發票人工確認。

Railway cron（UTC）：`0,50 1,7,13,22 * * *`。一天叫 8 次，對不上時段的 4 次直接結束，
不載入任何東西（cron 只能寫一組分鐘，06:50 跟其他整點湊不進同一個式子）。

用法：
  python jobs.py           依現在時間挑時段（對不上就什麼都不做）
  python jobs.py 1500      指定時段（手動補跑）
  python jobs.py list      列出時段
"""

import os
import sys
from datetime import datetime, timedelta

SLOTS = ("0650", "0900", "1500", "2100")
# cron 可能晚幾分鐘才啟動；30 分鐘內都算同一班。08:50 那類叫醒比時段早，不會被當成 09:00。
SLOT_WINDOW = timedelta(minutes=30)


def _now_tpe():
    from tz_utils import now_tpe
    return now_tpe()


def _slot_dt(slot, day, tz):
    return datetime(day.year, day.month, day.day, int(slot[:2]), int(slot[2:]), tzinfo=tz)


def pick_slot(now):
    """now（台北時間、含時區）落在哪個時段的 [時段, 時段+30 分) 裡；都沒有回 None。"""
    for slot in SLOTS:
        start = _slot_dt(slot, now.date(), now.tzinfo)
        if start <= now < start + SLOT_WINDOW:
            return slot
    return None


def previous_slot_start(slot, day, tz):
    """這一班的上一班開始時間（Gmail 轉發用：只看兩班之間進來的信）。"""
    i = SLOTS.index(slot)
    if i == 0:
        return _slot_dt(SLOTS[-1], day - timedelta(days=1), tz)
    return _slot_dt(SLOTS[i - 1], day, tz)


def _step(name, fn, *args, **kwargs):
    """每一步各自吞例外：一段壞了不拖累同一班的其他工作。"""
    print(f"[jobs] ▶ {name}", flush=True)
    try:
        out = fn(*args, **kwargs)
        if out not in (None, 0, False):
            print(f"[jobs]   {name}：{out}", flush=True)
    except Exception as e:
        print(f"[jobs] ✖ {name} 失敗（非致命）：{type(e).__name__}: {e}", flush=True)
        try:
            from admin_notify import notify_admin
            notify_admin(e, {"module": "jobs", "section": name})
        except Exception:
            pass


# ───────────────────────── 各項工作 ─────────────────────────

def due_reminders():
    from line_sender import push_to_user_sync
    from personal import dispatch_due_reminders
    return dispatch_due_reminders(push_to_user_sync)


def gmail_forward(since):
    import alerts
    return alerts.check_gmail_forward(since=since)


def couple_sync_checked():
    import couple_ledger
    return couple_ledger.sync_checked()


def invoice_review():
    import einvoice_ledger
    return einvoice_ledger.review()


def daily_report():
    if os.environ.get("DAILY_REPORT_PAUSED", "1") == "1":
        return "暫停中（DAILY_REPORT_PAUSED）"
    import asyncio
    from daily_report import run_daily_report
    asyncio.run(run_daily_report())
    return None


def hut_remind(kind):
    import hut_remind as hr
    from tz_utils import today_tpe
    return hr.push(kind, today_tpe())


def owntracks_nightly():
    import owntracks
    owntracks.nightly()


def notion_schema():
    """Notion 資料庫欄位收斂（以前每次開機跑；網頁會休眠後改成一天一次）。"""
    import notion_db
    notion_db.ensure_all_dbs()


def richmenu():
    import setup_richmenu
    return setup_richmenu.ensure_current()


def transfer_backfill():
    """轉帳通知信一年份補抓（以前每次開機跑）。有指紋擋著，重跑安全。"""
    import finance_sync
    return finance_sync.sync(lookback_days=400, sources=finance_sync.TRANSFER_SOURCES,
                             max_results=200)


def line_quota():
    from line_quota import check_and_warn
    check_and_warn()


def todo_morning():
    import couple_calendar
    couple_calendar.run_morning_todos()


def ledger_dedup():
    import einvoice_ledger
    report = einvoice_ledger.dedup()
    if report:
        from admin_notify import _admin_user_id
        admin_id = _admin_user_id()
        if admin_id:
            from line_sender import push_to_user_sync
            push_to_user_sync(admin_id, report)
    return report or "沒有重複"


def finance():
    """交易與持倉分開：其中一個掛掉不該讓另一個也不跑。"""
    import finance_sync
    _step("記帳同步：交易", finance_sync.sync)
    _step("記帳同步：持倉", finance_sync.sync_portfolio)


def einvoice():
    import einvoice_sync
    return einvoice_sync.sync()


def couple_rescan():
    import couple_ledger
    return couple_ledger.rescan()


def sources_backfill():
    import sources
    return sources.backfill()


def taxonomy_backfill():
    import spend_taxonomy
    return spend_taxonomy.backfill()


def couple_preview():
    import couple_calendar
    _step("情侶群組明天預告", couple_calendar.run_evening_preview)
    _step("個人明天預告", couple_calendar.run_evening_personal)


# ───────────────────────── 時段 ─────────────────────────

def plan(slot):
    """回傳 [(名稱, 函式, 參數)]，照順序跑。"""
    steps = {
        "0650": [
            ("山屋早上提醒", hut_remind, ("morning",)),   # 要趕在 07:00 開放前，排第一
            ("OwnTracks 整理昨天", owntracks_nightly, ()),
            ("每日報", daily_report, ()),
            ("Notion 欄位收斂", notion_schema, ()),
            ("圖文選單", richmenu, ()),
            ("轉帳信補抓", transfer_backfill, ()),
        ],
        "0900": [
            ("待辦早上提醒", todo_morning, ()),
            ("LINE 額度", line_quota, ()),
            ("找重複發票", ledger_dedup, ()),
        ],
        # 順序有關係：記帳 → 載具發票補品項 → 共同帳本重掃 → 來源 → 大類（要品項才分得出來）
        "1500": [
            ("記帳同步", finance, ()),
            ("載具發票", einvoice, ()),
            ("共同帳本重掃", couple_rescan, ()),
            ("來源補標", sources_backfill, ()),
            ("大類補標", taxonomy_backfill, ()),
        ],
        "2100": [
            ("明天預告", couple_preview, ()),
            ("山屋晚上提醒", hut_remind, ("evening",)),
        ],
    }[slot]
    return steps


def run(slot, now=None):
    now = now or _now_tpe()
    since = previous_slot_start(slot, now.date(), now.tzinfo)
    print(f"[jobs] 時段 {slot}（{now:%Y-%m-%d %H:%M} 台北）開始", flush=True)
    _step("到期提醒", due_reminders)
    for name, fn, args in plan(slot):
        _step(name, fn, *args)
    _step("重要 Gmail 轉發", gmail_forward, since)
    _step("Notion 打勾記共同帳本", couple_sync_checked)
    _step("發票人工確認", invoice_review)
    print(f"[jobs] 時段 {slot} 結束", flush=True)


def main(argv):
    arg = argv[1] if len(argv) > 1 else ""
    if arg == "list":
        for s in SLOTS:
            print(s, "、".join(name for name, _, _ in plan(s)))
        return 0
    if arg:
        if arg not in SLOTS:
            print(f"不認得的時段 {arg!r}，可用：{', '.join(SLOTS)}")
            return 2
        slot = arg
    else:
        now = _now_tpe()
        slot = pick_slot(now)
        if not slot:
            print(f"[jobs] {now:%H:%M} 不在任何時段，結束")
            return 0
    run(slot)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
