"""一句話 → 待辦內容 + 起訖日 + 優先度。

規則優先、AI 補位：規則吃掉「明天」「下週一」「9/15」這類佔絕大多數的
說法（即時、免費、可測），AI 只在規則認不出來時才呼叫（「中秋前」
「農曆年前」）。全部丟給 AI 的話每加一筆待辦就是一次 1-2 秒的 API 往返，
而且 AI 掛掉就完全設不了日期。

純邏輯與 I/O 分開：parse_dates / parse_priority 完全不碰網路，
_ai 是唯一的接縫，測試整個換掉它（同 phrasebook.py）。
"""

import re
from datetime import date, timedelta

# 中文數字 → int。只到十：更大的數字使用者會直接打阿拉伯數字，
# 而「二十三天後」這種說法在待辦裡沒出現過。
_CN_NUM = {
    "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _num(token):
    """阿拉伯或中文數字 → int。認不出來回 None。"""
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return _CN_NUM.get(token)


_NUM_PAT = r"(\d{1,2}|[一二兩三四五六七八九十])"

# 順序有意義：「大後天」必須排在「後天」前面，否則「大後天」會被
# 「後天」先比中，剩下一個孤零零的「大」字留在待辦內容裡。
_OFFSET_DAYS = (
    ("大後天", 3),
    ("後天", 2),
    ("明天", 1),
    ("明日", 1),
    ("今天", 0),
    ("今日", 0),
)


# 週一=0 … 週日=6（對齊 date.weekday()）
_WEEKDAYS = {
    "一": 0, "1": 0,
    "二": 1, "2": 1,
    "三": 2, "3": 2,
    "四": 3, "4": 3,
    "五": 4, "5": 4,
    "六": 5, "6": 5,
    "日": 6, "天": 6, "七": 6, "7": 6,
}

_WEEK_WORD = r"(?:週|周|星期|禮拜)"
_WEEKDAY_CHARS = "".join(_WEEKDAYS)
_WEEKDAY_RE = re.compile(
    r"(下下|下|這|本)?" + _WEEK_WORD + r"([" + _WEEKDAY_CHARS + r"])"
)


def _weekday_date(today, qualifier, weekday):
    """qualifier: '下下' / '下' / '這' / '本' / None。

    一律先算本週一（today - today.weekday()）再位移 —— 直接對 today
    加減天數在跨週時會錯，而那正是最常用的情境。
    """
    monday = today - timedelta(days=today.weekday())
    if qualifier == "下":
        return monday + timedelta(days=7 + weekday)
    if qualifier == "下下":
        return monday + timedelta(days=14 + weekday)
    if qualifier in ("這", "本"):
        # 本週已經過去的日子也算本週：使用者說「這週一」就是指那天
        return monday + timedelta(days=weekday)
    # 沒講這週下週 → 取下一次，今天符合就是今天
    candidate = monday + timedelta(days=weekday)
    if candidate < today:
        candidate += timedelta(days=7)
    return candidate


# 明確日期。**一定要有分隔符**（/ - . 月）：裸數字不視為日期，
# 「買915號的東西」不該變成 9/15 到期。猜錯憑空長出一個截止日，
# 比猜不到（跳防呆按鈕）糟得多。
_MD = r"(\d{1,2})\s*(?:/|-|\.|月)\s*(\d{1,2})\s*(?:日|號)?"
_YMD = r"(?:(\d{4})\s*(?:/|-|\.|年)\s*)?"
_DATE_RE = re.compile(_YMD + _MD)
_RANGE_RE = re.compile(_YMD + _MD + r"\s*(?:-|~|到|至)\s*" + _YMD + _MD)

# 明確日期沒講年份時，往回容忍幾天才判定是「明年」。
# 補登上個月的事情很常見，回到去年則幾乎不會發生。
_PAST_TOLERANCE_DAYS = 30


def _resolve(year, month, day, today, anchor=None):
    """(年, 月, 日) → date。年份沒講時自己推。無效日期回 None。

    anchor 有值時（區間的結束日）用它當基準：結束比開始早就滾到下一年。
    """
    base = anchor or today
    for candidate_year in ([int(year)] if year else [base.year, base.year + 1]):
        try:
            found = date(candidate_year, int(month), int(day))
        except ValueError:
            return None                      # 2月30日這種
        if year:
            return found
        if anchor:
            if found >= anchor:
                return found
        elif found >= today - timedelta(days=_PAST_TOLERANCE_DAYS):
            return found
    return None


def parse_dates(text, today):
    """text → (start, end, 去掉日期字樣的 text)。

    認不出日期時回 (None, None, 原 text)。
    """
    rest = text

    m = _RANGE_RE.search(rest)
    if m:
        y1, m1, d1, y2, m2, d2 = m.groups()
        start = _resolve(y1, m1, d1, today)
        end = _resolve(y2, m2, d2, today, anchor=start) if start else None
        if start and end:
            return start, end, (rest[:m.start()] + rest[m.end():]).strip()

    for word, offset in _OFFSET_DAYS:
        if word in rest:
            return (today + timedelta(days=offset), None,
                    rest.replace(word, "", 1).strip())

    m = re.search(_NUM_PAT + r"\s*(?:天|日)後", rest)
    if m:
        n = _num(m.group(1))
        if n is not None:
            return (today + timedelta(days=n), None,
                    (rest[:m.start()] + rest[m.end():]).strip())

    m = re.search(_NUM_PAT + r"\s*(?:週|周|星期|禮拜)後", rest)
    if m:
        n = _num(m.group(1))
        if n is not None:
            return (today + timedelta(weeks=n), None,
                    (rest[:m.start()] + rest[m.end():]).strip())

    # 星期排在「N 週後」之後：先讓「2 禮拜後」整段被吃掉，
    # 免得剩下的字尾再被星期規則咬到
    m = _WEEKDAY_RE.search(rest)
    if m:
        weekday = _WEEKDAYS.get(m.group(2))
        if weekday is not None:
            found = _weekday_date(today, m.group(1), weekday)
            return found, None, (rest[:m.start()] + rest[m.end():]).strip()


    m = _DATE_RE.search(rest)
    if m:
        found = _resolve(*m.groups(), today)
        if found:
            return found, None, (rest[:m.start()] + rest[m.end():]).strip()

    return None, None, rest


PRIORITIES = ("P0", "P1", "P2", "P3")

# 左右邊界自己界定，不用 ：中文字元在 re 裡算 \w，所以
# 「買P3手機殼」的 P3 兩側  判定會跟直覺相反。
# 這裡要求左邊是開頭或空白、右邊是結尾或空白。
_PRIORITY_RE = re.compile(r"(?:^|\s)([Pp][0-3])(?=\s|$)")


def parse_priority(text):
    """text → (優先度 或 None, 去掉優先度 token 的 text)。

    只認 P0-P3 這個 token。「很急」「重要」刻意不解析：那是主觀詞，
    猜錯會讓使用者對整個功能失去信任，而防呆按鈕點一下就解決了。
    """
    m = _PRIORITY_RE.search(text or "")
    if not m:
        return None, (text or "").strip()
    rest = (text[:m.start()] + " " + text[m.end():]).strip()
    return m.group(1).upper(), rest
