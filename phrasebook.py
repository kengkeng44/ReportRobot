"""每日三句:英文 / 西班牙文走間隔重複,中文金句走隨機不重複。

為什麼是固定間隔而不是 SM-2:真正的遺忘曲線要吃「你記得嗎」的回饋,
那需要信裡放連結、server.py 開端點、Notion 存熟程度。使用者選了零互動
(見 spec 2.4),所以這裡只有一張固定的間隔表。

這個模組刻意**不碰 Notion、不碰 AI** —— 只做決策,I/O 由呼叫端負責。
測試因此不需要 mock 任何東西。
"""

import random
import re
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


# 半形冒號也認:模型偶爾會混用,為了這個丟掉一句已經生好的句子不划算
_LINE_RE = {
    "sentence": re.compile(r"句子\s*[：:]\s*(.+)"),
    "meaning": re.compile(r"意思\s*[：:]\s*(.+)"),
    "note": re.compile(r"提示\s*[：:]\s*(.+)"),
}


def parse_ai(text):
    """把 AI 的三行回覆拆成 dict。沒有句子就回 None。

    容錯而不是 raise —— 但「沒有句子」是硬失敗:意思和提示是配角,
    句子沒有就沒有東西可教,寧可讓呼叫端當作生不出來。
    """
    out = {}
    for key, pattern in _LINE_RE.items():
        m = pattern.search(text or "")
        out[key] = m.group(1).strip() if m else ""
    return out if out["sentence"] else None


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


# ─────────────────────────────────────────────────────────
# I/O 邊界:上面全是純邏輯,以下開始碰 Notion 與 AI
#
# 兩個間接層(_store / _ai)存在的唯一理由是讓測試整個換掉它們 ——
# humor.py 用同樣的手法,不然每個測試都要 mock 兩套 SDK。
# ─────────────────────────────────────────────────────────

LANGUAGES = ("英文", "西班牙文")

# 塞進 prompt 當「別再生這些」的句數。全塞會灌爆 token,
# 而語句庫本身就是歷史,不需要另建一張表(跟 humor.py 不同)。
AVOID_IN_PROMPT = 15

AI_MODEL = "claude-sonnet-4-5"


def _store():
    import notion_db
    return notion_db


def _ai(prompt, max_tokens=200):
    import anthropic
    import usage_tracker
    from humor import _env

    client = anthropic.Anthropic(api_key=_env("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=AI_MODEL,
        max_tokens=max_tokens,
        temperature=1.0,
        messages=[{"role": "user", "content": prompt}],
    )
    usage_tracker.track(AI_MODEL, message)
    return message.content[0].text.strip()


def _avoid_block(rows):
    """組 prompt 裡的「別再生這些」。庫是空的就回空字串(整段不附)。"""
    from prompts import AVOID_PHRASE_BLOCK

    recent = [r.get("sentence") for r in rows[:AVOID_IN_PROMPT]
              if r.get("sentence")]
    if not recent:
        return ""
    return AVOID_PHRASE_BLOCK.format(
        recent="\n".join(f"- {s}" for s in recent)
    )


def _generate(language, existing, today):
    """AI 現生一句並寫回語句庫。失敗回 None。

    寫回去是刻意的:生出來的句子會跟著進複習循環,使用者不貼檔的
    日子庫也在長大,而不是生完就丟。
    """
    from prompts import DAILY_PHRASE_PROMPT

    try:
        raw = _ai(DAILY_PHRASE_PROMPT.format(
            language=language, avoid_block=_avoid_block(existing),
        ))
    except Exception as e:
        print(f"[phrasebook] {language} AI 補位失敗：{e}")
        return None

    row = parse_ai(raw)
    if not row:
        print(f"[phrasebook] {language} AI 回覆解析不出句子")
        return None

    _store().phrase_add(
        row["sentence"], language,
        meaning=row["meaning"], note=row["note"],
        source="AI生成", day=today, due=next_due(1, today),
    )
    return row


def _one_language(language, today):
    """某語言的今日一句:先看庫裡有沒有到期的,沒有才叫 AI。"""
    try:
        rows = _store().phrases_load(language)
    except Exception as e:
        print(f"[phrasebook] {language} 讀取語句庫失敗：{e}")
        rows = []

    picked = pick_due(rows, today)
    if picked:
        fields = advance(picked, today)
        _store().phrase_advance(picked["page_id"], fields)
        return picked

    return _generate(language, rows, today)


def _one_quote(today):
    """今日金句。金句沒有 AI 補位 —— 硬生的「名言」是假的。"""
    try:
        rows = _store().quotes_load()
    except Exception as e:
        print(f"[phrasebook] 讀取金句庫失敗：{e}")
        return None

    picked = pick_quote(rows, today)
    if picked:
        _store().quote_mark_seen(picked["page_id"], today)
    return picked


def daily_three(today):
    """每日信的「今日三句」。全部拿不到回 None。

    三個來源各自 try:英文生不出來不該讓西班牙文和金句一起消失。
    """
    en = _one_language("英文", today)
    es = _one_language("西班牙文", today)
    quote = _one_quote(today)
    return format_daily(en=en, es=es, quote=quote)
