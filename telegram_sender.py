"""
Telegram Bot API 推送模組（取代部分 LINE push 用以避開 LINE 月配額）。

設計：
- push_to_user_sync(user_id, text)：與 line_sender 同名 + 同 signature，呼叫端可以無痛 swap。
- 單 user 部署：user_id 參數目前保留但不使用，一律推到 TELEGRAM_CHAT_ID env。
  未來多 user 可加 user_id → chat_id mapping，這裡留個 hook 點。
- HTML mode：保留 line_sender 已產生的 <b>/<a> 標籤（line_sender 會 strip 但 Telegram 支援）。
- 失敗只 print warn，不 raise（與 line_sender 一致，避免拖垮排程）。

設定：
- TELEGRAM_TOKEN：BotFather 拿到的 token
- TELEGRAM_CHAT_ID：目標 chat（私人對話的 chat_id，跟 bot /start 後從 getUpdates 取得）
"""

import os
import re
import requests
from html import escape


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


TELEGRAM_TOKEN = _env("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")

API_BASE = "https://api.telegram.org"
MAX_CHARS = 3500  # Telegram 4096 上限，留 buffer 給 HTML 標籤


_ALLOWED_TAGS = re.compile(
    r"</?(?:b|strong|i|em|u|s|code|pre|a)(?:\s+href=\"[^\"]*\")?\s*/?>",
    re.IGNORECASE,
)


def _sanitize_html(text):
    """Telegram HTML mode 只支援少數 tags。其他 tags 拿掉，避免 400 'can't parse entities'。
    保留：<b><strong><i><em><u><s><code><pre><a href="...">"""
    if not text:
        return ""
    placeholders = []

    def _stash(m):
        placeholders.append(m.group(0))
        return f"\x00TG_TAG_{len(placeholders) - 1}\x00"

    stashed = _ALLOWED_TAGS.sub(_stash, text)
    cleaned = re.sub(r"<[^>]+>", "", stashed)
    cleaned = escape(cleaned, quote=False)

    def _restore(m):
        return placeholders[int(m.group(1))]

    return re.sub(r"\x00TG_TAG_(\d+)\x00", _restore, cleaned)


def _chunks(text):
    if not text:
        return []
    sanitized = _sanitize_html(text)
    return [sanitized[i:i + MAX_CHARS] for i in range(0, len(sanitized), MAX_CHARS)]


def _post(chat_id, text):
    if not TELEGRAM_TOKEN:
        print("Telegram push 跳過：TELEGRAM_TOKEN 未設")
        return False
    url = f"{API_BASE}/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if r.status_code != 200:
            print(f"Telegram sendMessage 失敗 {r.status_code}: {r.text[:300]}")
            return False
        return True
    except Exception as e:
        print(f"Telegram post 例外：{e}")
        return False


def push_to_user_sync(user_id, text):
    """同步推送（給 apscheduler / 提醒 / 待辦定期提醒用）。
    與 line_sender.push_to_user_sync 同 signature。
    user_id 參數目前保留但未使用 — 單 user 部署推到 TELEGRAM_CHAT_ID。"""
    if not TELEGRAM_CHAT_ID:
        print("Telegram push 跳過：TELEGRAM_CHAT_ID 未設")
        return
    chunks = _chunks(text)
    if not chunks:
        return
    for chunk in chunks:
        _post(TELEGRAM_CHAT_ID, chunk)
