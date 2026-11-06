"""admin_notify 的通知節流。

這裡的核心 bug（2026-08-19 修）：_THROTTLE_WINDOW 原本是 5 分鐘，而
alerts_loop 的輪詢間隔也正好是 5 分鐘 —— 閘門關閉的時間跟水來的間隔一樣長，
等於沒有閘門。CWA 掛一小時就會推 12 則異常通知，還每則都吃 LINE 月配額。
"""

from datetime import datetime, timedelta, timezone

import pytest

import admin_notify


UTC = timezone.utc
T0 = datetime(2026, 8, 19, 5, 11, 0, tzinfo=UTC)  # 台北 13:11


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每個測試都從乾淨狀態開始,並確保不會真的推 LINE。"""
    admin_notify._STATE.clear()
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "U-test")
    sent = []
    import line_sender
    monkeypatch.setattr(line_sender, "push_to_user_sync",
                        lambda uid, text: sent.append(text))
    return sent


def _at(monkeypatch, moment):
    """把 admin_notify 眼中的「現在」固定在 moment。"""
    monkeypatch.setattr(admin_notify, "_now", lambda: moment)


def _boom(monkeypatch, moment, confirm_first=True):
    """模擬一次 http_utils 連線逾時。"""
    _at(monkeypatch, moment)
    admin_notify.notify_admin(
        TimeoutError("connect timeout"),
        {"module": "http_utils", "function": "get"},
        confirm_first=confirm_first,
    )


# ── 背景輪詢:偶發不吵,持續才吵 ────────────────────────────

def test_single_blip_stays_silent(monkeypatch, _clean):
    """13:11 那種偶發一次的 connect timeout,不該打擾使用者。"""
    _boom(monkeypatch, T0)
    assert _clean == []


def test_two_blips_still_silent(monkeypatch, _clean):
    """CWA 每 5 分鐘輪詢，連續逾時兩輪是每天都會發生的事，不值得推。"""
    _boom(monkeypatch, T0)
    _boom(monkeypatch, T0 + timedelta(minutes=5))
    assert _clean == []


def test_third_failure_within_hour_notifies(monkeypatch, _clean):
    """一小時內失敗滿 3 次 = 不是偶發，這時才值得通知。"""
    _boom(monkeypatch, T0)
    _boom(monkeypatch, T0 + timedelta(minutes=5))
    _boom(monkeypatch, T0 + timedelta(minutes=40))
    assert len(_clean) == 1
    assert "connect timeout" in _clean[0]


def test_scattered_blips_never_add_up(monkeypatch, _clean):
    """一天裡零星抖幾下（間隔超過一小時）不該被累積成通知。"""
    for h in range(0, 24, 2):
        _boom(monkeypatch, T0 + timedelta(hours=h))
        _boom(monkeypatch, T0 + timedelta(hours=h, minutes=5))
    assert _clean == []


def test_silence_window_after_notifying(monkeypatch, _clean):
    """通知過就安靜一段時間，不要每 5 分鐘吵一次。"""
    for m in (0, 5, 10):                               # 第 3 次通知
        _boom(monkeypatch, T0 + timedelta(minutes=m))
    for m in (15, 20, 25, 30, 35):
        _boom(monkeypatch, T0 + timedelta(minutes=m))
    assert len(_clean) == 1


def test_notifies_again_after_silence_window(monkeypatch, _clean):
    """還在壞就該再提醒一次，只是頻率低很多。"""
    for m in range(0, 45, 5):
        _boom(monkeypatch, T0 + timedelta(minutes=m))
    assert len(_clean) == 2                            # 10 分、40 分


def test_one_hour_outage_sends_few_not_a_dozen(monkeypatch, _clean):
    """CWA 掛一小時，每 5 分鐘一次共 12 輪。"""
    for i in range(12):
        _boom(monkeypatch, T0 + timedelta(minutes=5 * i))
    assert len(_clean) <= 3, f"一小時的故障推了 {len(_clean)} 則，太吵"
    assert len(_clean) >= 1, "持續一小時的故障不能完全不通知"


def test_blip_after_past_outage_stays_silent(monkeypatch, _clean):
    """昨天真的壞過、通知過；今天偶發一次不該直接推（舊版會）。"""
    for m in (0, 5, 10):
        _boom(monkeypatch, T0 + timedelta(minutes=m))
    assert len(_clean) == 1
    _boom(monkeypatch, T0 + timedelta(hours=20))
    assert len(_clean) == 1


def test_old_observation_expires(monkeypatch, _clean):
    """昨天抖一下、今天又抖一下，是兩次偶發，不該被算成「持續故障」。"""
    _boom(monkeypatch, T0)
    _boom(monkeypatch, T0 + timedelta(hours=20))
    _boom(monkeypatch, T0 + timedelta(hours=20, minutes=5))
    assert _clean == []


def test_api_key_in_url_is_masked(monkeypatch, _clean):
    """requests 的錯誤訊息會帶完整網址，CWA 金鑰不能跟著推到 LINE。"""
    _at(monkeypatch, T0)
    admin_notify.notify_admin(
        RuntimeError("Max retries exceeded with url: /api/v1/rest/datastore/"
                     "W-C0033-001?Authorization=CWA-SECRET-1234 (Caused by X)"),
        {"module": "http_utils", "extra": "url=https://x/?token=abc&q=1"},
    )
    assert "CWA-SECRET-1234" not in _clean[0]
    assert "abc" not in _clean[0]
    assert "Authorization=***" in _clean[0]
    assert "q=1" in _clean[0]


# ── 關鍵任務:第一次就要通知 ──────────────────────────────

def test_critical_failure_notifies_immediately(monkeypatch, _clean):
    """每日推播一天只跑一次,要求「連續兩次」等於明天才通知,太遲。"""
    _at(monkeypatch, T0)
    admin_notify.notify_admin(
        RuntimeError("天氣整段炸了"),
        {"module": "daily_report", "section": "天氣"},
    )
    assert len(_clean) == 1


def test_critical_still_throttled_when_repeating(monkeypatch, _clean):
    """立即通知不等於可以洗版。"""
    _at(monkeypatch, T0)
    admin_notify.notify_admin(RuntimeError("炸了"), {"module": "daily_report"})
    _at(monkeypatch, T0 + timedelta(minutes=1))
    admin_notify.notify_admin(RuntimeError("炸了"), {"module": "daily_report"})
    assert len(_clean) == 1


# ── 分流 ─────────────────────────────────────────────────

def test_different_keys_are_independent(monkeypatch, _clean):
    """CWA 掛掉不該讓 Gmail 的故障被一起吃掉。"""
    _at(monkeypatch, T0)
    admin_notify.notify_admin(RuntimeError("A"), {"module": "daily_report", "section": "天氣"})
    admin_notify.notify_admin(RuntimeError("B"), {"module": "daily_report", "section": "盤前"})
    assert len(_clean) == 2


def test_message_contains_module_and_type(monkeypatch, _clean):
    _at(monkeypatch, T0)
    admin_notify.notify_admin(RuntimeError("壞掉了"), {"module": "daily_report", "section": "天氣"})
    text = _clean[0]
    assert "daily_report" in text
    assert "天氣" in text
    assert "RuntimeError" in text
    assert "壞掉了" in text


def test_no_admin_id_is_noop(monkeypatch, _clean):
    monkeypatch.delenv("ADMIN_LINE_USER_ID", raising=False)
    monkeypatch.setattr(admin_notify, "_env", lambda name: "")
    _at(monkeypatch, T0)
    admin_notify.notify_admin(RuntimeError("x"), {"module": "daily_report"})
    assert _clean == []


def test_notify_never_raises(monkeypatch, _clean):
    """通知失敗絕不能拖垮主流程。"""
    import line_sender

    def boom(uid, text):
        raise RuntimeError("LINE 掛了")

    monkeypatch.setattr(line_sender, "push_to_user_sync", boom)
    _at(monkeypatch, T0)
    admin_notify.notify_admin(RuntimeError("x"), {"module": "daily_report"})  # 不該炸
