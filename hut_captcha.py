"""山屋申請的驗證碼轉手：本機截圖 → LINE 傳給本人 → 本人回驗證碼 → 本機填入送出。

流程：
  1. 本機 tools/hut_apply.py 填好表、截下驗證碼，POST /admin/hut-captcha
     {"image_b64", "summary", "step"} → 這裡存起來、LINE 推「摘要＋驗證碼圖」給 ADMIN_LINE_USER_ID
  2. 本人在 LINE 私訊回「驗證碼 1234」（或「驗證碼 取消」）
  3. 本機輪詢 GET /admin/hut-captcha/<id> 拿到 code → 填入、送出
  4. 本機 POST /admin/hut-captcha/<id>/result 回報結果截圖 → LINE 推給本人

只放記憶體、CAPTCHA_TTL 後過期；同時只留一個進行中的請求（新的蓋掉舊的，舊的變 cancelled）。
本人回覆驗證碼＝同意送出這一筆，所以摘要要寫清楚是哪一筆。
圖片網址 /hut-img/<token>.<ext>：token 隨機，猜不到就看不到；過期就 404。
"""

import os
import re
import secrets
import threading
from datetime import timedelta

from tz_utils import now_tpe

CAPTCHA_TTL = timedelta(minutes=5)
RESULT_TTL = timedelta(minutes=30)   # 結果截圖留久一點，LINE 抓圖可能晚一點
MAX_IMAGE = 2 * 1024 * 1024          # LINE 預覽圖上限 1MB、原圖 10MB；本機會先壓 JPEG
KEYWORD = "驗證碼"
CANCEL_WORDS = {"取消", "不要", "cancel", "stop"}
_REPLY = re.compile(r"^/?驗證碼\s*[:：]?\s*(.*)$", re.S)

_LOCK = threading.Lock()
_current = {}   # 目前這一筆：id, created, summary, step, state, code
_images = {}    # token -> (bytes, mime, expires_at)


def reset():
    """測試用。"""
    with _LOCK:
        _current.clear()
        _images.clear()


def _mime(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    raise ValueError("圖片只收 PNG 或 JPEG")


def _put_image(data, now, ttl):
    if not data:
        raise ValueError("沒有圖片")
    if len(data) > MAX_IMAGE:
        raise ValueError(f"圖片太大（{len(data)} bytes，上限 {MAX_IMAGE}）")
    mime, ext = _mime(data)
    token = secrets.token_urlsafe(16)
    _images[token] = (data, mime, now + ttl)
    for t in [t for t, (_, _, exp) in _images.items() if exp < now]:
        _images.pop(t, None)
    return f"{token}.{ext}"


def get_image(name, now=None):
    """/hut-img/<name> 用。回 (bytes, mime) 或 None。"""
    now = now or now_tpe()
    token = name.rsplit(".", 1)[0]
    with _LOCK:
        got = _images.get(token)
    if not got or got[2] < now:
        return None
    return got[0], got[1]


def _public_base():
    domain = (os.environ.get("RAILWAY_PUBLIC_DOMAIN")
              or "chengreportbot-production.up.railway.app")
    return f"https://{domain}"


def _image_message(name):
    url = f"{_public_base()}/hut-img/{name}"
    return {"type": "image", "originalContentUrl": url, "previewImageUrl": url}


def _push(messages):
    admin = os.environ.get("ADMIN_LINE_USER_ID", "")
    if not admin:
        raise RuntimeError("沒設 ADMIN_LINE_USER_ID，驗證碼傳不出去")
    import line_sender
    if not line_sender.push_batch_to_user_sync(admin, messages):   # 文字＋圖一次 push，配額算 1
        raise RuntimeError("LINE 推播失敗，驗證碼沒傳到")


def _expire(now):
    if _current.get("state") == "waiting" and now - _current["created"] > CAPTCHA_TTL:
        _current["state"] = "expired"


def open_challenge(image, summary, step="apply", now=None, push=None):
    """本機送來一張驗證碼圖。回 {"id", "expires_in"}。"""
    now = now or now_tpe()
    summary = (summary or "").strip()[:1500]
    if not summary:
        raise ValueError("要附摘要（哪個山屋、哪天、幾人），不然你不知道自己在同意什麼")
    step = "login" if step == "login" else "apply"
    with _LOCK:
        if _current.get("state") == "waiting":
            _current["state"] = "cancelled"
        name = _put_image(image, now, CAPTCHA_TTL)
        cid = secrets.token_urlsafe(9)
        _current.clear()
        _current.update(id=cid, created=now, summary=summary, step=step,
                        state="waiting", code=None)
    head = "🔐 登入要驗證碼" if step == "login" else "🏔️ 準備送出山屋申請"
    mins = int(CAPTCHA_TTL.total_seconds() // 60)
    text = (f"{head}\n{summary}\n\n"
            f"看圖回「驗證碼 XXXX」就{'登入' if step == 'login' else '送出'}；"
            f"不要就回「驗證碼 取消」。{mins} 分鐘內有效。")
    try:
        (push or _push)([text, _image_message(name)])
    except Exception:
        with _LOCK:
            if _current.get("id") == cid:
                _current["state"] = "cancelled"   # 你沒看到圖，就不能算在等你
        raise
    return {"id": cid, "expires_in": int(CAPTCHA_TTL.total_seconds())}


def poll(cid, now=None):
    """本機輪詢。state: waiting / answered / cancelled / expired / unknown。"""
    now = now or now_tpe()
    with _LOCK:
        if _current.get("id") != cid:
            return {"state": "unknown"}
        _expire(now)
        out = {"state": _current["state"]}
        if _current["state"] == "answered":
            out["code"] = _current["code"]
        return out


def is_reply(text):
    return bool(_REPLY.match((text or "").strip()))


def answer_from_line(text, now=None):
    """LINE 私訊「驗證碼 1234」／「驗證碼 取消」。回要給本人的回覆字串。"""
    now = now or now_tpe()
    m = _REPLY.match((text or "").strip())
    raw = (m.group(1) if m else "").strip()
    code = re.sub(r"\s+", "", raw)
    with _LOCK:
        if not _current:
            return "現在沒有在等驗證碼。"
        _expire(now)
        state = _current["state"]
        if state == "expired":
            return "這張驗證碼已經過期了，這次不送。下次有空位會再傳新的。"
        if state != "waiting":
            return "這一筆已經處理過了。"
        if code.lower() in CANCEL_WORDS:
            _current["state"] = "cancelled"
            return "好，這筆取消，不送出。"
        if not code or len(code) > 12:
            return "沒看到驗證碼。請回「驗證碼 1234」這樣。"
        _current.update(state="answered", code=code)
        step = _current["step"]
    return f"收到 {code}，{'登入中' if step == 'login' else '送出中'}…結果等一下傳給你。"


def report_result(cid, text, image=None, now=None, push=None):
    """本機回報送出結果。"""
    now = now or now_tpe()
    text = (text or "").strip()[:1500] or "（沒有說明）"
    with _LOCK:
        if _current.get("id") != cid:
            raise ValueError("不認得這個 id（可能已被新的請求蓋掉）")
        _current["state"] = "done"
        name = _put_image(image, now, RESULT_TTL) if image else None
    messages = [text] + ([_image_message(name)] if name else [])
    (push or _push)(messages)
    return {"ok": True}


def notify(text, image=None, now=None, push=None):
    """本機在沒有驗證碼的時候要告訴本人的事（例如網站改版、登入失敗）。"""
    now = now or now_tpe()
    text = (text or "").strip()[:1500]
    if not text:
        raise ValueError("沒有內容")
    with _LOCK:
        name = _put_image(image, now, RESULT_TTL) if image else None
    (push or _push)([text] + ([_image_message(name)] if name else []))
    return {"ok": True}
