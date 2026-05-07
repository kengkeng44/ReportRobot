"""
健康 / 習慣追蹤：喝水 / 運動 / 睡眠 / 體重 in-memory 日記。
所有資料以 user_id 隔離。Railway redeploy 會清空（v2 持久化會搬 Postgres）。
"""

import re
import threading
from datetime import date, datetime


_LOCK = threading.Lock()

# user_id → category → list of {date, value, note, ts}
_LOGS = {}

# 標準類別（全部小寫 ASCII key）+ 顯示資訊
_CATEGORIES = {
    "water": {"emoji": "💧", "label": "喝水", "unit": "杯", "default": 1},
    "exercise": {"emoji": "🏃", "label": "運動", "unit": "分鐘", "default": None},
    "sleep": {"emoji": "😴", "label": "睡眠", "unit": "小時", "default": None},
    "weight": {"emoji": "⚖️", "label": "體重", "unit": "kg", "default": None},
}

# 中文 → 標準 key
_KEYWORD_TO_KEY = {
    "喝水": "water", "水": "water", "water": "water",
    "運動": "exercise", "exercise": "exercise", "workout": "exercise",
    "睡眠": "sleep", "睡覺": "sleep", "sleep": "sleep",
    "體重": "weight", "weight": "weight",
}


def parse_keyword(text):
    """嘗試把 text 對應到 health category key。回 None 表示不是 health 指令。"""
    if not text:
        return None
    return _KEYWORD_TO_KEY.get(text.strip().lower())


def log_entry(user_id, key, value=None, note=""):
    """記一筆。value=None 對 water 預設 +1 杯，其他類別必須給 value。"""
    cfg = _CATEGORIES.get(key)
    if not cfg:
        return False, f"未知類別：{key}"
    if value is None:
        if cfg["default"] is None:
            return False, f"請給數值，例：/{cfg['label']} 30"
        value = cfg["default"]
    with _LOCK:
        u = _LOGS.setdefault(user_id, {})
        c = u.setdefault(key, [])
        c.append({
            "date": date.today(),
            "value": float(value),
            "note": (note or "").strip(),
            "ts": datetime.now(),
        })
    return True, _format_today(user_id, key)


def _format_today(user_id, key):
    cfg = _CATEGORIES[key]
    today = date.today()
    with _LOCK:
        rows = [r for r in _LOGS.get(user_id, {}).get(key, []) if r["date"] == today]
    if not rows:
        return f"{cfg['emoji']} 今天還沒記錄 {cfg['label']}"
    if key == "water":
        total = sum(r["value"] for r in rows)
        return f"{cfg['emoji']} 今天喝水 {total:.0f} 杯"
    if key == "exercise":
        total = sum(r["value"] for r in rows)
        last = rows[-1]
        note = f"（{last['note']}）" if last["note"] else ""
        return f"{cfg['emoji']} 今天運動 {total:.0f} 分鐘{note}"
    if key == "sleep":
        last = rows[-1]
        return f"{cfg['emoji']} 睡眠 {last['value']:.1f} 小時"
    if key == "weight":
        last = rows[-1]
        return f"{cfg['emoji']} 體重 {last['value']:.1f} kg"
    return f"{cfg['emoji']} 已記錄"


def format_summary(user_id, period="month"):
    """本週 / 本月健康統計。period in {'week', 'month'}。"""
    today = date.today()
    if period == "week":
        from datetime import timedelta
        start = today - timedelta(days=6)
        title = "📊 本週健康"
    else:
        start = today.replace(day=1)
        title = f"📊 {today.year}-{today.month:02d} 健康月報"

    with _LOCK:
        user_logs = dict(_LOGS.get(user_id, {}))

    if not any(user_logs.values()):
        return (
            f"{title}\n\n"
            "目前沒有紀錄。用法：\n"
            "  /喝水         今天 +1 杯\n"
            "  /喝水 3       今天 +3 杯\n"
            "  /運動 30 跑步  記 30 分鐘 + 備註\n"
            "  /睡眠 7.5\n"
            "  /體重 70.5\n"
            "  /健康         本月統計（這頁）\n"
            "  /健康 週      本週統計"
        )

    lines = [title]
    for key, cfg in _CATEGORIES.items():
        rows = [r for r in user_logs.get(key, []) if r["date"] >= start]
        if not rows:
            continue
        if key in ("water", "exercise"):
            total = sum(r["value"] for r in rows)
            days = len({r["date"] for r in rows})
            avg = total / days if days else 0
            lines.append(
                f"{cfg['emoji']} {cfg['label']}：{days} 天 / 共 {total:.0f} {cfg['unit']}"
                f"（日均 {avg:.1f}）"
            )
        elif key in ("sleep", "weight"):
            vals = [r["value"] for r in rows]
            avg = sum(vals) / len(vals)
            latest = rows[-1]["value"]
            mn, mx = min(vals), max(vals)
            lines.append(
                f"{cfg['emoji']} {cfg['label']}：{len(rows)} 筆 / 平均 {avg:.1f} "
                f"{cfg['unit']}（最新 {latest:.1f} / 區間 {mn:.1f}~{mx:.1f}）"
            )
    return "\n".join(lines)


# ─── command parser helper ───
# 「/喝水」「/喝水 3」「/運動 30 跑步」「/睡眠 7.5」「/體重 70.5」
_LOG_RE = re.compile(r"^([\d.]+)(?:\s+(.+))?$")


def parse_log_args(arg_text):
    """從 /喝水 後面的字串抽 (value, note)。空字串回 (None, '')。"""
    if not arg_text or not arg_text.strip():
        return None, ""
    m = _LOG_RE.match(arg_text.strip())
    if not m:
        return None, arg_text.strip()
    value = float(m.group(1))
    note = (m.group(2) or "").strip()
    return value, note
