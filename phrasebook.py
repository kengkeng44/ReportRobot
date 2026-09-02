"""每日三句:英文 / 西班牙文走間隔重複,中文金句走隨機不重複。

為什麼是固定間隔而不是 SM-2:真正的遺忘曲線要吃「你記得嗎」的回饋,
那需要信裡放連結、server.py 開端點、Notion 存熟程度。使用者選了零互動
(見 spec 2.4),所以這裡只有一張固定的間隔表。

這個模組刻意**不碰 Notion、不碰 AI** —— 只做決策,I/O 由呼叫端負責。
測試因此不需要 mock 任何東西。
"""

import random
from datetime import timedelta

# 第 n 次出現之後,隔幾天再出現。使用者原話是「隔一個月、三個月再重傳」,
# 對應第 3、第 4 級;前兩級是標準的短期鞏固。
INTERVALS = (1, 7, 30, 90, 180)


def next_due(appeared_count, today):
    """出現過 appeared_count 次(含這次)之後,下次該哪天出現。

    超過表長就一直用最後一級(180 天一輪),不是停止出現 ——
    背過的東西還是會忘,只是慢一點。

    appeared_count 是 0 時當成 1:Notion 的「出現次數」沒填,讀回來
    就是 0。這裡吞掉那個 off-by-one,呼叫端不必特判。
    """
    index = min(max(appeared_count, 1) - 1, len(INTERVALS) - 1)
    return today + timedelta(days=INTERVALS[index])


def pick_due(rows, today):
    """從 rows 挑一句今天該出現的。沒有就回 None。

    rows 的每個元素至少要有 due(ISO 字串或 None)。

    排序規則:
    1. due 為空的最優先 —— 使用者剛貼進 Notion,當天就該上場
    2. 其次 due 最舊的 —— 逾期最久的先還債

    空字串在字典序上小於任何 ISO 日期,所以兩條規則可以用同一個
    排序 key 表達,不需要分兩段。
    """
    today_iso = today.isoformat()
    # not r.get("due") 短路要留著:少了它,due=None 會跟字串比較
    # (None <= "2026-09-01"),直接 TypeError。
    due = [r for r in rows if not r.get("due") or r["due"] <= today_iso]
    if not due:
        return None
    return min(due, key=lambda r: r.get("due") or "")


def advance(row, today):
    """挑中一句之後,要寫回 Notion 的欄位。

    回 dict 而不是直接寫 Notion:這個模組不做 I/O,而且這樣測試
    看得到「算出來的排程」而不是「有沒有呼叫 API」。
    """
    # Notion 的 number 欄位可能回 float(例如 3.0),float 拿去當
    # INTERVALS 的索引會 TypeError,所以這裡轉 int。
    appeared = int(row.get("appeared") or 0) + 1
    return {
        "appeared": appeared,
        "last_seen": today,
        "due": next_due(appeared, today),
    }


def pick_quote(rows, today):
    """挑一句中文金句。沒講過的優先(隨機),全講過就挑最久沒講的。

    為什麼金句不排間隔重複:英西是要背的,隔一個月再看有回升價值;
    金句是要被啟發的,同一句名言隔一個月不會產生同樣的回升(見 spec 2.3)。

    為什麼用完不回 None(語句庫的做法是回 None 交給 AI 補位):
    金句沒有 AI 補位這條路 —— 硬生的「名言」是假的。輪回去重講
    比讓區塊消失好。

    today 目前沒用到,保留在簽名上是為了跟 pick_due 對稱,呼叫端
    兩個都傳同一組參數。
    """
    if not rows:
        return None
    unseen = [r for r in rows if not r.get("last_seen")]
    if unseen:
        # 隨機而不是取第一個:不然新貼一批之後會照 Notion 的順序
        # 一路念下去,排前面的永遠先被消耗完
        return random.choice(unseen)
    return min(rows, key=lambda r: r.get("last_seen") or "")


def _phrase_block(tag, row):
    """一句的三行:原句 / 中文意思 / 情境提示。後兩行沒填就不佔行。"""
    lines = [f"[{tag}] {row['sentence']}"]
    if row.get("meaning"):
        lines.append(f"     {row['meaning']}")
    if row.get("note"):
        lines.append(f"     💡 {row['note']}")
    return "\n".join(lines)


def format_daily(en=None, es=None, quote=None):
    """組「今日三句」的純文字。三個都沒有回 None。

    回 None 是刻意的:呼叫端(_build_personal_sections)的既有規則是
    「空的區塊直接不放」,留一張空卡片比沒有還糟。
    """
    parts = [_phrase_block(tag, row)
             for tag, row in (("EN", en), ("ES", es)) if row]

    if quote:
        lines = [f"[中] {quote['sentence']}"]
        if quote.get("source"):
            lines.append(f"     —— {quote['source']}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts) if parts else None
