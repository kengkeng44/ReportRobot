"""私訊喵管家傳轉帳截圖 → Claude 讀圖 → 交易明細（方向＝轉帳）。

為什麼要截圖：「別人轉給我」三家銀行（國泰／富邦／郵局）都只有 App 推播，
不寄 Email；LINE 的轉帳（iPASS MONEY）也只在 LINE 裡看得到，沒有 API。
2026-10-24 使用者選的做法：每月把 iPASS MONEY 帳戶紀錄、銀行 App 入帳明細截圖傳給 bot。

去重：
- 同一張圖傳兩次 → 指紋一樣，跳過
- 截圖裡的轉出跟富邦／國泰轉帳通知信是同一筆 → 用 bank_transfer.transfer_fingerprint
  （日期＋時分＋方向＋金額，不含對方名稱）比對，跳過
- 截圖沒有時間的 → 指紋時分留空；同一天同金額同方向就當同一筆（寧可漏記一筆，不要重複）

只限本人私訊（command_router._is_admin），圖片只送 Anthropic 讀，不存檔。
"""

import base64
import json
import os
import re

import sources as _sources
from parsers.bank_transfer import is_self, transfer_fingerprint
from tz_utils import today_tpe

SOURCE = "截圖辨識"

_PROMPT = """這是一張手機截圖，可能是 LINE 轉帳（iPASS MONEY / LINE Pay）紀錄、LINE Bank 或銀行 App（國泰、富邦、郵局等）的交易明細。
今天是 {today}。帳戶主人是「{owner}」。

把截圖裡**每一筆轉帳**列出來，只輸出 JSON 陣列，不要其他文字：
[{{"date":"YYYY-MM-DD","time":"HH:MM 或空字串","direction":"in 或 out","amount":整數,"counterparty":"對方名稱（看不到就空字串）","channel":"iPASS MONEY / LINE Bank / 國泰 / 富邦 / 郵局 / 其他","memo":"備註或空字串","skip":"空字串，或略過原因"}}]

規則：
- direction：別人轉給主人＝in；主人轉出去＝out
- 截圖沒寫年份就用今年；算出來比今天還晚就用去年
- 刷卡消費、繳信用卡費、繳費、儲值、提款、利息不是轉帳 → skip 填原因
- 主人轉給自己的其他帳戶 → skip 填「自己的帳戶」
- 看不清楚的數字不要猜，那筆 skip 填「看不清楚」
- 如果是電腦版 LINE 聊天室畫面：日期看訊息上方的日期分隔列（「今天」「昨天」「10/26(日)」），時間看泡泡旁邊的小字
- 聊天室裡只算轉帳卡片或銀行／LINE Pay 官方通知；有人打字說「我轉你了」但沒有轉帳卡片的，skip 填「只是聊天」
- 一筆都沒有就輸出 []"""


def _owner():
    return os.environ.get("OWNER_REAL_NAME", "").strip() or "（未知）"


def download_line_image(message_id):
    """LINE 圖片要另外打 content API 下載。回 (bytes, media_type)。"""
    import requests
    from line_sender import LINE_CHANNEL_TOKEN
    r = requests.get(f"https://api-data.line.me/v2/bot/message/{message_id}/content",
                     headers={"Authorization": f"Bearer {LINE_CHANNEL_TOKEN}"}, timeout=20)
    r.raise_for_status()
    media = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0]
    return r.content, media


def extract(image_bytes, media_type, api_key=None, create=None):
    """圖 → Claude 讀出的轉帳清單（list of dict）。讀不出 JSON 回 []。"""
    if create is None:
        from sonnet_client import create
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    prompt = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                     "data": base64.b64encode(image_bytes).decode()}},
        {"type": "text", "text": _PROMPT.format(today=today_tpe().isoformat(), owner=_owner())},
    ]
    msg = create(api_key, prompt, max_tokens=4000, effort="low", label="transfer_ocr")
    text = "".join(getattr(b, "text", "") for b in msg.content)
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        rows = json.loads(m.group(0))
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict)]


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})")


def to_txns(rows):
    """Claude 的輸出 → (要寫的交易, 略過的原因清單)。欄位不對的當略過，不猜。"""
    txns, skipped = [], []
    for r in rows:
        if r.get("skip"):
            skipped.append(str(r["skip"])[:20])
            continue
        date = str(r.get("date") or "")
        direction = r.get("direction")
        try:
            amount = int(round(float(r.get("amount"))))
        except (TypeError, ValueError):
            amount = 0
        if not _DATE.match(date) or direction not in ("in", "out") or amount <= 0:
            skipped.append("欄位不完整")
            continue
        if date > today_tpe().isoformat():
            skipped.append("日期在未來")
            continue
        who = str(r.get("counterparty") or "").strip()[:30]
        if is_self(who):
            skipped.append("自己的帳戶")
            continue
        m = _HHMM.match(str(r.get("time") or ""))
        hhmm = f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""
        channel = str(r.get("channel") or "").strip()[:20]
        memo = str(r.get("memo") or "").strip()[:60]
        arrow = f"↗️ 轉給 {who or '（未知）'}" if direction == "out" else f"↙️ 收到 {who or '（未知）'}"
        notes = [x for x in (channel, f"留言：{memo}" if memo else "") if x]
        txns.append({
            "date": date,
            "time": hhmm,
            "amount": amount,
            "currency": "TWD",
            "shop": arrow,
            "direction": "轉帳",
            "source": SOURCE,
            "items": "｜".join(notes) if notes else None,
            "fingerprint": transfer_fingerprint(date, hhmm, direction, amount),
        })
    return txns, skipped


def record(txns, notion=None):
    """寫進交易明細，回 (寫入的, 已記過的)。

    沒時間的截圖要另外跟「同日同金額同方向」比：信件那筆有時分，指紋對不上。
    """
    if notion is None:
        import notion_db as notion
    seen = notion.transactions_existing_fingerprints(limit=20000, source_names=_sources.TRANSFER_DISPLAY)
    if seen is None:
        raise RuntimeError("讀不到既有指紋，這次先不寫以免重複")
    written, dup = [], []
    for t in txns:
        direction = "out" if t["shop"].startswith("↗️") else "in"
        candidates = {t["fingerprint"]}
        if not t["time"]:
            # 沒時間：同一天任何時分的同金額同方向都算重複
            candidates |= {transfer_fingerprint(t["date"], f"{h:02d}:{m:02d}", direction, t["amount"])
                           for h in range(24) for m in range(60)}
        if candidates & seen:
            dup.append(t)
            continue
        if notion.transaction_add(t):
            seen.add(t["fingerprint"])
            written.append(t)
    return written, dup


def _line(t):
    d = t["date"][5:].replace("-", "/")
    return f"{d} {t['shop']} {t['amount']:,}"


def handle_line_image(message_id):
    """webhook 入口：下載 → 辨識 → 寫入 → 回一段文字。任何失敗都回人看得懂的話。"""
    try:
        img, media = download_line_image(message_id)
    except Exception as e:
        print(f"[transfer_ocr] 下載圖片失敗：{e}")
        return "📸 圖片下載失敗，再傳一次試試。"
    return handle_image(img, media)


def handle_image(img, media):
    """辨識 → 寫入 → 回一段文字。私訊截圖和本機 LINE 掃描（/admin/transfer-ocr）共用。"""
    try:
        rows = extract(img, media)
    except Exception as e:
        print(f"[transfer_ocr] 辨識失敗：{e}")
        # 附錯誤類型與狀態碼：Railway log 不好看，回覆裡就要看得出是額度、格式還是連線問題
        why = type(e).__name__ + (f" {e.status_code}" if getattr(e, "status_code", None) else "")
        return f"📸 辨識失敗（{why}），等一下再傳一次。"
    txns, skipped = to_txns(rows)
    if not txns:
        why = f"（略過：{'、'.join(sorted(set(skipped)))}）" if skipped else ""
        return f"📸 這張沒看到可以記的轉帳{why}"
    try:
        written, dup = record(txns)
    except Exception as e:
        print(f"[transfer_ocr] 寫入失敗：{e}")
        return "📸 記帳表暫時連不上，等一下再傳一次（重傳不會重複記）。"
    lines = [f"📸 記了 {len(written)} 筆轉帳"]
    lines += [f"・{_line(t)}" for t in written[:15]]
    if len(written) > 15:
        lines.append(f"・…還有 {len(written) - 15} 筆")
    if dup:
        lines.append(f"已經記過 {len(dup)} 筆，跳過")
    if skipped:
        lines.append(f"不是轉帳或看不清楚 {len(skipped)} 筆，跳過")
    lines.append("方向都標「轉帳」不算花費；是房租這種的話在 Notion 改成支出")
    return "\n".join(lines)
