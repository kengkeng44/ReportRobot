"""
管理員錯誤通知。
關鍵異常即時 push 到 ADMIN_LINE_USER_ID（通常是專案維運者本人的 LINE userId）。

設計重點：
- 同錯誤類型 5 分鐘內只通知一次（throttle，避免某 API 持續掛掉時被淹沒）
- notify 自身失敗不能讓主程式炸：所有錯誤往 stderr，最差也只是少一則通知
- 訊息含時間戳（台北時間）、模組、錯誤類型、訊息前 200 字（不含 stack trace）

設定：env var `ADMIN_LINE_USER_ID`（從 webhook 拿到自己訊息時的 source.userId）。
未設定時 notify_admin 是 no-op，不會卡到主流程。
"""

import os
import sys
import threading
from datetime import datetime, timedelta, timezone


_THROTTLE_WINDOW = timedelta(minutes=5)
_LOCK = threading.Lock()
_LAST_NOTIFIED = {}  # 錯誤 key → 上次通知時間（UTC）

_TPE = timezone(timedelta(hours=8))


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


def _admin_user_id():
    return _env("ADMIN_LINE_USER_ID")


def _should_notify(key):
    """同一個 key 在 _THROTTLE_WINDOW 內只放行一次。回 True = 應通知。"""
    now = datetime.now(timezone.utc)
    with _LOCK:
        last = _LAST_NOTIFIED.get(key)
        if last and (now - last) < _THROTTLE_WINDOW:
            return False
        _LAST_NOTIFIED[key] = now
    return True


def notify_admin(error, context=None):
    """
    通知管理員。永不 raise；通知失敗只往 stderr。

    error: Exception 物件（或可轉字串的東西）
    context: dict，常見鍵 module / section / function / extra
    """
    try:
        admin_id = _admin_user_id()
        if not admin_id:
            return  # 沒設管理員 → silent no-op

        ctx = context or {}
        module = ctx.get("module", "unknown")
        section = ctx.get("section") or ctx.get("function") or ""
        err_type = type(error).__name__ if isinstance(error, BaseException) else "Error"
        err_msg = str(error)[:200]

        throttle_key = f"{err_type}:{module}:{section}"
        if not _should_notify(throttle_key):
            print(f"[admin_notify] throttled: {throttle_key}", file=sys.stderr)
            return

        ts = datetime.now(_TPE).strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "⚠️ ReportRobot 異常通知",
            f"時間：{ts} (TPE)",
            f"模組：{module}" + (f" / {section}" if section else ""),
            f"類型：{err_type}",
            f"訊息：{err_msg}",
        ]
        if ctx.get("extra"):
            lines.append(f"備註：{str(ctx['extra'])[:200]}")
        text = "\n".join(lines)

        try:
            from line_sender import push_to_user_sync
            push_to_user_sync(admin_id, text)
        except Exception as send_err:
            print(f"[admin_notify] push 失敗：{send_err}", file=sys.stderr)
    except Exception as outer:
        # 連訊息組合都炸的話，只能 stderr 留底
        print(f"[admin_notify] 自身崩潰：{outer}", file=sys.stderr)
