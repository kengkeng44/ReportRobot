"""
管理員錯誤通知。
關鍵異常即時 push 到 ADMIN_LINE_USER_ID（通常是專案維運者本人的 LINE userId）。

設計重點：
- 同錯誤類型 30 分鐘內只通知一次（throttle）；背景輪詢要 1 小時內失敗 3 次才通知
- 訊息裡網址參數的金鑰（Authorization=、token= 等）一律遮成 ***
- notify 自身失敗不能讓主程式炸：所有錯誤往 stderr，最差也只是少一則通知
- 訊息含時間戳（台北時間）、模組、錯誤類型、訊息前 200 字（不含 stack trace）

設定：env var `ADMIN_LINE_USER_ID`（從 webhook 拿到自己訊息時的 source.userId）。
未設定時 notify_admin 是 no-op，不會卡到主流程。
"""

import os
import re
import sys
import threading
from datetime import datetime, timedelta, timezone


# 這段時間內失敗滿 _CONFIRM_COUNT 次，才算「真的壞了」而不是網路抖一下。
# 原本是 15 分鐘內 2 次：alerts_loop 每 5 分鐘打一次 CWA、一天 288 次，
# Railway 從海外連 CWA 偶爾連續逾時兩輪，結果幾乎天天跳通知。
# 真的掛掉時一小時會失敗 12 次，3 次的門檻照樣抓得到。
_CONFIRM_WINDOW = timedelta(minutes=60)
_CONFIRM_COUNT = 3
# 通知過之後安靜多久。務必 > 呼叫端的輪詢間隔，否則等於沒有節流 ——
# 原本這裡是 5 分鐘、alerts_loop 也是 5 分鐘，CWA 掛一小時就推了 12 則。
_SILENCE_WINDOW = timedelta(minutes=30)

_LOCK = threading.Lock()
# 錯誤 key → {"fails": 觀察窗內的失敗時間, "notified": 上次通知時間 or None,
#            "last": 最近一次失敗}
_STATE = {}

_TPE = timezone(timedelta(hours=8))


def _now():
    """抽成函式是為了讓測試能把時間釘住。"""
    return datetime.now(timezone.utc)


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


def _should_notify(key, confirm_first=False):
    """這次要不要真的推通知。回 True = 應通知。

    confirm_first=True（高頻背景輪詢，例如 http_utils 的連線失敗）：
        第一次失敗只記錄不吵 —— 連線逾時這種故障通常下一輪就自己好了，
        為它推一則通知純粹是噪音，而且異常通知本身也吃 LINE 月配額。
        要在 _CONFIRM_WINDOW 內累積 _CONFIRM_COUNT 次失敗，才算真的壞掉。

    confirm_first=False（低頻關鍵任務，例如每日推播）：
        第一次就通知 —— 一天只跑一次的東西，要求「連續兩次」等於明天才告訴你。

    兩者共用 _SILENCE_WINDOW：通知過就安靜一陣子，避免洗版。
    """
    now = _now()
    with _LOCK:
        st = _STATE.get(key)

        # 上一次失敗已經是一個窗口以前 → 那次故障早就結束，這是新的一次，
        # 從頭觀察。少了這段的話，通知過一次之後，之後每次偶發都會直接推。
        if st is not None and now - st["last"] > _CONFIRM_WINDOW:
            st = None

        if st is None:
            st = _STATE[key] = {"fails": [], "notified": None, "last": now}
            if not confirm_first:
                st["notified"] = now
                return True
        st["last"] = now

        if st["notified"] is not None:
            if now - st["notified"] < _SILENCE_WINDOW:
                return False
            st["notified"] = now
            return True

        # 還在觀察中（只可能是 confirm_first 的路徑）：只留窗口內的失敗
        st["fails"] = [t for t in st["fails"] if now - t <= _CONFIRM_WINDOW]
        st["fails"].append(now)
        if len(st["fails"]) < _CONFIRM_COUNT:
            return False
        st["notified"] = now
        return True


# 網址參數裡的金鑰（CWA 的 Authorization=CWA-xxx 會跟著 requests 的錯誤訊息出來）
_SECRET_PARAM = re.compile(
    r"((?:authorization|api_?key|key|token|access_token|secret)=)[^&\s)'\"]+",
    re.IGNORECASE,
)


def _redact(text):
    return _SECRET_PARAM.sub(lambda m: m.group(1) + "***", text)


def notify_admin(error, context=None, confirm_first=False):
    """
    通知管理員。永不 raise；通知失敗只往 stderr。

    error: Exception 物件（或可轉字串的東西）
    context: dict，常見鍵 module / section / function / extra
    confirm_first: True 表示這是高頻背景輪詢的失敗，要再失敗一次才通知
                   （見 _should_notify）。一天只跑一次的任務不要開。
    """
    try:
        admin_id = _admin_user_id()
        if not admin_id:
            return  # 沒設管理員 → silent no-op

        ctx = context or {}
        module = ctx.get("module", "unknown")
        section = ctx.get("section") or ctx.get("function") or ""
        err_type = type(error).__name__ if isinstance(error, BaseException) else "Error"
        err_msg = _redact(str(error))[:200]

        throttle_key = f"{err_type}:{module}:{section}"
        if not _should_notify(throttle_key, confirm_first):
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
            lines.append(f"備註：{_redact(str(ctx['extra']))[:200]}")
        text = "\n".join(lines)

        try:
            from line_sender import push_to_user_sync
            push_to_user_sync(admin_id, text)
        except Exception as send_err:
            print(f"[admin_notify] push 失敗：{send_err}", file=sys.stderr)
    except Exception as outer:
        # 連訊息組合都炸的話，只能 stderr 留底
        print(f"[admin_notify] 自身崩潰：{outer}", file=sys.stderr)
