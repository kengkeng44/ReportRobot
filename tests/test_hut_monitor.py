from datetime import date, datetime, timedelta, timezone

import pytest

import hut_monitor as hm
from hut_sources import Slot

TPE = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 2, 16, 30, tzinfo=TPE)
TODAY = NOW.date()
SAT = date(2026, 12, 5)   # 33 天後；先到先得在範圍內，抽籤制（≤30 天）不在
FRI = date(2026, 11, 27)  # 25 天後


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    hm.reset()
    monkeypatch.delenv("HUT_FOREST_ON_SERVER", raising=False)
    monkeypatch.setattr(hm, "_sync_in_background", lambda *a: None)
    yield
    hm.reset()


def _run(slots, errors=None, now=NOW, **kw):
    sent, alerts = [], []
    out = hm.run_check(now=now, fetch=lambda today: (list(slots), dict(errors or {})),
                       send=lambda subject, text: sent.append((subject, text)) or True,
                       notify=lambda err, ctx: alerts.append(ctx["section"]), **kw)
    return out, sent, alerts


def _s(site, unit, day, remaining, kind="bed"):
    return Slot(site, unit, kind, day, remaining, 5)


def test_watchable_window():
    assert hm.watchable(_s("tconline", "天池山莊", SAT, 1), TODAY)       # 先到先得 33 天
    assert not hm.watchable(_s("jmlnt", "嘉明湖山屋", SAT, 1), TODAY)    # 抽籤制要 ≤30 天
    assert hm.watchable(_s("jmlnt", "嘉明湖山屋", FRI, 1), TODAY)
    assert not hm.watchable(_s("tconline", "天池山莊", date(2026, 11, 5), 1), TODAY)  # <5 天
    assert not hm.watchable(_s("tconline", "天池山莊", date(2027, 1, 10), 1), TODAY)  # >61 天
    assert not hm.watchable(_s("tconline", "天池山莊", date(2026, 11, 25), 1), TODAY)  # 週三不看
    assert hm.watchable(_s("tconline", "天池山莊", date(2026, 11, 29), 1), TODAY)      # 週日看


def test_first_run_is_baseline_then_increase_is_change():
    out, sent, _ = _run([_s("tconline", "天池山莊", SAT, 0)])
    assert out["changes"] == 0 and sent == []
    out, sent, _ = _run([_s("tconline", "天池山莊", SAT, 3)])
    assert out["changes"] == 1
    assert sent[0][0] == "山屋：天池山莊 12/5(六) 釋出 3床"


def test_decrease_and_zero_are_not_changes():
    _run([_s("tconline", "天池山莊", SAT, 5)])
    out, sent, _ = _run([_s("tconline", "天池山莊", SAT, 2)])
    assert out["changes"] == 0 and sent == []


def test_date_entering_window_with_known_value_is_not_new():
    """抽籤制的日期進入 30 天範圍時，上次已有值就照常比，不算「新」。"""
    day = date(2026, 12, 3)  # 31 天後，不在嘉明湖範圍
    _run([_s("jmlnt", "嘉明湖山屋", day, 4)])
    tomorrow = NOW + timedelta(days=1)  # 變 30 天，進範圍
    out, _, _ = _run([_s("jmlnt", "嘉明湖山屋", day, 4)], now=tomorrow)
    assert out["changes"] == 0


def test_failed_site_keeps_old_snapshot_and_alerts_on_third():
    _run([_s("tconline", "天池山莊", SAT, 0)])
    for i in range(3):
        _, _, alerts = _run([], {"tconline": "HTTP 500"})
    assert alerts == ["tconline"]
    out, _, _ = _run([_s("tconline", "天池山莊", SAT, 2)])
    assert out["changes"] == 1  # 舊快照 0 還在，2 算變多


def test_onestop_missing_is_error_and_fresh_ingest_is_used():
    out, _, _ = _run([])
    assert out["errors"]["onestop_paiyun"] == "本次沒查到（電腦沒開）"
    payload = {"slots": [_s("onestop_99", "九九山莊", SAT, 0).to_dict()],
               "errors": {"onestop_paiyun": "被網站擋下（Request Rejected），這次停止"}}
    hm.ingest(payload, now=NOW)
    out, _, _ = _run([])
    assert "onestop_99" not in out["errors"]
    assert out["errors"]["onestop_paiyun"].startswith("被網站擋下")
    hm.ingest({"slots": [_s("onestop_99", "九九山莊", SAT, 4).to_dict()]}, now=NOW)
    out, _, _ = _run([])
    assert out["changes"] == 1


def test_pc_off_never_alerts():
    for i in range(4):
        _, _, alerts = _run([])
        assert alerts == []


def test_stale_ingest_ignored():
    hm.ingest({"slots": [_s("onestop_99", "九九山莊", SAT, 4).to_dict()]},
              now=NOW - timedelta(hours=2))
    out, _, _ = _run([])
    assert out["errors"]["onestop_99"] == "本次沒查到（電腦沒開）"


def test_ingest_rejects_unknown_site():
    with pytest.raises(ValueError):
        hm.ingest({"slots": [_s("evil", "x", SAT, 1).to_dict()]}, now=NOW)


def test_forest_off_server_expects_ingest(monkeypatch):
    monkeypatch.setenv("HUT_FOREST_ON_SERVER", "0")
    called = []
    hm.run_check(now=NOW, fetch=lambda today: called.append(1) or ([], {}),
                 send=lambda s, t: True, notify=lambda e, c: None)
    assert called == []
    assert "jmlnt" in hm.status()["errors"]


def test_events_mail_only_when_asked():
    _, sent, _ = _run([])
    assert sent == []
    _, sent, _ = _run([], send_events_today=True)
    assert sent and sent[0][0].startswith("山屋：今天")


def test_line_reply():
    assert "還沒查過" in hm.line_reply(today=TODAY)
    _run([_s("tconline", "天池山莊", SAT, 4)])
    text = hm.line_reply(today=TODAY)
    assert "天池山莊：12/5(六) 4床" in text


def test_send_failure_rolls_back_and_alerts():
    _run([_s("tconline", "天池山莊", SAT, 0)])
    alerts = []

    def boom(subject, text):
        raise RuntimeError("smtp down")

    slots = [_s("tconline", "天池山莊", SAT, 3)]
    out = hm.run_check(now=NOW, fetch=lambda today: (list(slots), {}), send=boom,
                       notify=lambda err, ctx: alerts.append(ctx["section"]))
    assert out["changes"] == 1 and out["sent"] is False
    assert "send" in alerts
    out, sent, _ = _run(slots)
    assert out["changes"] == 1 and len(sent) == 1


def test_send_failure_rolls_back_new_key():
    _run([_s("tconline", "天池山莊", SAT, 0)])
    new_day = date(2026, 12, 6)
    slots = [_s("tconline", "天池山莊", SAT, 0), _s("tconline", "天池山莊", new_day, 2)]

    def boom(subject, text):
        raise RuntimeError("x")

    hm.run_check(now=NOW, fetch=lambda today: (list(slots), {}), send=boom,
                 notify=lambda e, c: None)
    out, sent, _ = _run(slots)
    assert out["changes"] == 1 and len(sent) == 1


def test_send_returns_false_no_rollback():
    _run([_s("tconline", "天池山莊", SAT, 0)])
    slots = [_s("tconline", "天池山莊", SAT, 3)]
    out = hm.run_check(now=NOW, fetch=lambda today: (list(slots), {}),
                       send=lambda s, t: False, notify=lambda e, c: None)
    assert out["changes"] == 1 and out["sent"] is False
    out, sent, _ = _run(slots)
    assert out["changes"] == 0 and sent == []


def test_past_days_pruned_from_snapshot():
    _run([_s("tconline", "天池山莊", SAT, 0)])
    assert any(k[2] == SAT.isoformat() for k in hm._snapshot)
    later = datetime(2026, 12, 20, 16, 30, tzinfo=TPE)
    _run([], now=later)
    assert not hm._snapshot


THU = date(2026, 12, 3)  # 31 天後，週四


def test_weekday_increase_not_mailed_and_not_rereported():
    _run([_s("tconline", "天池山莊", THU, 0)])
    out, sent, _ = _run([_s("tconline", "天池山莊", THU, 3)])
    assert out["changes"] == 0 and sent == []
    out, sent, _ = _run([_s("tconline", "天池山莊", THU, 3)])
    assert out["changes"] == 0 and sent == []


def test_weekend_increase_still_mailed_with_weekday_mixed():
    _run([_s("tconline", "天池山莊", THU, 0), _s("tconline", "天池山莊", SAT, 0)])
    out, sent, _ = _run([_s("tconline", "天池山莊", THU, 3), _s("tconline", "天池山莊", SAT, 2)])
    assert out["changes"] == 1
    assert "12/5(六)" in sent[0][0]


def test_sync_called_with_all_slots_and_errors():
    calls = []
    slots = [_s("tconline", "天池山莊", THU, 1)]
    hm.run_check(now=NOW, fetch=lambda today: (list(slots), {"jmlnt": "HTTP 500"}),
                 send=lambda s, t: True, notify=lambda e, c: None,
                 sync=lambda sl, er, now, notify: calls.append((sl, er, now)))
    sl, er, now = calls[0]
    assert sl == slots and er["jmlnt"] == "HTTP 500" and now == NOW


def test_notion_job_alerts_on_third_failure(monkeypatch):
    import hut_notion
    def boom(*a, **k):
        raise RuntimeError("notion down")
    monkeypatch.setattr(hut_notion, "sync", boom)
    alerts = []
    for _ in range(3):
        hm._notion_job([], {}, NOW, lambda e, c: alerts.append(c["section"]))
    assert alerts == ["notion"]


def test_notion_job_success_resets_count(monkeypatch):
    import hut_notion
    results = iter([RuntimeError("x"), RuntimeError("x"), None, RuntimeError("x"), RuntimeError("x")])
    def flaky(*a, **k):
        r = next(results)
        if r:
            raise r
    monkeypatch.setattr(hut_notion, "sync", flaky)
    alerts = []
    for _ in range(5):
        hm._notion_job([], {}, NOW, lambda e, c: alerts.append(c["section"]))
    assert alerts == []


def test_restore_from_notion_then_first_run_reports():
    rows = {"tconline|天池山莊|2026-12-05": ("p1", 0, 5),
            "tconline|站況": ("p2", None, None),
            "onestop_99|九九山莊|2026-12-05": ("p3", None, 3)}
    n = hm.restore_from_notion(today=TODAY, load=lambda today: rows)
    assert n == 1
    out, sent, _ = _run([_s("tconline", "天池山莊", SAT, 3)])
    assert out["changes"] == 1 and sent


def test_restore_skipped_when_snapshot_exists():
    _run([_s("tconline", "天池山莊", SAT, 0)])
    n = hm.restore_from_notion(today=TODAY, load=lambda today: {"tconline|天池山莊|2026-12-05": ("p", 9, 1)})
    assert n == 0
    assert hm._snapshot[("tconline", "天池山莊", "2026-12-05")] == 0


def test_restore_failure_falls_back_to_baseline():
    def boom(today):
        raise RuntimeError("notion down")
    assert hm.restore_from_notion(today=TODAY, load=boom) == 0
    out, sent, _ = _run([_s("tconline", "天池山莊", SAT, 3)])
    assert out["changes"] == 0  # 照舊第一次只建基準
