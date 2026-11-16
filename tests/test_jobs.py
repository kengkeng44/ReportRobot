from datetime import datetime, timedelta, timezone

import pytest

import jobs

TPE = timezone(timedelta(hours=8))


def at(h, m, day=13):
    return datetime(2026, 11, day, h, m, tzinfo=TPE)


# ── 時段挑選：Railway cron `*/10 * * * *`（UTC），每一班只認一次叫醒 ──

def test_each_slot_matches_exactly_one_cron_wakeup():
    day = datetime(2026, 11, 12, 16, 0, tzinfo=timezone.utc)   # 台北 11/13 00:00
    hits = {}
    for i in range(24 * 6):
        now = (day + timedelta(minutes=10 * i)).astimezone(TPE)
        slot = jobs.pick_slot(now)
        if slot:
            hits.setdefault(slot, []).append(f"{now:%H:%M}")
    assert hits == {"0650": ["06:50"], "0900": ["09:00"], "1500": ["15:00"], "2100": ["21:00"]}


def test_late_start_within_window_still_counts():
    assert jobs.pick_slot(at(15, 9)) == "1500"
    assert jobs.pick_slot(at(15, 10)) is None


def test_railway_cron_runs_every_10_minutes():
    import json
    import pathlib
    cfg = json.loads((pathlib.Path(jobs.__file__).parent / "railway.cron.json").read_text())
    assert cfg["deploy"]["cronSchedule"] == "*/10 * * * *"


def test_previous_slot_wraps_to_yesterday():
    assert jobs.previous_slot_start("0650", at(0, 0).date(), TPE) == at(21, 0, day=12)
    assert jobs.previous_slot_start("1500", at(0, 0).date(), TPE) == at(9, 0)


def test_every_slot_has_a_plan():
    for slot in jobs.SLOTS:
        assert jobs.plan(slot)


def test_morning_hut_reminder_runs_first():
    """06:50 的山屋提醒要趕在 07:00 開放前，排在其他工作前面。"""
    assert jobs.plan("0650")[0][1] is jobs.hut_remind


def test_one_failing_step_does_not_stop_the_slot(monkeypatch):
    ran = []
    monkeypatch.setattr(jobs, "notify_admin", None, raising=False)
    monkeypatch.setattr("admin_notify.notify_admin", lambda *a, **k: ran.append("notified"))

    def boom():
        raise RuntimeError("壞了")

    monkeypatch.setattr(jobs, "plan", lambda slot: [("壞的", boom, ()), ("好的", lambda: ran.append("ok"), ())])
    for name in ("due_reminders", "gmail_forward", "couple_sync_checked", "invoice_review"):
        monkeypatch.setattr(jobs, name, lambda *a, _n=name: ran.append(_n))
    jobs.run("1500", now=at(15, 0))
    assert ran == ["due_reminders", "notified", "ok", "gmail_forward",
                   "couple_sync_checked", "invoice_review"]


def test_gmail_forward_gets_previous_slot_as_window(monkeypatch):
    seen = {}
    monkeypatch.setattr(jobs, "plan", lambda slot: [])
    for name in ("due_reminders", "couple_sync_checked", "invoice_review"):
        monkeypatch.setattr(jobs, name, lambda: None)
    monkeypatch.setattr(jobs, "gmail_forward", lambda since: seen.setdefault("since", since))
    jobs.run("2100", now=at(21, 3))
    assert seen["since"] == at(15, 0)


def test_main_outside_slots_only_sends_due_reminders(monkeypatch):
    """「今天 18:00 去關車門」不能等到 21:00 那班才送。"""
    ran = []
    monkeypatch.setattr(jobs, "_now_tpe", lambda: at(18, 0))
    monkeypatch.setattr(jobs, "run", lambda slot: pytest.fail("不該跑整班"))
    monkeypatch.setattr(jobs, "due_reminders", lambda: ran.append("due"))
    assert jobs.main(["jobs.py"]) == 0
    assert ran == ["due"]


def test_off_slot_reminder_failure_does_not_page_admin(monkeypatch):
    """每 10 分鐘一次，Notion 掛掉時每次都通知會洗版。"""
    monkeypatch.setattr(jobs, "_now_tpe", lambda: at(18, 0))
    monkeypatch.setattr("admin_notify.notify_admin", lambda *a, **k: pytest.fail("不該通知"))

    def boom():
        raise RuntimeError("Notion 掛了")

    monkeypatch.setattr(jobs, "due_reminders", boom)
    assert jobs.main(["jobs.py"]) == 0


def test_main_rejects_unknown_slot():
    assert jobs.main(["jobs.py", "1234"]) == 2


def test_daily_report_respects_pause(monkeypatch):
    monkeypatch.delenv("DAILY_REPORT_PAUSED", raising=False)
    assert "暫停" in jobs.daily_report()


# ── 重要 Gmail：用時間窗，不靠記憶體 ──

class _FakeGmail:
    def __init__(self):
        self.query = None

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults):
        self.query = q
        return self

    def execute(self):
        return {"messages": []}


def test_gmail_forward_uses_after_epoch(monkeypatch):
    import alerts
    fake = _FakeGmail()
    monkeypatch.setenv("GMAIL_FORWARD_FROM", "a@x.com")
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "U1")
    monkeypatch.setattr("gmail_reader.get_gmail_service", lambda: fake)
    since = at(15, 0)
    alerts.check_gmail_forward(since=since)
    assert fake.query == f"after:{int(since.timestamp())} (from:a@x.com)"


def test_typhoon_alert_removed():
    import alerts
    assert not hasattr(alerts, "check_typhoon")


# ── 提醒：睡著時到期的要補送，不是默默丟掉 ──

def test_due_reminders_are_sent_once(monkeypatch):
    import notion_db
    import personal
    rows = [
        {"page_id": "p1", "user_id": "U1", "local_id": 1, "text": "喝水", "fire_at": at(10, 0)},
        {"page_id": "p2", "user_id": "U1", "local_id": 2, "text": "開會", "fire_at": at(18, 0)},
    ]
    marked, pushed = [], []
    monkeypatch.setattr(personal, "_notion_enabled", lambda: True)
    monkeypatch.setattr(notion_db, "reminders_load_pending_all", lambda: rows)
    monkeypatch.setattr(notion_db, "reminders_set_status",
                        lambda pid, st: marked.append((pid, st)) or True)
    n = personal.dispatch_due_reminders(lambda uid, text: pushed.append((uid, text)), now=at(15, 0))
    assert n == 1
    assert marked == [("p1", "fired")]
    assert pushed == [("U1", "⏰ 提醒（原定 11/13 10:00）：喝水")]


def test_due_reminder_not_sent_if_mark_fails(monkeypatch):
    import notion_db
    import personal
    rows = [{"page_id": "p1", "user_id": "U1", "local_id": 1, "text": "喝水", "fire_at": at(10, 0)}]
    pushed = []
    monkeypatch.setattr(personal, "_notion_enabled", lambda: True)
    monkeypatch.setattr(notion_db, "reminders_load_pending_all", lambda: rows)
    monkeypatch.setattr(notion_db, "reminders_set_status", lambda pid, st: False)
    assert personal.dispatch_due_reminders(lambda *a: pushed.append(a), now=at(15, 0)) == 0
    assert pushed == []


def test_web_job_skips_reminder_already_sent_by_cron(monkeypatch):
    import notion_db
    import personal
    pushed = []
    monkeypatch.setattr(personal, "_notion_enabled", lambda: True)
    monkeypatch.setattr(notion_db, "reminders_get_status", lambda pid: "fired")
    personal._REMINDERS["U9"] = [{"id": 1, "text": "x", "fire_at": at(10, 0), "page_id": "p9"}]
    personal._fire_reminder("U9", 1, lambda *a: pushed.append(a))
    assert pushed == []


# ── 雷達：有人看才抓 ──

def test_radar_stale_rules():
    import radar
    assert radar.is_stale(None)
    assert radar.is_stale({"at": "2026-11-13 10:00"}, now=at(15, 0))       # 收盤前抓的
    assert not radar.is_stale({"at": "2026-11-13 14:50"}, now=at(15, 0))
    assert not radar.is_stale({"at": "2026-11-12 15:00"}, now=at(9, 0))    # 今天還沒到 14:45
    assert radar.is_stale({"at": "2026-11-12 10:00"}, now=at(9, 0))
