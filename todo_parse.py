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


_WEEKDAY_NAMES = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")

_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _parse_iso(token):
    """'2026-09-25' → date。認不出來回 None。"""
    m = _ISO_RE.fullmatch((token or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _ai_dates(text, today):
    """規則吃不下來時問 AI。回 (start, end)，任何問題都回 (None, None)。

    AI 失敗不該讓整件事沒記到 —— 呼叫端拿到 (None, None) 之後
    照樣把內容寫進 Notion，再跳防呆按鈕讓使用者補日期。
    """
    from prompts import TODO_DATE_PROMPT

    prompt = TODO_DATE_PROMPT.format(
        today=today.isoformat(),
        weekday=_WEEKDAY_NAMES[today.weekday()],
        text=text,
    )
    try:
        answer = (_ai(prompt) or "").strip()
    except Exception as e:
        print(f"[todo_parse] AI 解析日期失敗：{e}")
        return None, None

    if answer.upper() == "NONE":
        return None, None

    parts = [p for p in answer.split("~") if p.strip()]
    start = _parse_iso(parts[0]) if parts else None
    end = _parse_iso(parts[1]) if len(parts) > 1 else None
    if not start:
        return None, None
    return start, end


def parse(text, today):
    """一句話 → {text, start, end, priority}。

    規則先跑；規則認不出日期才問 AI。AI 失敗或回垃圾時 start/end 留 None，
    內容照樣回 —— 呼叫端據此把事情記下來再跳防呆按鈕。
    """
    priority, rest = parse_priority(text or "")
    start, end, rest = parse_dates(rest, today)

    if start is None and rest.strip():
        start, end = _ai_dates(rest, today)

    return {
        "text": rest.strip(),
        "start": start,
        "end": end,
        "priority": priority,
    }


# ─────────────────────────────────────────────────────────
# I/O 邊界：上面全是純邏輯，以下開始碰 Anthropic
#
# _ai 存在的唯一理由是讓測試整個換掉它 —— phrasebook.py 同一套手法。
# ─────────────────────────────────────────────────────────

AI_MODEL = "claude-haiku-4-5-20251001"


def _ai(prompt, max_tokens=64):
    """這是一次格式固定、零創意的抽取任務，而且它擋在使用者面前
    （他按了 ➕ 正在等回覆），所以用最快的模型 + temperature=0：
    同一句話每次都要得到同一個日期。"""
    import anthropic
    import usage_tracker
    from humor import _env

    client = anthropic.Anthropic(api_key=_env("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=AI_MODEL,
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    usage_tracker.track(AI_MODEL, message, feature="待辦解析")
    return message.content[0].text.strip()


# ─────────────────────────────────────────────────────────
# 待辦類型：關鍵字判斷，不問 AI（每加一件都問的話又慢又花錢）
# 順序有意義：「繳」比「買」先比，「買保險」是繳費不是採購。
# ─────────────────────────────────────────────────────────

# 排列順序也是 LINE 待辦卡片的分段順序，跟網頁儀表板一致
# 🤖 自動化、💻 電腦設定、💰 理財、👥 找人：你個人的事太多擠在「專案」「其他」，
# 拆出來才篩得動
TODO_TYPES = ("🛒 採購", "🏡 回家", "🧹 家務", "💳 繳費", "💰 理財", "📅 預約", "📋 手續",
              "👥 找人", "📚 學習", "🤖 自動化", "💻 電腦設定", "🛠 專案", "💼 工作", "📌 其他")

_TYPE_KEYWORDS = (
    ("💳 繳費", ("繳", "付款", "帳單", "電費", "水費", "瓦斯費", "房租", "保費", "保險", "稅",
                "管理費", "卡費", "轉帳", "匯款", "還錢")),
    # 電腦設定排在自動化前面：「換掉機器人的密鑰」是資安設定，不是新的機器人
    ("💻 電腦設定", ("設定", "Wi-Fi", "WiFi", "wifi", "網卡", "安裝", "skill", "Remote Control",
                  "密鑰", "資安", "權限", "環境變數")),
    ("🤖 自動化", ("自動", "機器人", "監控", "排程", "cron", "bot", "爬蟲", "收藏", "貼文整理",
                 "Gmail", "gmail", "AppID", "資料庫")),
    # 理財排在預約前面：「預約銀行辦信用卡」要篩理財時找得到
    ("💰 理財", ("股票", "淨值", "存款", "銀行", "信用卡", "額度", "證券", "基金", "投資", "財務")),
    ("📅 預約", ("預約", "掛號", "訂位", "訂票", "訂房", "門診", "看醫生", "回診", "剪頭髮",
                "報名", "約")),
    # 「回淡水拿毛巾」「帶胃藥回家」是回家那趟要辦的事，比採購先比
    ("🏡 回家", ("回淡水", "回家", "淡水", "帶回")),
    # 「買洗衣精」有「洗」也是採購：有「買」先算採購，名詞清單留到家務之後比
    ("🛒 採購", ("買", "採買", "補貨")),
    ("📋 手續", ("歸戶", "申請", "辦理", "補件", "換證", "證件", "變更", "註冊")),
    # 不放單字「找」：會誤中「找房子」「找資料」
    ("👥 找人", ("去找", "採訪", "拜訪", "聚餐", "見面", "老師", "朋友")),
    # 不放單字「考」「學」：會誤中「參考」「學校」
    ("📚 學習", ("練習", "考試", "讀書", "上課", "課程", "多益", "leetcode", "LeetCode")),
    ("🛠 專案", ("測試", "程式", "部署")),
    ("💼 工作", ("報告", "會議", "開會", "簡報", "交件", "提案", "專案", "客戶", "上班", "加班")),
    ("🧹 家務", ("洗", "掃", "拖地", "倒垃圾", "垃圾", "整理", "收納", "曬", "晾", "換床單",
                "清", "修", "換燈", "澆花")),
    ("🛒 採購", ("買", "補貨", "購", "採買", "衛生紙", "抹布", "碗", "盤", "鍋", "杯", "夾子",
                "桌布", "砧板", "餐具", "烤箱", "洗衣精", "牙膏", "麵包", "牛奶", "雞蛋", "菜")),
)


def guess_type(text):
    """待辦內容 → TODO_TYPES 其中一個。認不出來歸「📌 其他」。"""
    t = text or ""
    for name, words in _TYPE_KEYWORDS:
        if any(w in t for w in words):
            return name
    return "📌 其他"
