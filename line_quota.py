"""
LINE push 月配額計數器 + 警示。

LINE Free Plan 每月 200 則 push（reply 不算）。我們追蹤已用量，超過設定門檻
（預設 80% / 90% / 100%）push 一次 warn 給 admin（每月每門檻只警示一次）。

支援指令：/額度 顯示當月用量。

注意：in-memory，redeploy 重置（同月迄今 push 計數會歸零）。要長期追蹤需要
等 Notion / Postgres 持久化做完。

設定：
- LINE_PUSH_QUOTA：env var，預設 200。Light Plan 是 4000，自行調整。
"""

import os
import threading
from datetime import date, timedelta


_LOCK = threading.Lock()
# date.isoformat() → push 次數
_DAILY_COUNT = {}
# 當月已警示過的門檻 set，避免每天重推
# key 格式：「YYYY-MM:80」
_WARNED = set()


def _quota():
    return int(os.environ.get("LINE_PUSH_QUOTA", "200"))


WARN_THRESHOLDS = (80, 90, 100)


def bump():
    """每次 LINE push 成功呼叫。reply 不要呼叫（LINE reply 不計配額）。"""
    today = date.today().isoformat()
    with _LOCK:
        _DAILY_COUNT[today] = _DAILY_COUNT.get(today, 0) + 1


def get_month_count():
    today = date.today()
    prefix = today.strftime("%Y-%m")
    with _LOCK:
        return sum(c for d, c in _DAILY_COUNT.items() if d.startswith(prefix))


def get_stats():
    today = date.today()
    used = get_month_count()
    quota = _quota()
    pct = (used / quota * 100) if quota > 0 else 0
    # 月底日數
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
    last_day = (next_month - timedelta(days=1)).day
    days_passed = today.day
    days_remaining = last_day - today.day
    daily_avg = used / days_passed if days_passed > 0 else 0
    projected = daily_avg * last_day
    return {
        "month": today.strftime("%Y-%m"),
        "used": used,
        "quota": quota,
        "used_pct": pct,
        "days_passed": days_passed,
        "days_remaining": days_remaining,
        "daily_avg": daily_avg,
        "projected_month_end": projected,
    }


def format_stats():
    s = get_stats()
    pct = s["used_pct"]
    bar_filled = int(pct / 5)  # 20 格表示 100%
    bar_filled = min(bar_filled, 20)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)
    emoji = "🟢" if pct < 60 else "🟡" if pct < 85 else "🔴"
    proj_warn = ""
    if s["projected_month_end"] > s["quota"]:
        proj_warn = "  ⚠️ 月底推估超出配額\n"
    return (
        f"<b>📊 LINE Push 月配額（{s['month']}）</b>\n"
        f"{emoji} 已用 {s['used']} / {s['quota']}（{pct:.1f}%）\n"
        f"  {bar}\n"
        f"  日均 {s['daily_avg']:.1f} 則｜剩餘 {s['days_remaining']} 天\n"
        f"  月底推估：{s['projected_month_end']:.0f} 則\n"
        f"{proj_warn}"
        f"\nℹ️ in-memory 計數，redeploy 後歸零。"
    )


def check_and_warn():
    """每天 09:00 跑一次。超 80%/90%/100% 各推一次 admin（同月每門檻一次）。"""
    s = get_stats()
    pct = s["used_pct"]
    admin_id = os.environ.get("ADMIN_LINE_USER_ID", "")
    if not admin_id:
        return
    month = s["month"]
    for threshold in WARN_THRESHOLDS:
        key = f"{month}:{threshold}"
        if pct >= threshold and key not in _WARNED:
            _WARNED.add(key)
            text = (
                f"⚠️ LINE push 月配額已達 {threshold}%\n"
                f"  已用 {s['used']} / {s['quota']}\n"
                f"  日均 {s['daily_avg']:.1f}｜月底推估 {s['projected_month_end']:.0f}\n"
                f"  剩 {s['days_remaining']} 天\n\n"
                f"超過 200 則的 push 會收費。建議：\n"
                f"  - 暫停未必要的警示（如 /待辦清空 清掉提醒源）\n"
                f"  - 或在 Infisical 把 LINE_PUSH_QUOTA 調高（升 LINE 方案）"
            )
            try:
                # 直接 _post 不走 push_to_user_sync 避免警示自己也被計數成觸發點
                # 但實際上 push_to_user_sync 會 bump，那個 1 次無傷大雅；這裡求簡單就用它
                from line_sender import push_to_user_sync
                push_to_user_sync(admin_id, text)
                print(f"[line_quota] 推送 {threshold}% warn 給 admin")
            except Exception as e:
                print(f"[line_quota] warn push 失敗：{e}")
