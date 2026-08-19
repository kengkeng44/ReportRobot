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


def test_second_failure_confirms_and_notifies(monkeypatch, _clean):
    """5 分鐘後又失敗 = 不是偶發,這時才值得通知。"""
    _boom(monkeypatch, T0)
    _boom(monkeypatch, T0 + timedelta(minutes=5))
    assert len(_clean) == 1
    assert "connect timeout" in _clean[0]


def test_silence_window_after_notifying(monkeypatch, _clean):
    """通知過就安靜一段時間,不要每 5 分鐘吵一次。"""
    _boom(monkeypatch, T0)
    _boom(monkeypatch, T0 + timedelta(minutes=5))     # 通知
    for extra in (10, 15, 20, 25, 30):
        _boom(monkeypatch, T0 + timedelta(minutes=extra))
    assert len(_clean) == 1


def test_notifies_again_after_silence_window(monkeypatch, _clean):
    """還在壞就該再提醒一次,只是頻率低很多。"""
    _boom(monkeypatch, T0)
    _boom(monkeypatch, T0 + timedelta(minutes=5))     # 第 1 則
    _boom(monkeypatch, T0 + timedelta(minutes=40))    # 超過靜默窗 → 第 2 則
    assert len(_clean) == 2


def test_one_hour_outage_sends_few_not_a_dozen(monkeypatch, _clean):
    """這就是原本的 bug:CWA 掛一小時,每 5 分鐘一次共 12 輪。"""
    for i in range(12):
        _boom(monkeypatch, T0 + timedelta(minutes=5 * i))
    assert len(_clean) <= 3, f"一小時的故障推了 {len(_clean)} 則,太吵"
    assert len(_clean) >= 1, "持續一小時的故障不能完全不通知"


def test_old_observation_expires(monkeypatch, _clean):
    """昨天抖一下、今天又抖一下,是兩次偶發,不該被算成「持續故障」。"""
    _boom(monkeypatch, T0)
    _boom(monkeypatch, T0 + timedelta(hours=20))
    assert _clean == []


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
