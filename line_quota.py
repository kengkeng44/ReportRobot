"""
LINE push 月配額計數器 + 警示。

LINE Free Plan 每月 200 則 push（reply 不算）。我們追蹤已用量，超過設定門檻
（預設 80% / 90% / 100%）push 一次 warn 給 admin（每月每門檻只警示一次）。

支援指令：/額度 顯示當月用量。

持久化：每次 bump 同步寫 Notion LineQuota DB；啟動時從 Notion 載入當月計數。
Notion 失敗 fallback in-memory（不阻塞主流程）。

設定：
- LINE_PUSH_QUOTA：env var，預設 200。Light Plan 是 4000，自行調整。
"""

import os
import threading
from datetime import timedelta

from tz_utils import today_tpe


_LOCK = threading.Lock()
# date.isoformat() → push 次數（in-memory cache）
_DAILY_COUNT = {}
# 當月 Notion 持久化的 base count（啟動時從 Notion load）+ 本期 deploy 累積
# 真實當月用量 = _NOTION_BASE_COUNT[month] + 本期 in-memory daily 加總
_NOTION_BASE_COUNT = {}
_LOADED_MONTHS = set()  # 已 load 過 Notion base 的月份
# 當月已警示過的門檻 set，避免每天重推
# key 格式：「YYYY-MM:80」
_WARNED = set()


def _quota():
    return int(os.environ.get("LINE_PUSH_QUOTA", "200"))


WARN_THRESHOLDS = (80, 90, 100)


def _ensure_loaded(month_str):
    """確保當月 base count 已從 Notion load 過（每月只 load 一次）。"""
    if month_str in _LOADED_MONTHS:
        return
    try:
        import notion_db
        if not notion_db.is_configured():
            _LOADED_MONTHS.add(month_str)
            return
        _, base = notion_db.quota_get_month(month_str)
        with _LOCK:
            _NOTION_BASE_COUNT[month_str] = base
            _LOADED_MONTHS.add(month_str)
        if base > 0:
            print(f"[line_quota] 從 Notion load {month_str}={base}")
    except Exception as e:
        print(f"[line_quota] load Notion 失敗：{e}")
        _LOADED_MONTHS.add(month_str)  # 標記避免每次都試


def _persist_to_notion():
    """把當月最新總數同步寫 Notion（best-effort）。"""
    try:
        import notion_db
        if not notion_db.is_configured():
            return
        today = today_tpe()
        month_str = today.strftime("%Y-%m")
        total = get_month_count()
        notion_db.quota_set_month(month_str, total)
    except Exception as e:
        print(f"[line_quota] persist Notion 失敗：{e}")


def bump():
    """每次 LINE push 成功呼叫。reply 不要呼叫（LINE reply 不計配額）。"""
    today_iso = today_tpe().isoformat()
    month_str = today_tpe().strftime("%Y-%m")
    _ensure_loaded(month_str)
    with _LOCK:
        _DAILY_COUNT[today_iso] = _DAILY_COUNT.get(today_iso, 0) + 1
    _persist_to_notion()


def get_month_count():
    """真實當月用量 = Notion 載入的 base + 本期 deploy 後的 in-memory 累積。"""
    today = today_tpe()
    month_str = today.strftime("%Y-%m")
    _ensure_loaded(month_str)
    prefix = month_str
    with _LOCK:
        in_mem = sum(c for d, c in _DAILY_COUNT.items() if d.startswith(prefix))
        base = _NOTION_BASE_COUNT.get(month_str, 0)
    return base + in_mem


def get_stats():
    today = today_tpe()
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
    try:
        import notion_db
        persist = "✅ Notion 持久化" if notion_db.is_configured() else "⚠️ in-memory 計數（redeploy 歸零）"
    except Exception:
        persist = "⚠️ in-memory 計數（redeploy 歸零）"
    return (
        f"<b>📊 LINE Push 月配額（{s['month']}）</b>\n"
        f"{emoji} 已用 {s['used']} / {s['quota']}（{pct:.1f}%）\n"
        f"  {bar}\n"
        f"  日均 {s['daily_avg']:.1f} 則｜剩餘 {s['days_remaining']} 天\n"
        f"  月底推估：{s['projected_month_end']:.0f} 則\n"
        f"{proj_warn}"
        f"\nℹ️ {persist}"
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
