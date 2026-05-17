"""
LINE Messaging API 模組：push / reply 訊息（文字 / Flex 卡片皆可）。

對外介面接受三種型別：
- str       純文字（自動 strip HTML、超過 4500 字會切多則）
- dict      已成形的 LINE message dict（例：Flex Message，{"type": "flex", ...}）
- list      混合上面兩種，會依序送

push 走 push API 並計入月配額；reply 走 reply API（不計費，但 reply_token 限 1 次 / 30 秒）。
"""

import os
import re
import requests
from html import unescape


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


LINE_CHANNEL_TOKEN = _env("LINE_CHANNEL_TOKEN")
LINE_GROUP_ID = _env("LINE_GROUP_ID")

PUSH_URL = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"
MAX_CHARS = 4500       # 單則文字上限（LINE 5000，留 buffer）
MAX_MESSAGES = 5       # 一次最多 5 則訊息（LINE API limit）


def _headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
    }


def _strip_html(text):
    """LINE 不支援 HTML：<a href="x">y</a> → "y (x)"，其他 tag 拿掉。"""
    if not text:
        return ""
    text = re.sub(
        r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>',
        lambda m: f"{m.group(2)} ({m.group(1)})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def _chunks(text):
    """切成 LINE 單則上限的 chunks，最多 MAX_MESSAGES 則。"""
    plain = _strip_html(text)
    if not plain:
        return []
    return [plain[i:i + MAX_CHARS] for i in range(0, len(plain), MAX_CHARS)][:MAX_MESSAGES]


def _to_messages(response):
    """response → list[LINE message dict]，最多 MAX_MESSAGES。
    str  → 切 chunk → text message
    dict → 假設已是 LINE message dict（含 'type'），直接用
    list → 上述兩種混合，依序展開
    None/空字串 → []
    """
    if response is None:
        return []
    items = response if isinstance(response, list) else [response]
    out = []
    for it in items:
        if it is None or it == "":
            continue
        if isinstance(it, dict):
            out.append(it)
        else:
            for c in _chunks(str(it)):
                out.append({"type": "text", "text": c})
        if len(out) >= MAX_MESSAGES:
            break
    return out[:MAX_MESSAGES]


def _post(url, payload):
    try:
        r = requests.post(url, json=payload, headers=_headers(), timeout=10)
        if r.status_code != 200:
            print(f"LINE {url.rsplit('/', 1)[-1]} 失敗 {r.status_code}: {r.text[:300]}")
            return False
        return True
    except Exception as e:
        print(f"LINE post 例外：{e}")
        return False


def _bump_quota():
    """LINE push 成功後計數一次（reply 不算）。"""
    try:
        import line_quota
        line_quota.bump()
    except Exception:
        pass


def _push_one(target, message):
    """送一則 LINE message 給 target，成功就 bump 一次配額。"""
    if _post(PUSH_URL, {"to": target, "messages": [message]}):
        _bump_quota()


async def push_message(response):
    """推播到群組（每日報用）。response 同上 _to_messages 規則。"""
    if not (LINE_CHANNEL_TOKEN and LINE_GROUP_ID):
        return
    for msg in _to_messages(response):
        _push_one(LINE_GROUP_ID, msg)


def push_message_sync(response):
    """同步版本：推播到群組（給 apscheduler 警示用）。"""
    if not (LINE_CHANNEL_TOKEN and LINE_GROUP_ID):
        return
    for msg in _to_messages(response):
        _push_one(LINE_GROUP_ID, msg)


def push_to_user_sync(user_id, response):
    """同步版本的 push（給 apscheduler / 提醒 / postback handler 用）。"""
    if not (LINE_CHANNEL_TOKEN and user_id):
        return
    for msg in _to_messages(response):
        _push_one(user_id, msg)


async def reply_message(reply_token, response):
    """回覆使用者訊息（webhook 互動用）。reply_token 只能用 1 次、有效 30 秒。
    一次 reply 可塞最多 5 則訊息，全打包同一 request。"""
    if not (LINE_CHANNEL_TOKEN and reply_token):
        return
    messages = _to_messages(response)
    if not messages:
        return
    _post(REPLY_URL, {
        "replyToken": reply_token,
        "messages": messages,
    })


# ── 舊介面相容（main.py 過渡期）──
async def send_message(text):
    await push_message(text)


async def send_photo(image_path, caption=None):
    """LINE 不傳圖，no-op。"""
    return
