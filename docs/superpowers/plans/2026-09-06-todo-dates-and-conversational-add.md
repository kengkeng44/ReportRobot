# 待辦升級（起訖日期、優先度、對話式新增）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 待辦加上起訖日期與優先度，每日信只顯示「截止日 ≤ 今天 或 P0」，並讓使用者按一顆 ➕ 之後直接講一句話就記進去。

**Architecture:** 新增純邏輯模組 `todo_parse.py`（一句話 → 內容 / 起訖日 / 優先度，規則優先、AI 補位）；`notion_db` 的 `Todos` schema 加兩欄並由既有的 `_ensure_properties` 自動補上；`personal.py` 開一個記憶體待命狀態 `_PENDING_TODO`；`command_router.handle` 最前面攔截待命訊息；`flex_builder` 加 ➕ 與防呆按鈕。

**Tech Stack:** Python 3.12、pytest、Notion API（`notion-client`）、LINE Messaging API（Flex + postback）、Anthropic SDK（僅 AI 補位）

**規格：** `docs/superpowers/specs/2026-09-05-todo-dates-and-conversational-add-design.md`

---

## 開工前必讀

**分支：** 從 `main` 開 `feat/todo-dates-and-conversational-add`。

```bash
git -C C:/Users/acer/projects/ReportRobot checkout -b feat/todo-dates-and-conversational-add
```

**跑測試：**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q
```

目前基線 **1119 passed**。

**這個 repo 的三條硬規矩：**

1. **換行字元。** `tests/*.py` 與大部分模組在工作目錄裡是 **CRLF**。不要用
   `cat >>` 或 `echo >>` 附加程式碼——那會塞進裸 LF，讓檔案變成混合換行。
   要附加就用 Python：

   ```python
   import io
   p = "tests/test_x.py"
   s = io.open(p, encoding="utf-8", newline="").read()
   io.open(p, "w", encoding="utf-8", newline="").write(s + block.replace("\n", "\r\n"))
   ```

   **新檔**寫完之後統一轉一次：

   ```python
   import io
   s = io.open(p, encoding="utf-8").read()
   io.open(p, "w", encoding="utf-8", newline="\r\n").write(s)
   ```

2. **Windows 主控台是 CP950**，印中文會亂碼。那只是顯示問題，檔案內容仍是
   UTF-8。不要為了「修亂碼」去改檔案編碼。

3. **commit message 用 bash heredoc**，不要用 PowerShell 的 `@'...'@`
   （在 Bash tool 裡那會變成字面上的 `@`）：

   ```bash
   git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
   feat: ...
   MSG
   ```

**本機沒有 `NOTION_TOKEN`**，所以 `notion_db.is_configured()` 回 `False`，
測試不會打到網路。**不要為了跑測試去設它。**

---

## 檔案結構

| 檔案 | 動作 | 職責 | 任務 |
|---|---|---|---|
| `todo_parse.py` | 新增 | 純邏輯：一句話 → `{text, start, end, priority}`。規則 + `_ai` 接縫 | 1-5 |
| `tests/test_todo_parse.py` | 新增 | 上面那支的全部規則案例，零 mock | 1-5 |
| `notion_db.py` | 改 | `Todos` schema 加 `期間`/`優先度`；`todos_create` 收新欄位；新增 `todos_update_fields`；`todos_load_for_user` 讀回新欄位 | 6-7 |
| `personal.py` | 改 | `add_todo` 收新欄位；`_PENDING_TODO` 待命狀態；`todos_due_today` / `format_today_todos` / `set_todo_due` | 8-9, 14 |
| `flex_builder.py` | 改 | 新增 `todo_due_prompt_flex`；`todo_list_flex` 加 ➕ 與日期/優先度 | 10-11 |
| `command_router.py` | 改 | 待命攔截（`handle` 最前面）；`todo_add_start` / `todo_set_due` postback | 12-13 |
| `prompts.py` | 改 | `TODO_DATE_PROMPT` | 5 |
| `daily_report.py` | 改 | 待辦區塊改用 `personal.format_today_todos()` | 15 |

**為什麼 `todo_parse.py` 獨立成檔：** `personal.py` 已經同時管待辦與提醒兩套
東西。日期解析是一組純函式、零 I/O、規則密集、測試量大的邏輯，混進去會讓
那個檔更難讀。

**`format_todos` 一個字都不動。** LINE 打「待辦」的行為維持原樣，每日信改用
新函式。動它會弄壞指令查詢，而那個壞法在信上看不出來。

---

## 資料型別（後面每個任務都照這個走）

`todo_parse.parse()` 的回傳：

```python
{
    "text": "交社宅資料",          # str，已移除日期與優先度 token，strip 過
    "start": date(2026, 9, 8),     # datetime.date 或 None
    "end": None,                   # datetime.date 或 None（單日待辦一律 None）
    "priority": "P0",              # "P0"/"P1"/"P2"/"P3" 或 None
}
```

**`end` 只在真的講了區間時才有值。** 「9/8 交資料」的 `end` 是 `None`，
不是 `date(2026, 9, 8)` —— 「截止日」的定義是 `end or start`，
兩邊都填等於逼呼叫端處理兩種等價表示法。

---

## Segment A：日期與優先度解析（純邏輯）

### Task 1: 相對日（今天 / 明天 / N 天後）

**Files:**
- Create: `todo_parse.py`
- Test: `tests/test_todo_parse.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_todo_parse.py`：

```python
"""一句話 → 待辦內容 + 起訖日 + 優先度。

零 I/O、零 mock：所有案例直接餵字串斷言結果。

基準日一律用固定的 date(2026, 9, 5)（**週六**，本週一 = 8/31）而不是 today() ——
會隨時間漂移的測試等於沒有測試。跨月、跨年的案例另外寫死自己的基準日。
"""

from datetime import date

import todo_parse

SAT = date(2026, 9, 5)          # 2026-09-05 是**週六**（本週一 = 8/31）


def _d(text, today=SAT):
    """只取日期，讓斷言短一點。"""
    start, end, _rest = todo_parse.parse_dates(text, today)
    return start, end


# ── 相對日 ────────────────────────────────────────────────

def test_today():
    assert _d("今天交資料") == (date(2026, 9, 5), None)


def test_tomorrow():
    assert _d("明天交資料") == (date(2026, 9, 6), None)


def test_day_after_tomorrow():
    assert _d("後天交資料") == (date(2026, 9, 7), None)


def test_two_days_after_tomorrow():
    """大後天必須排在「後天」前面比對，否則「後天」會先吃掉尾巴。"""
    assert _d("大後天交資料") == (date(2026, 9, 8), None)


def test_n_days_later():
    assert _d("三天後交資料") == (date(2026, 9, 8), None)
    assert _d("3天後交資料") == (date(2026, 9, 8), None)


def test_n_weeks_later():
    assert _d("一週後交資料") == (date(2026, 9, 12), None)
    assert _d("2禮拜後交資料") == (date(2026, 9, 19), None)


def test_no_date_at_all():
    assert _d("交社宅資料") == (None, None)


def test_date_token_is_removed_from_the_text():
    """留著「明天」兩個字會讓待辦顯示成「明天交資料」——
    隔天再看就是錯的。"""
    _s, _e, rest = todo_parse.parse_dates("明天交資料", SAT)
    assert rest == "交資料"
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_parse.py -q
```

預期：`ModuleNotFoundError: No module named 'todo_parse'`

- [ ] **Step 3: 寫最小實作**

建立 `todo_parse.py`：

```python
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


def parse_dates(text, today):
    """text → (start, end, 去掉日期字樣的 text)。

    認不出日期時回 (None, None, 原 text)。
    """
    rest = text

    for word, offset in _OFFSET_DAYS:
        if word in rest:
            return today + timedelta(days=offset), None, rest.replace(word, "", 1).strip()

    m = re.search(_NUM_PAT + r"\s*(?:天|日)後", rest)
    if m:
        n = _num(m.group(1))
        if n is not None:
            return today + timedelta(days=n), None, (rest[:m.start()] + rest[m.end():]).strip()

    m = re.search(_NUM_PAT + r"\s*(?:週|周|星期|禮拜)後", rest)
    if m:
        n = _num(m.group(1))
        if n is not None:
            return today + timedelta(weeks=n), None, (rest[:m.start()] + rest[m.end():]).strip()

    return None, None, rest
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_parse.py -q
```

預期：`8 passed`

- [ ] **Step 5: 換行字元轉 CRLF**

```bash
cd C:/Users/acer/projects/ReportRobot && python -c "
import io
for p in ('todo_parse.py','tests/test_todo_parse.py'):
    s=io.open(p,encoding='utf-8').read()
    io.open(p,'w',encoding='utf-8',newline='\r\n').write(s)
    b=io.open(p,'rb').read()
    print(p,'CRLF:',b.count(b'\r\n'),'bare LF:',b.count(b'\n')-b.count(b'\r\n'))
"
```

預期：兩個檔案的 `bare LF` 都是 0

- [ ] **Step 6: Commit**

```bash
cd C:/Users/acer/projects/ReportRobot && git add todo_parse.py tests/test_todo_parse.py && git commit -F - <<'MSG'
feat: todo_parse 相對日解析（今天/明天/N 天後）

規則優先、零 I/O。「大後天」排在「後天」前面比對，否則會留下
一個孤零零的「大」字在待辦內容裡。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 2: 星期（這週一 / 下週一 / 裸週五）

**Files:**
- Modify: `todo_parse.py`
- Test: `tests/test_todo_parse.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_todo_parse.py`（記得用 Python 附加以保住 CRLF）：

```python
# ── 星期 ──────────────────────────────────────────────────
#
# 這是整支模組最容易寫錯的地方，所以每個 case 都有測試。
# 演算法釘死：先算「本週一」= today - today.weekday()，
# 下週一 = 本週一 + 7，再加 (X-1) 天。跨月跨年由 date 型別自己處理。

def test_this_week_monday_can_be_in_the_past():
    """週六講「這週一」指的是已經過去的 8/31，不是下週。"""
    assert _d("這週一交資料") == (date(2026, 8, 31), None)
    assert _d("本週一交資料") == (date(2026, 8, 31), None)


def test_next_week_monday():
    assert _d("下週一交資料") == (date(2026, 9, 7), None)
    assert _d("下禮拜一交資料") == (date(2026, 9, 7), None)
    assert _d("下星期一交資料") == (date(2026, 9, 7), None)


def test_bare_weekday_takes_the_next_occurrence():
    """沒講這週下週時取「下一次」——週六講「週一」是下週一。"""
    assert _d("週一交資料") == (date(2026, 9, 7), None)


def test_bare_weekday_today_means_today():
    """週六講「週六」就是今天，不是下週六。
    「今天要交」是最常見的說法，推到七天後等於漏掉。"""
    assert _d("週六交資料") == (date(2026, 9, 5), None)
    assert _d("禮拜六交資料") == (date(2026, 9, 5), None)


def test_weekday_seven_is_sunday():
    """週日 / 週天 / 週7 都是同一天。"""
    assert _d("週日交資料") == (date(2026, 9, 6), None)
    assert _d("週天交資料") == (date(2026, 9, 6), None)


def test_next_week_across_month_boundary():
    """9/29（週二）講「下週一」→ 10/05。"""
    assert _d("下週一交資料", today=date(2026, 9, 29)) == (date(2026, 10, 5), None)


def test_next_week_across_year_boundary():
    """12/29（週二）講「下週三」→ 隔年 1/06。"""
    assert _d("下週三交資料", today=date(2026, 12, 29)) == (date(2027, 1, 6), None)


def test_weekday_token_is_removed():
    _s, _e, rest = todo_parse.parse_dates("下週一交社宅資料", SAT)
    assert rest == "交社宅資料"
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_parse.py -q
```

預期：8 個新測試 FAIL（`assert (None, None) == (datetime.date(2026, 9, 1), None)`）

- [ ] **Step 3: 寫最小實作**

在 `todo_parse.py` 的 `_OFFSET_DAYS` 之後、`parse_dates` 之前插入：

```python
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
```

然後在 `parse_dates` 裡，**「N 週後」那段之後、`return None, None, rest` 之前**
插入：

```python
    m = _WEEKDAY_RE.search(rest)
    if m:
        weekday = _WEEKDAYS.get(m.group(2))
        if weekday is not None:
            found = _weekday_date(today, m.group(1), weekday)
            return found, None, (rest[:m.start()] + rest[m.end():]).strip()
```

> **順序很重要：** 星期的比對必須排在「N 週後」**之後**。「2 禮拜後」的
> 「後」不在 `_WEEKDAYS` 裡所以不會誤中，但把星期放後面同時保證
> 「下週一」不會被前面的規則吃掉半截。

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_parse.py -q
```

預期：`16 passed`

- [ ] **Step 5: Commit**

```bash
cd C:/Users/acer/projects/ReportRobot && git add todo_parse.py tests/test_todo_parse.py && git commit -F - <<'MSG'
feat: todo_parse 星期解析（這週/下週/裸週X）

一律先算本週一再位移，不對 today 直接加減 —— 後者跨週會錯，
而跨週正是最常用的情境。跨月跨年交給 date 型別。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 3: 明確日期與區間（9/15、9月15日、9/1-9/10）

**Files:**
- Modify: `todo_parse.py`
- Test: `tests/test_todo_parse.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_todo_parse.py`：

```python
# ── 明確日期與區間 ────────────────────────────────────────

def test_slash_date():
    assert _d("9/15 交資料") == (date(2026, 9, 15), None)


def test_chinese_date():
    assert _d("9月15日交資料") == (date(2026, 9, 15), None)
    assert _d("9月15號交資料") == (date(2026, 9, 15), None)


def test_date_with_year():
    assert _d("2027/1/5 交資料") == (date(2027, 1, 5), None)


def test_recent_past_date_stays_in_this_year():
    """9/05 講「9/01」是四天前，不是明年 —— 補登昨天忘了記的事很常見。"""
    assert _d("9/1 交資料") == (date(2026, 9, 1), None)


def test_long_past_date_rolls_to_next_year():
    """12/20 講「1/5」指的是明年一月，不是十一個月前。

    分界線是 30 天：超過就往後滾一年。任何分界線都會有錯的個案，
    但「補登上個月」比「回到去年」常見得多。
    """
    assert _d("1/5 交資料", today=date(2026, 12, 20)) == (date(2027, 1, 5), None)


def test_bare_digits_are_never_a_date():
    """「買915號公車票」不該變成 9/15 到期。

    裸數字沒有任何分隔符，猜錯的代價（憑空長出一個截止日）
    比猜不到（跳防呆按鈕讓使用者按一下）大得多。
    """
    assert _d("買915號的東西") == (None, None)
    assert _d("繳 3000 元") == (None, None)


def test_range_with_dash():
    assert _d("9/1-9/10 出差") == (date(2026, 9, 1), date(2026, 9, 10))


def test_range_with_chinese_to():
    assert _d("9/1到9/10 出差") == (date(2026, 9, 1), date(2026, 9, 10))
    assert _d("9/1~9/10 出差") == (date(2026, 9, 1), date(2026, 9, 10))


def test_range_across_year():
    """12/28-1/5：結束比開始早就把結束滾到下一年。"""
    assert _d("12/28-1/5 出差", today=date(2026, 12, 1)) == (
        date(2026, 12, 28), date(2027, 1, 5))


def test_range_token_is_removed():
    _s, _e, rest = todo_parse.parse_dates("9/1-9/10 出差", SAT)
    assert rest == "出差"
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_parse.py -q
```

預期：10 個新測試 FAIL

- [ ] **Step 3: 寫最小實作**

在 `todo_parse.py` 的 `_weekday_date` 之後插入：

```python
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
```

然後在 `parse_dates` **最前面**（`for word, offset in _OFFSET_DAYS:` 之前）
插入區間比對：

```python
    m = _RANGE_RE.search(rest)
    if m:
        y1, m1, d1, y2, m2, d2 = m.groups()
        start = _resolve(y1, m1, d1, today)
        end = _resolve(y2, m2, d2, today, anchor=start) if start else None
        if start and end:
            return start, end, (rest[:m.start()] + rest[m.end():]).strip()
```

單日比對放在星期那段之後、`return None, None, rest` 之前：

```python
    m = _DATE_RE.search(rest)
    if m:
        found = _resolve(*m.groups(), today)
        if found:
            return found, None, (rest[:m.start()] + rest[m.end():]).strip()
```

> **為什麼區間排最前面：** `_DATE_RE` 會先比中「9/1-9/10」裡的 `9/1`，
> 剩下 `-9/10` 留在待辦內容裡。區間必須先吃。

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_parse.py -q
```

預期：`26 passed`

- [ ] **Step 5: Commit**

```bash
cd C:/Users/acer/projects/ReportRobot && git add todo_parse.py tests/test_todo_parse.py && git commit -F - <<'MSG'
feat: todo_parse 明確日期與區間

裸數字不視為日期（「買915號的東西」不該長出截止日）。
區間比對排在單日之前，否則 9/1-9/10 會被切成 9/1 加一段垃圾。
沒講年份時往回容忍 30 天，超過判定為明年。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 4: 優先度解析

**Files:**
- Modify: `todo_parse.py`
- Test: `tests/test_todo_parse.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_todo_parse.py`：

```python
# ── 優先度 ────────────────────────────────────────────────

def test_priority_token():
    assert todo_parse.parse_priority("P0 交社宅資料") == ("P0", "交社宅資料")


def test_priority_is_case_insensitive():
    assert todo_parse.parse_priority("p2 買菜") == ("P2", "買菜")


def test_priority_can_be_at_the_end():
    assert todo_parse.parse_priority("交社宅資料 P1") == ("P1", "交社宅資料")


def test_no_priority():
    assert todo_parse.parse_priority("交社宅資料") == (None, "交社宅資料")


def test_p4_is_not_a_priority():
    """只認 P0-P3。P4 留在內容裡，不要自作主張收斂。"""
    assert todo_parse.parse_priority("P4 交資料") == (None, "P4 交資料")


def test_natural_language_urgency_is_not_a_priority():
    """「很急」「重要」是主觀詞，猜錯會讓使用者對整個功能失去信任，
    而防呆按鈕點一下就解決了。"""
    assert todo_parse.parse_priority("很急 交社宅資料") == (None, "很急 交社宅資料")


def test_priority_inside_a_word_is_not_matched():
    """「買P3手機殼」的 P3 是產品型號，不是優先度。"""
    assert todo_parse.parse_priority("買P3手機殼") == (None, "買P3手機殼")
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_parse.py -k priority -q
```

預期：`AttributeError: module 'todo_parse' has no attribute 'parse_priority'`

- [ ] **Step 3: 寫最小實作**

附加到 `todo_parse.py`（`parse_dates` 之後）：

```python
PRIORITIES = ("P0", "P1", "P2", "P3")

# 左右邊界自己界定，不用 \b：中文字元在 re 裡算 \w，所以
# 「買P3手機殼」的 P3 兩側 \b 判定會跟直覺相反。
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
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_parse.py -q
```

預期：`33 passed`

- [ ] **Step 5: Commit**

```bash
cd C:/Users/acer/projects/ReportRobot && git add todo_parse.py tests/test_todo_parse.py && git commit -F - <<'MSG'
feat: todo_parse 優先度解析（P0-P3）

只認 P0-P3 token，「很急」「重要」不解析 —— 主觀詞猜錯會毀掉信任，
按鈕點一下就解決。用空白邊界而非 \b：中文字元在 re 裡算 \w。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 5: `parse()` 組合器 + AI 補位

**Files:**
- Modify: `todo_parse.py`
- Modify: `prompts.py`
- Test: `tests/test_todo_parse.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_todo_parse.py`：

```python
# ── parse()：組合器 + AI 補位 ─────────────────────────────

def test_parse_combines_everything():
    out = todo_parse.parse("P0 下週一交社宅資料", SAT)

    assert out == {
        "text": "交社宅資料",
        "start": date(2026, 9, 7),
        "end": None,
        "priority": "P0",
    }


def test_parse_without_date_or_priority():
    out = todo_parse.parse("交社宅資料", SAT)

    assert out["text"] == "交社宅資料"
    assert out["start"] is None
    assert out["priority"] is None


def test_rules_win_and_ai_is_never_called(monkeypatch):
    """規則認得的說法不該花錢。每加一筆待辦就打一次 API 是不可接受的。"""
    called = []
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: called.append(prompt))

    todo_parse.parse("明天交資料", SAT)

    assert called == []


def test_ai_fills_in_what_rules_cannot(monkeypatch):
    """「中秋前」這種規則吃不下來的說法才呼叫 AI。"""
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "2026-09-25")

    out = todo_parse.parse("中秋前交資料", SAT)

    assert out["start"] == date(2026, 9, 25)


def test_ai_can_return_a_range(monkeypatch):
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "2026-09-25~2026-09-28")

    out = todo_parse.parse("中秋連假出遊", SAT)

    assert out["start"] == date(2026, 9, 25)
    assert out["end"] == date(2026, 9, 28)


def test_ai_saying_none_means_no_date(monkeypatch):
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "NONE")

    out = todo_parse.parse("交社宅資料", SAT)

    assert out["start"] is None


def test_ai_failure_does_not_lose_the_todo(monkeypatch):
    """AI 掛掉時照樣回內容，讓呼叫端把事情記下來再跳防呆按鈕。
    因為解析日期失敗就整件事不記，是待辦最不能發生的事。"""
    def _boom(prompt):
        raise RuntimeError("API 掛了")
    monkeypatch.setattr(todo_parse, "_ai", _boom)

    out = todo_parse.parse("中秋前交資料", SAT)

    assert out["text"] == "中秋前交資料"
    assert out["start"] is None


def test_ai_garbage_is_ignored(monkeypatch):
    """AI 回了不是日期的東西時當作沒解析到，不要讓它污染資料。"""
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "我覺得是下週吧")

    out = todo_parse.parse("中秋前交資料", SAT)

    assert out["start"] is None


def test_ai_is_not_called_for_empty_text(monkeypatch):
    called = []
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: called.append(prompt))

    todo_parse.parse("   ", SAT)

    assert called == []


def test_prompt_carries_todays_date(monkeypatch):
    """AI 得知道今天幾號才算得出「中秋前」。"""
    seen = []
    monkeypatch.setattr(todo_parse, "_ai",
                        lambda prompt: seen.append(prompt) or "NONE")

    todo_parse.parse("中秋前交資料", SAT)

    assert "2026-09-05" in seen[0]
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_parse.py -q
```

預期：10 個新測試 FAIL（`AttributeError: module 'todo_parse' has no attribute 'parse'`）

- [ ] **Step 3: 加 prompt**

附加到 `prompts.py` 檔尾：

```python
TODO_DATE_PROMPT = """今天是 {today}（{weekday}）。

使用者說了這句話，請判斷他指的截止日期：

{text}

只回日期，格式 YYYY-MM-DD。
如果是一段期間，回 YYYY-MM-DD~YYYY-MM-DD。
如果句子裡完全沒有時間資訊，只回 NONE。

不要解釋，不要加任何其他文字。"""
```

- [ ] **Step 4: 寫最小實作**

附加到 `todo_parse.py` 檔尾：

```python
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
    usage_tracker.track(AI_MODEL, message)
    return message.content[0].text.strip()
```

> **為什麼用 Haiku 而不是 phrasebook 的模型：** 這是一次格式固定、
> 零創意的抽取任務，而且它擋在使用者面前（他按了 ➕ 正在等回覆）。
> `temperature=0` 同理 —— 同一句話每次都要得到同一個日期。

- [ ] **Step 5: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_parse.py -q
```

預期：`43 passed`

- [ ] **Step 6: 跑全套確認沒弄壞別的**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q
```

預期：`1162 passed`（1119 + 43）

- [ ] **Step 7: Commit**

```bash
cd C:/Users/acer/projects/ReportRobot && git add todo_parse.py prompts.py tests/test_todo_parse.py && git commit -F - <<'MSG'
feat: todo_parse.parse 組合器 + AI 補位

規則認得的說法完全不呼叫 AI（有測試守）。AI 失敗、逾時、或回垃圾時
一律當作沒解析到日期，內容照樣回 —— 因為解析日期失敗就整件事不記，
是待辦最不能發生的事。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Segment B：Notion 欄位與寫入

### Task 6: `Todos` schema 加兩欄 + `todos_create` 收新欄位

**Files:**
- Modify: `notion_db.py:137-144`（`_SCHEMAS["Todos"]`）
- Modify: `notion_db.py:1049-1073`（`todos_create`）
- Test: `tests/test_todo_notion_fields.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_todo_notion_fields.py`：

```python
"""Todos 的「期間」與「優先度」兩個新欄位。

不打網路：用一個假的 client 攔下 pages.create / pages.update
的 properties，直接斷言送出去的 payload。
"""

from datetime import date

import notion_db


# ── schema ────────────────────────────────────────────────

def test_todos_schema_has_the_two_new_fields():
    """既有 DB 由 _ensure_properties 自動補上，使用者不用手動建欄位。"""
    todos = notion_db._SCHEMAS["Todos"]

    assert "期間" in todos
    assert "優先度" in todos


def test_period_is_a_native_date_range():
    """用 Notion 原生的 date property（它本來就支援 start+end）。
    拆成「開始日」「結束日」兩欄要自己維護「結束不能早於開始」。"""
    assert notion_db._SCHEMAS["Todos"]["期間"] == {"date": {}}


def test_priority_options_are_p0_to_p3():
    options = notion_db._SCHEMAS["Todos"]["優先度"]["select"]["options"]

    assert [o["name"] for o in options] == ["P0", "P1", "P2", "P3"]


# ── todos_create ──────────────────────────────────────────

class FakePages:
    def __init__(self):
        self.created = []
        self.updated = []

    def create(self, parent, properties):
        self.created.append(properties)
        return {"id": "page-1"}

    def update(self, page_id, **kwargs):
        self.updated.append((page_id, kwargs))
        return {"id": page_id}


class FakeClient:
    def __init__(self):
        self.pages = FakePages()


def _install(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(notion_db, "_get_client", lambda: client)
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db-1")
    return client


def test_create_sends_a_date_range(monkeypatch):
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "出差", 1,
                           start=date(2026, 9, 1), end=date(2026, 9, 10))

    assert client.pages.created[0]["期間"] == {
        "date": {"start": "2026-09-01", "end": "2026-09-10"}
    }


def test_single_day_leaves_end_empty(monkeypatch):
    """只講一天時 end 留空 —— 兩邊填一樣的日期等於逼讀取端
    處理兩種等價表示法。"""
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 1, start=date(2026, 9, 8))

    assert client.pages.created[0]["期間"] == {
        "date": {"start": "2026-09-08", "end": None}
    }


def test_no_date_omits_the_field_entirely(monkeypatch):
    """Notion 的 date property 不接受 start=None，硬送整筆會失敗。"""
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 1)

    assert "期間" not in client.pages.created[0]


def test_priority_is_sent_as_select(monkeypatch):
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 1, priority="P0")

    assert client.pages.created[0]["優先度"] == {"select": {"name": "P0"}}


def test_no_priority_omits_the_field(monkeypatch):
    """select 不接受 name=None。而且「沒設優先度」跟「設成 P2」
    是兩件不同的事，不要偷偷補預設值。"""
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 1)

    assert "優先度" not in client.pages.created[0]


def test_unknown_priority_is_dropped(monkeypatch):
    """P9 不在選項裡。Notion 遇到未定義的 select 值會**擴充 schema**
    而不是報錯 —— 那種偏移完全沒有訊號（見 _SPEND_CATEGORIES 的註解）。"""
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 1, priority="P9")

    assert "優先度" not in client.pages.created[0]


def test_existing_fields_still_sent(monkeypatch):
    """加新欄位不能弄丟舊的。"""
    client = _install(monkeypatch)

    notion_db.todos_create("U1", "交資料", 7)
    props = client.pages.created[0]

    assert props["LocalId"] == {"number": 7}
    assert props["Done"] == {"checkbox": False}
    assert "分類" in props
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_notion_fields.py -q
```

預期：`KeyError: '期間'` 與 `TypeError: todos_create() got an unexpected keyword argument 'start'`

- [ ] **Step 3: 改 schema**

`notion_db.py` 的 `_SCHEMAS["Todos"]`（約 137-144 行），在 `"分類"` 那行之後加：

```python
        # 起訖日。Notion 原生 date property 支援 start+end，頁面上顯示成
        # 「9月1日 → 9月10日」，排序與篩選吃同一個型別。
        # 既有 DB 由 _ensure_properties 自動補上，現有資料一筆都不會動到。
        "期間": {"date": {}},
        "優先度": _select(("P0", "red"), ("P1", "orange"),
                          ("P2", "yellow"), ("P3", "gray")),
```

- [ ] **Step 4: 改 `todos_create`**

把 `notion_db.py` 的 `todos_create`（約 1049 行）整個換成：

```python
TODO_PRIORITIES = ("P0", "P1", "P2", "P3")


def _prop_date_range(start, end):
    """(date, date|None) → Notion date property，或 None（表示整個欄位不送）。

    Notion 的 date property 不接受 start=None，硬送整筆 create 會失敗。
    所以「沒有日期」的表示法是**不送這個欄位**，不是送一個空的。
    """
    if not start:
        return None
    return {"date": {"start": start.isoformat(),
                     "end": end.isoformat() if end else None}}


def todos_create(user_id, text, local_id, category=None,
                 start=None, end=None, priority=None):
    """建立一筆待辦。回 page_id 或 None。

    category 未指定時歸到「生活」—— 寧可分錯也不要留空，
    留空的話 Notion 上的分類檢視會漏掉這筆。

    start/end/priority 相反：沒設就**不送那個欄位**。「沒設截止日」
    跟「設成今天」是兩件不同的事，偷偷補預設值會讓隨手記的事
    隔天就變成逾期紅字。
    """
    db_id = get_or_create_db("Todos")
    client = _get_client()
    if not db_id or not client:
        return None

    props = {
        "Name": {"title": [{"text": {"content": text}}]},
        "UserId": {"rich_text": [{"text": {"content": user_id}}]},
        "Done": {"checkbox": False},
        "LocalId": {"number": int(local_id)},
        "分類": {"select": {"name": normalize_todo_category(category)}},
    }
    period = _prop_date_range(start, end)
    if period:
        props["期間"] = period
    # 不在白名單內的值直接丟掉：Notion 遇到未定義的 select 值會擴充
    # schema 而不是報錯，那種偏移完全沒有訊號（同 _SPEND_CATEGORIES）
    if priority in TODO_PRIORITIES:
        props["優先度"] = {"select": {"name": priority}}

    try:
        page = client.pages.create(parent={"database_id": db_id}, properties=props)
        return page["id"]
    except Exception as e:
        print(f"[notion] todos_create 失敗：{e}")
        return None
```

- [ ] **Step 5: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_notion_fields.py -q
```

預期：`10 passed`

- [ ] **Step 6: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q
```

預期：`1172 passed`

- [ ] **Step 7: Commit**

commit message（用 bash heredoc，見「開工前必讀」）：

```
feat: Todos 加「期間」與「優先度」欄位

期間用 Notion 原生 date range（start+end），不拆兩欄。
沒設日期 / 優先度時不送那個欄位：Notion 的 date 不接受 start=None，
而偷偷補預設優先度會讓「沒設」與「設成 P2」混為一談。
不在白名單內的優先度直接丟掉 —— Notion 遇到未定義的 select 值會
擴充 schema 而不是報錯。
```

要 add 的檔案：`notion_db.py tests/test_todo_notion_fields.py`

---

### Task 7: 讀回新欄位 + `todos_update_fields`

**Files:**
- Modify: `notion_db.py`（`todos_load_for_user` 約 952 行、`todos_load_all_users` 約 992 行）
- Test: `tests/test_todo_notion_fields.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_todo_notion_fields.py`：

```python
# ── 讀回 ──────────────────────────────────────────────────

class FakeDatabases:
    def __init__(self, results):
        self._results = results

    def query(self, **kwargs):
        return {"results": self._results}


def _row(text="交資料", local_id=1, period=None, priority=None):
    props = {
        "Name": {"title": [{"plain_text": text}]},
        "UserId": {"rich_text": [{"plain_text": "U1"}]},
        "LocalId": {"number": local_id},
        "Done": {"checkbox": False},
    }
    if period is not None:
        props["期間"] = {"date": period}
    if priority is not None:
        props["優先度"] = {"select": {"name": priority}}
    return {"id": f"page-{local_id}", "properties": props}


def _install_query(monkeypatch, rows):
    client = FakeClient()
    client.databases = FakeDatabases(rows)
    monkeypatch.setattr(notion_db, "_get_client", lambda: client)
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db-1")
    return client


def test_load_reads_the_date_range(monkeypatch):
    _install_query(monkeypatch, [
        _row(period={"start": "2026-09-01", "end": "2026-09-10"})])

    row = notion_db.todos_load_for_user("U1")[0]

    assert row["start"] == date(2026, 9, 1)
    assert row["end"] == date(2026, 9, 10)


def test_load_reads_a_single_day(monkeypatch):
    _install_query(monkeypatch, [_row(period={"start": "2026-09-08", "end": None})])

    row = notion_db.todos_load_for_user("U1")[0]

    assert row["start"] == date(2026, 9, 8)
    assert row["end"] is None


def test_old_rows_without_the_field_still_load(monkeypatch):
    """遷移前建立的資料列完全沒有這兩個欄位。
    這裡炸掉的話所有既有待辦會一起消失。"""
    _install_query(monkeypatch, [_row()])

    row = notion_db.todos_load_for_user("U1")[0]

    assert row["start"] is None
    assert row["end"] is None
    assert row["priority"] is None


def test_load_reads_priority(monkeypatch):
    _install_query(monkeypatch, [_row(priority="P0")])

    assert notion_db.todos_load_for_user("U1")[0]["priority"] == "P0"


def test_datetime_start_is_truncated_to_a_date(monkeypatch):
    """使用者在 Notion 上手動選日期時可能連時間一起選，
    存成 '2026-09-08T09:00:00.000+08:00'。"""
    _install_query(monkeypatch,
                   [_row(period={"start": "2026-09-08T09:00:00.000+08:00"})])

    assert notion_db.todos_load_for_user("U1")[0]["start"] == date(2026, 9, 8)


def test_garbage_date_does_not_kill_the_row(monkeypatch):
    _install_query(monkeypatch, [_row(period={"start": "not-a-date"})])

    row = notion_db.todos_load_for_user("U1")[0]

    assert row["start"] is None
    assert row["text"] == "交資料"


# ── todos_update_fields ───────────────────────────────────

def test_update_sends_the_date_range(monkeypatch):
    client = _install(monkeypatch)

    notion_db.todos_update_fields("page-1", start=date(2026, 9, 8))

    page_id, kwargs = client.pages.updated[0]
    assert page_id == "page-1"
    assert kwargs["properties"]["期間"] == {
        "date": {"start": "2026-09-08", "end": None}
    }


def test_update_sends_only_what_changed(monkeypatch):
    """補截止日時不該順手覆蓋優先度。"""
    client = _install(monkeypatch)

    notion_db.todos_update_fields("page-1", start=date(2026, 9, 8))

    assert "優先度" not in client.pages.updated[0][1]["properties"]


def test_update_with_nothing_does_not_call_notion(monkeypatch):
    """空的 update 是一次白花的 API 往返。"""
    client = _install(monkeypatch)

    assert notion_db.todos_update_fields("page-1") is False
    assert client.pages.updated == []
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_notion_fields.py -q
```

預期：`KeyError: 'start'` 與 `AttributeError: module 'notion_db' has no attribute 'todos_update_fields'`

- [ ] **Step 3: 寫最小實作**

在 `notion_db.py` 的 `_prop_date_range` 定義之後加一支讀日期區間的 helper：

```python
def _read_date_range(props, name):
    """Notion date property → (start, end)。兩個都是 datetime.date 或 None。

    遷移前建立的資料列沒有這個欄位，所以必須容忍缺欄位 ——
    這裡炸掉的話所有既有待辦會一起消失。

    使用者在 Notion 上可能連時間一起選，存成
    '2026-09-08T09:00:00.000+08:00'，所以取前 10 個字元。
    """
    from datetime import date as _date

    def _one(value):
        if not value:
            return None
        try:
            y, m, d = str(value)[:10].split("-")
            return _date(int(y), int(m), int(d))
        except (ValueError, TypeError):
            return None

    raw = (props.get(name, {}) or {}).get("date") or {}
    return _one(raw.get("start")), _one(raw.get("end"))
```

`todos_load_for_user` 的 `out.append({...})`（約 979 行）改成：

```python
            start, end = _read_date_range(props, "期間")
            out.append({
                "page_id": r["id"],
                "local_id": int(local_id),
                "text": text,
                "done": bool(done),
                "category": _read_select(props, "分類"),
                "start": start,
                "end": end,
                "priority": _read_select(props, "優先度") or None,
            })
```

`todos_load_all_users` 的 `out.setdefault(...)`（約 1015 行）同樣改成：

```python
            start, end = _read_date_range(props, "期間")
            out.setdefault(user_id, []).append({
                "page_id": r["id"],
                "local_id": int(local_id),
                "text": text,
                "done": False,
                "category": _read_select(props, "分類"),
                "start": start,
                "end": end,
                "priority": _read_select(props, "優先度") or None,
            })
```

在 `todos_delete` 之前加：

```python
def todos_update_fields(page_id, start=None, end=None, priority=None):
    """補上截止日或優先度。回 True/False。

    只送有給的欄位 —— 補截止日時不該順手覆蓋優先度。
    什麼都沒給就不打 API：空的 update 是一次白花的往返。
    """
    props = {}
    period = _prop_date_range(start, end)
    if period:
        props["期間"] = period
    if priority in TODO_PRIORITIES:
        props["優先度"] = {"select": {"name": priority}}
    if not props:
        return False

    client = _get_client()
    if not client:
        return False
    try:
        client.pages.update(page_id=page_id, properties=props)
        return True
    except Exception as e:
        print(f"[notion] todos_update_fields 失敗：{e}")
        return False
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_notion_fields.py -q
```

預期：`19 passed`

- [ ] **Step 5: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q
```

預期：`1181 passed`

- [ ] **Step 6: Commit**

commit message：

```
feat: 讀回 Todos 的期間/優先度 + todos_update_fields

_read_date_range 容忍缺欄位與帶時間的值：遷移前的資料列沒有這兩欄，
這裡炸掉的話所有既有待辦會一起消失。
update 只送有給的欄位 —— 補截止日不該覆蓋優先度。
```

要 add 的檔案：`notion_db.py tests/test_todo_notion_fields.py`

---

## Segment C：待命狀態與 LINE 流程

### Task 8: `personal` 待命狀態

**Files:**
- Modify: `personal.py`
- Test: `tests/test_pending_todo.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_pending_todo.py`：

```python
"""按下 ➕ 之後的「待命」狀態。

只活在記憶體：壽命是幾秒，為它多一次 Notion 往返不划算。
Railway 重啟丟掉的代價只是使用者要重按一次 ➕。
"""

from datetime import date, timedelta

import pytest

import personal


@pytest.fixture(autouse=True)
def _clean():
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()
    yield
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()


def test_not_pending_by_default():
    assert personal.is_pending_todo("U1") is False


def test_start_makes_it_pending():
    personal.start_pending_todo("U1")

    assert personal.is_pending_todo("U1") is True


def test_pending_is_per_user():
    personal.start_pending_todo("U1")

    assert personal.is_pending_todo("U2") is False


def test_clear_ends_it():
    personal.start_pending_todo("U1")
    personal.clear_pending_todo("U1")

    assert personal.is_pending_todo("U1") is False


def test_clearing_when_not_pending_is_harmless():
    personal.clear_pending_todo("U1")     # 不該炸

    assert personal.is_pending_todo("U1") is False


def test_pressing_plus_twice_restarts_the_clock():
    """按兩次 ➕ 不該報錯，而且要重新計時。"""
    personal.start_pending_todo("U1")
    old = personal._PENDING_TODO["U1"]
    personal.start_pending_todo("U1")

    assert personal._PENDING_TODO["U1"] >= old
    assert personal.is_pending_todo("U1") is True


def test_pending_expires():
    """沒有逾時的話，一個忘掉的待命狀態會把使用者隔天隨口講的
    任何一句話變成待辦。"""
    personal.start_pending_todo("U1")
    personal._PENDING_TODO["U1"] -= timedelta(
        minutes=personal.PENDING_TODO_TIMEOUT_MINUTES + 1)

    assert personal.is_pending_todo("U1") is False


def test_expiry_boundary_is_still_pending():
    personal.start_pending_todo("U1")
    personal._PENDING_TODO["U1"] -= timedelta(
        minutes=personal.PENDING_TODO_TIMEOUT_MINUTES - 1)

    assert personal.is_pending_todo("U1") is True


def test_expired_state_is_swept():
    """逾時的項目要真的刪掉，不然 dict 會一直長。"""
    personal.start_pending_todo("U1")
    personal._PENDING_TODO["U1"] -= timedelta(
        minutes=personal.PENDING_TODO_TIMEOUT_MINUTES + 1)

    personal.is_pending_todo("U1")

    assert "U1" not in personal._PENDING_TODO
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_pending_todo.py -q
```

預期：`AttributeError: module 'personal' has no attribute '_PENDING_TODO'`

- [ ] **Step 3: 寫最小實作**

在 `personal.py` 的 `_REMINDER_NEXT_ID = {}` 之後加：

```python
# ────────────────────────────────────────
# 待辦「待命」狀態：使用者按了 ➕，下一句話就是待辦內容
#
# user_id → 按下 ➕ 的時間。**不進 Notion**：壽命只有幾秒，
# 為它多一次 API 往返不划算，Railway 重啟丟掉的代價只是重按一次。
# ────────────────────────────────────────

_PENDING_TODO = {}

# 沒有逾時的話，一個忘掉的待命狀態會把使用者隔天隨口講的
# 任何一句話變成待辦。
PENDING_TODO_TIMEOUT_MINUTES = 10
```

在 `add_todo` 之前加：

```python
def start_pending_todo(user_id):
    """進入待命：下一句話當待辦內容。按兩次就重新計時，不報錯。"""
    with _LOCK:
        _PENDING_TODO[user_id] = now_tpe()


def clear_pending_todo(user_id):
    """離開待命。不在待命中也不會炸。"""
    with _LOCK:
        _PENDING_TODO.pop(user_id, None)


def is_pending_todo(user_id):
    """待命中且未逾時回 True。逾時的順手掃掉，不然 dict 會一直長。"""
    with _LOCK:
        started = _PENDING_TODO.get(user_id)
        if not started:
            return False
        if now_tpe() - started > timedelta(minutes=PENDING_TODO_TIMEOUT_MINUTES):
            _PENDING_TODO.pop(user_id, None)
            return False
        return True
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_pending_todo.py -q
```

預期：`9 passed`

- [ ] **Step 5: Commit**

commit message：

```
feat: 待辦「待命」狀態（按 ➕ 之後下一句話就是內容）

只活在記憶體：壽命幾秒，多一次 Notion 往返不划算。
10 分鐘逾時是必要的 —— 沒有的話一個忘掉的待命狀態會把使用者
隔天隨口講的任何一句話變成待辦。
```

要 add 的檔案：`personal.py tests/test_pending_todo.py`

---

### Task 9: `add_todo` 收日期與優先度 + `set_todo_due`

**Files:**
- Modify: `personal.py`（`add_todo` 約 74 行、`_ensure_todos_loaded` 約 48-58 行）
- Test: `tests/test_pending_todo.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_pending_todo.py`：

```python
# ── add_todo 帶日期與優先度 ───────────────────────────────

def test_add_todo_keeps_the_date_in_memory():
    """Notion 沒設定時（本機、以及 NOTION_TOKEN 掉了的時候）
    仍然要記得日期，不然清單卡片會顯示成沒有截止日。"""
    personal.add_todo("U1", "交資料", start=date(2026, 9, 8), priority="P0")

    item = personal.list_todos("U1")[0]
    assert item["start"] == date(2026, 9, 8)
    assert item["end"] is None
    assert item["priority"] == "P0"


def test_add_todo_without_dates_still_works():
    """既有呼叫端（/待辦 加 X）不帶新參數。"""
    personal.add_todo("U1", "交資料")

    item = personal.list_todos("U1")[0]
    assert item["start"] is None
    assert item["priority"] is None


def test_set_todo_due_updates_in_memory():
    tid = personal.add_todo("U1", "交資料")

    assert personal.set_todo_due("U1", tid, date(2026, 9, 8)) is True
    assert personal.list_todos("U1")[0]["start"] == date(2026, 9, 8)


def test_set_todo_due_on_a_missing_id():
    assert personal.set_todo_due("U1", 99, date(2026, 9, 8)) is False
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_pending_todo.py -q
```

預期：`TypeError: add_todo() got an unexpected keyword argument 'start'`

- [ ] **Step 3: 寫最小實作**

把 `personal.py` 的 `add_todo` 換成：

```python
def add_todo(user_id, text, start=None, end=None, priority=None):
    """新增一筆待辦，回 local id。

    start/end/priority 都是選填 —— 既有的 `/待辦 加 X` 不帶它們。
    沒給就留 None，**不補預設值**：隨手記的事被預設成今天到期，
    隔天信裡就是一行紅字。
    """
    _ensure_todos_loaded(user_id)
    with _LOCK:
        next_id = _TODO_NEXT_ID.get(user_id, 0) + 1
        _TODO_NEXT_ID[user_id] = next_id
        item = {
            "id": next_id, "text": text, "done": False,
            "created_at": datetime.now(),
            "page_id": None,
            "start": start, "end": end, "priority": priority,
        }
        _TODOS.setdefault(user_id, []).append(item)
    # Notion 寫在 lock 外（避免持鎖打網路 IO）
    if _notion_enabled():
        try:
            import notion_db
            page_id = notion_db.todos_create(
                user_id, text, next_id,
                start=start, end=end, priority=priority,
            )
            if page_id:
                with _LOCK:
                    item["page_id"] = page_id
        except Exception as e:
            print(f"[personal/todos] Notion create 失敗：{e}")
    return next_id


def set_todo_due(user_id, todo_id, start, end=None):
    """事後補上截止日（防呆按鈕走這裡）。找不到編號回 False。

    記憶體先更新再打 Notion —— 跟 add_todo 同一套：Notion 掛掉時
    使用者這一輪看到的東西仍然是對的。
    """
    _ensure_todos_loaded(user_id)
    page_id = None
    with _LOCK:
        for t in _TODOS.get(user_id, []):
            if t["id"] == todo_id:
                t["start"], t["end"] = start, end
                page_id = t.get("page_id")
                break
        else:
            return False
    if page_id and _notion_enabled():
        try:
            import notion_db
            notion_db.todos_update_fields(page_id, start=start, end=end)
        except Exception as e:
            print(f"[personal/todos] Notion 補截止日失敗：{e}")
    return True
```

同時把 `_ensure_todos_loaded` 從 Notion 讀回來的 `_TODOS[user_id].append({...})`
（約 48-58 行）換成：

```python
                _TODOS[user_id].append({
                    "id": r["local_id"],
                    "text": r["text"],
                    "done": False,
                    "created_at": datetime.now(),
                    "page_id": r["page_id"],
                    "start": r.get("start"),
                    "end": r.get("end"),
                    "priority": r.get("priority"),
                })
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_pending_todo.py -q
```

預期：`13 passed`

- [ ] **Step 5: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q
```

預期：`1194 passed`

- [ ] **Step 6: Commit**

commit message：

```
feat: add_todo 收起訖日與優先度 + set_todo_due

沒給就留 None，不補預設值：隨手記的事被預設成今天到期，
隔天信裡就是一行紅字。
```

要 add 的檔案：`personal.py tests/test_pending_todo.py`

---

### Task 10: `flex_builder` 的兩個新卡片

> **順序說明：** Task 11（清單卡片改版）與這一項本來可以合併，但拆開讓
> Task 12 的待命攔截有東西可以呼叫。這一項只做**新增**，不動既有的
> `todo_list_flex`。

**Files:**
- Modify: `flex_builder.py`
- Test: `tests/test_todo_flex.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_todo_flex.py`：

```python
"""待辦清單卡片與防呆按鈕卡。"""

import json
from datetime import date

import flex_builder


def _dump(msg):
    return json.dumps(msg, ensure_ascii=False)


CHOICES = (("今天", "today"), ("明天", "tomorrow"), ("不設", "none"))


# ── 防呆按鈕卡 ────────────────────────────────────────────

def test_due_prompt_carries_the_todo_id():
    """按鈕要更新**那一筆**，不是新增第二筆。"""
    msg = flex_builder.todo_due_prompt_flex(7, CHOICES)

    assert "id=7" in _dump(msg)


def test_due_prompt_has_every_choice():
    text = _dump(flex_builder.todo_due_prompt_flex(7, CHOICES))

    for label, key in CHOICES:
        assert label in text
        assert f"d={key}" in text


def test_due_prompt_uses_postback_not_message():
    """message 型的按鈕會在對話裡留下一句「今天」，
    而且會被待命攔截或指令解析再處理一次。"""
    text = _dump(flex_builder.todo_due_prompt_flex(7, CHOICES))

    assert "postback" in text
    assert '"type": "message"' not in text
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_flex.py -q
```

預期：`AttributeError: module 'flex_builder' has no attribute 'todo_due_prompt_flex'`

- [ ] **Step 3: 寫最小實作**

附加到 `flex_builder.py` 的 `todo_list_flex` 之後：

```python
def todo_due_prompt_flex(todo_id, choices):
    """「還沒設截止日」的按鈕卡。choices: ((顯示字, key), ...)。

    用 postback 而不是 message 型按鈕：message 會在對話裡留下一句
    「今天」，而且那句話會再被指令解析（甚至待命攔截）處理一次。
    """
    buttons = [{
        "type": "button",
        "style": "secondary", "height": "sm", "margin": "sm",
        "action": {
            "type": "postback",
            "label": label,
            "data": _postback("todo_set_due", id=todo_id, d=key),
            "displayText": f"📅 {label}",
        },
    } for label, key in choices]

    bubble = {
        "type": "bubble", "size": "kilo",
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "backgroundColor": _LIGHT_BG, "paddingAll": "lg",
            "contents": [
                {"type": "text", "text": "什麼時候要做？",
                 "size": "sm", "weight": "bold", "color": _TEXT_DARK},
            ] + buttons,
        },
    }
    return _wrap(bubble, alt="選一個截止日")
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_flex.py -q
```

預期：`3 passed`

- [ ] **Step 5: Commit**

commit message：

```
feat: 截止日防呆按鈕卡

按鈕一律用 postback：message 型會在對話裡留下一句話，
那句話會再被指令解析或待命攔截處理一次。
```

要 add 的檔案：`flex_builder.py tests/test_todo_flex.py`

---

### Task 11: 清單卡片加 ➕、日期、優先度

**Files:**
- Modify: `flex_builder.py:82-143`（`todo_list_flex`）
- Test: `tests/test_todo_flex.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_todo_flex.py`：

```python
# ── 清單卡片 ──────────────────────────────────────────────

def _item(tid=1, text="交資料", start=None, end=None, priority=None):
    return {"id": tid, "text": text, "start": start, "end": end,
            "priority": priority}


def test_list_has_an_add_button():
    """使用者要的是「按一顆按鈕就能加」，不是打 /待辦 加。"""
    msg = flex_builder.todo_list_flex([_item()])

    assert "todo_add_start" in _dump(msg)


def test_empty_list_also_has_the_add_button():
    """清單空的時候最需要那顆按鈕。"""
    msg = flex_builder.todo_list_flex([])

    assert "todo_add_start" in _dump(msg)


def test_due_date_is_shown():
    msg = flex_builder.todo_list_flex([_item(start=date(2026, 9, 8))])

    assert "9/08" in _dump(msg)


def test_date_range_shows_both_ends():
    msg = flex_builder.todo_list_flex(
        [_item(start=date(2026, 9, 1), end=date(2026, 9, 10))])
    text = _dump(msg)

    assert "9/01" in text and "9/10" in text


def test_priority_is_shown():
    msg = flex_builder.todo_list_flex([_item(priority="P0")])

    assert "P0" in _dump(msg)


def test_item_without_dates_still_renders():
    """既有待辦兩欄都是空的。這裡炸掉的話清單整個打不開。"""
    msg = flex_builder.todo_list_flex([{"id": 1, "text": "交資料"}])

    assert "交資料" in _dump(msg)


def test_complete_button_survived():
    """加東西不能弄掉既有的按鈕。"""
    assert "todo_complete" in _dump(flex_builder.todo_list_flex([_item()]))
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_flex.py -q
```

預期：`assert 'todo_add_start' in ...` FAIL

- [ ] **Step 3: 寫最小實作**

在 `flex_builder.py` 的 `todo_list_flex` 之前加：

```python
_WEEKDAY_NAMES = "一二三四五六日"


def _due_label(item):
    """待辦的日期與優先度標籤。兩個都沒有回空字串。

    既有待辦兩欄都是空的，所以每個欄位都要容忍缺值 ——
    這裡炸掉的話清單整個打不開。
    """
    parts = []
    start, end = item.get("start"), item.get("end")
    if start:
        label = f"{start.month}/{start.day:02d}（{_WEEKDAY_NAMES[start.weekday()]}）"
        if end:
            label += f" → {end.month}/{end.day:02d}"
        parts.append(f"📅 {label}")
    if item.get("priority"):
        parts.append(item["priority"])
    return "　".join(parts)


def _add_todo_button():
    """清單卡片底部的「➕ 加一件事」。

    刻意不改 Rich Menu 加一格：那要重產選單圖 + 重跑 setup-richmenu，
    而清單卡片上這顆已經夠用（使用者本來就是先按「待辦」看清單）。
    """
    return {
        "type": "box", "layout": "vertical",
        "paddingAll": "md", "backgroundColor": _LIGHT_BG,
        "contents": [{
            "type": "button",
            "style": "primary", "color": _BROWN, "height": "sm",
            "action": {
                "type": "postback",
                "label": "➕ 加一件事",
                "data": _postback("todo_add_start"),
                "displayText": "➕ 加一件事",
            },
        }],
    }
```

`todo_list_flex` 裡兩處 `"footer": _footer_tip(...)` 都換成：

```python
            "footer": _add_todo_button(),
```

每一列的內容（約 110-131 行的 `rows.append({...})`）換成：

```python
        due = _due_label(t)
        row_texts = [{
            "type": "text", "text": f"⬜ {t['text']}",
            "size": "sm", "color": _TEXT_DARK,
            "wrap": True,
        }]
        if due:
            row_texts.append({
                "type": "text", "text": due,
                "size": "xs", "color": _TEXT_LIGHT, "margin": "xs",
            })
        rows.append({
            "type": "box", "layout": "horizontal",
            "spacing": "md", "alignItems": "center",
            "contents": [
                {"type": "box", "layout": "vertical", "flex": 5,
                 "contents": row_texts},
                {
                    "type": "button",
                    "style": "primary", "color": _GREEN,
                    "height": "sm", "flex": 2,
                    "action": {
                        "type": "postback",
                        "label": "完成",
                        "data": _postback("todo_complete", id=t["id"]),
                        "displayText": f"✅ 完成 [{t['id']}] {text_preview}",
                    },
                },
            ],
        })
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_flex.py -q
```

預期：`10 passed`

- [ ] **Step 5: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q
```

預期：`1204 passed`

- [ ] **Step 6: Commit**

commit message：

```
feat: 待辦卡片加 ➕、日期、優先度

➕ 放在清單卡片而不是 Rich Menu：後者要重產選單圖 + 重跑
setup-richmenu，而使用者本來就是先按「待辦」看清單。
每個新欄位都容忍缺值 —— 既有待辦兩欄都是空的。
```

要 add 的檔案：`flex_builder.py tests/test_todo_flex.py`

---

### Task 12: `command_router` 待命攔截

**Files:**
- Modify: `command_router.py:865-880`（`handle`）
- Test: `tests/test_pending_todo_router.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_pending_todo_router.py`：

```python
"""待命中收到訊息時 command_router 的行為。

規則（使用者 2026-09-05 確認）：
  認得出是已知指令 → 解除待命、回一句「已取消」，然後照常執行那個指令
  認不出來         → 當作待辦內容記下來

刻意不是「以 / 開頭就解除」：Rich Menu 的按鈕送的是**文字訊息**，
而且不是每顆都有斜線（setup_richmenu.py 的「記一筆」就沒有）。
只認斜線的話，按「記一筆」會得到一筆叫「記一筆」的待辦。
"""

import json

import pytest

import command_router as cr
import personal

PERSONAL_CTX = {"source_type": "user", "user_id": "U1"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()
    # 日期解析不該打 AI
    import todo_parse
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "NONE")
    yield
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()


def _text(reply):
    """handle 可能回 str / dict / list，全部攤成一段字串好斷言。"""
    return json.dumps(reply, ensure_ascii=False, default=str)


def test_unknown_text_becomes_a_todo():
    personal.start_pending_todo("U1")

    reply = cr.handle("交社宅資料", PERSONAL_CTX)

    assert personal.list_todos("U1")[0]["text"] == "交社宅資料"
    assert "已記下" in _text(reply)


def test_recording_ends_the_pending_state():
    """記完就離開待命，下一句話是普通訊息。"""
    personal.start_pending_todo("U1")
    cr.handle("交社宅資料", PERSONAL_CTX)

    assert personal.is_pending_todo("U1") is False


def test_a_known_command_cancels_instead_of_recording():
    """按了 ➕ 又改按別的按鈕時，不該記下一筆叫「快過期」的待辦。"""
    personal.start_pending_todo("U1")

    reply = cr.handle("help", PERSONAL_CTX)

    assert personal.list_todos("U1") == []
    assert "已取消" in _text(reply)
    assert personal.is_pending_todo("U1") is False


def test_the_cancelled_command_still_runs():
    """解除待命之後那個指令要照常執行，不是吞掉。"""
    personal.start_pending_todo("U1")

    reply = _text(cr.handle("help", PERSONAL_CTX))

    assert "待辦清單" in reply          # HELP_TEXT 的內容


def test_bare_richmenu_button_also_cancels():
    """Rich Menu 的「記一筆」送的是**沒有斜線**的裸文字。"""
    personal.start_pending_todo("U1")

    cr.handle("記一筆", PERSONAL_CTX)

    assert personal.list_todos("U1") == []


def test_not_pending_means_normal_handling():
    """沒按 ➕ 時隨口講的話不該變成待辦。"""
    reply = cr.handle("交社宅資料", PERSONAL_CTX)

    assert personal.list_todos("U1") == []
    assert reply is None or "已記下" not in _text(reply)


def test_group_chat_never_records():
    """待辦是個人功能。群組裡就算狀態殘留也不能記。"""
    personal.start_pending_todo("U1")

    cr.handle("交社宅資料", {"source_type": "group", "user_id": "U1"})

    assert personal.list_todos("U1") == []


def test_date_and_priority_are_parsed():
    personal.start_pending_todo("U1")

    cr.handle("P0 明天交社宅資料", PERSONAL_CTX)

    item = personal.list_todos("U1")[0]
    assert item["text"] == "交社宅資料"
    assert item["priority"] == "P0"
    assert item["start"] is not None


def test_missing_date_triggers_the_fallback_buttons():
    """沒講日期時：內容先記下來，再跳按鈕。"""
    personal.start_pending_todo("U1")

    reply = cr.handle("交社宅資料", PERSONAL_CTX)

    assert personal.list_todos("U1")[0]["text"] == "交社宅資料"
    assert "todo_set_due" in _text(reply)


def test_date_given_means_no_buttons():
    personal.start_pending_todo("U1")

    reply = cr.handle("明天交社宅資料", PERSONAL_CTX)

    assert "todo_set_due" not in _text(reply)


def test_empty_message_does_not_record_a_blank_todo():
    personal.start_pending_todo("U1")

    cr.handle("   ", PERSONAL_CTX)

    assert personal.list_todos("U1") == []
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_pending_todo_router.py -q
```

預期：多數 FAIL（待命中的文字目前直接掉進 `parse()` 回 `None`）

- [ ] **Step 3: 寫最小實作**

在 `command_router.py` 的 `_handle_todo_subcmd`（約 523 行）之後加：

```python
_TODO_DUE_CHOICES = (
    ("今天", "today"),
    ("明天", "tomorrow"),
    ("週五", "friday"),
    ("下週一", "next_monday"),
    ("不設", "none"),
)


def _format_due(start, end):
    """(date, date|None) → '9/08（一）' 或 '9/01（二） → 9/10（四）'。"""
    names = "一二三四五六日"

    def _one(d):
        return f"{d.month}/{d.day:02d}（{names[d.weekday()]}）"

    return _one(start) + (f" → {_one(end)}" if end else "")


def _record_spoken_todo(user_id, text):
    """待命中收到的自由文字 → 記成待辦。回覆給 reply_message。

    **內容先寫進 Notion，才問缺的欄位。** 順序是刻意的：使用者中途跑掉、
    或 LINE 連線斷了，至少那件事已經記住了。先問再存的話，中斷等於
    整件事沒記到 —— 那正是待辦最不能發生的事。
    """
    import personal
    import todo_parse
    from tz_utils import today_tpe

    parsed = todo_parse.parse(text, today_tpe())
    content = parsed["text"]
    if not content:
        return "沒聽到內容，取消新增。"

    tid = personal.add_todo(
        user_id, content,
        start=parsed["start"], end=parsed["end"], priority=parsed["priority"],
    )

    lines = [f"✅ 已記下：{content}"]
    if parsed["start"]:
        tail = f"📅 {_format_due(parsed['start'], parsed['end'])}"
        if parsed["priority"]:
            tail += f"　{parsed['priority']}"
        lines.append(tail)
        return "\n".join(lines)

    lines.append("⚠️ 還沒設截止日")
    from flex_builder import todo_due_prompt_flex
    # 兩個都缺時只問截止日，優先度留空。連問兩輪按鈕會讓
    # 「按一下就好」變成「按三下」；優先度可以事後在 Notion 上改，
    # 截止日不補則會從信裡消失 —— 後果不對等。
    return ["\n".join(lines), todo_due_prompt_flex(tid, _TODO_DUE_CHOICES)]


# 「解除待命，但那個指令照常跑」的哨兵。用一個獨一無二的物件而不是
# None / False：那兩個都是合法的回覆值。
_PENDING_CANCELLED = object()


def _intercept_pending_todo(text, ctx, parsed):
    """待命中的訊息處理。

    回傳語意有三種：
      None                → 不在待命，照常處理
      _PENDING_CANCELLED  → 解除待命，但那個指令要照常執行
      其他                → 已記成待辦，這一輪結束
    """
    import personal

    user_id = (ctx or {}).get("user_id")
    if not user_id or not _is_personal_chat(ctx):
        return None
    if not personal.is_pending_todo(user_id):
        return None

    personal.clear_pending_todo(user_id)

    if parsed:
        return _PENDING_CANCELLED
    if not (text or "").strip():
        return "沒聽到內容，取消新增。"
    return _record_spoken_todo(user_id, text)
```

接著把既有的 `def handle(text, ctx=None):` **改名**為 `def _dispatch(text, ctx, parsed):`，
並刪掉它開頭的 `parsed = parse(text)` 那一行（`parsed` 改由參數傳入）。
函式本體其餘部分**一個字都不動**。

然後新增新的 `handle`：

```python
def handle(text, ctx=None):
    """parse + dispatch；回字串 / dict / list（給 reply_message 直接送）或 None。

    待命攔截排在最前面：使用者按了 ➕ 之後講的話，只要不是已知指令
    就記成待辦。已知指令則解除待命、回一句「已取消」，然後照常執行 ——
    Rich Menu 的按鈕送的是**文字訊息**且不一定有斜線（`記一筆` 就沒有），
    所以「以 / 開頭才解除」行不通。

    攔截必須排在所有指令分派之前（否則使用者講的「待辦」「快過期」
    會先被指令吃掉），但在 parse 之後（否則已知指令會被記成待辦）。
    """
    parsed = parse(text)
    intercepted = _intercept_pending_todo(text, ctx, parsed)

    if intercepted is not None and intercepted is not _PENDING_CANCELLED:
        return intercepted

    result = _dispatch(text, ctx, parsed)

    if intercepted is _PENDING_CANCELLED:
        note = "已取消新增待辦。"
        if result is None:
            return note
        return [note] + (result if isinstance(result, list) else [result])
    return result
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_pending_todo_router.py -q
```

預期：`11 passed`

- [ ] **Step 5: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q
```

預期：`1215 passed`

- [ ] **Step 6: Commit**

commit message：

```
feat: 待命中的訊息記成待辦，已知指令則解除待命

攔截排在所有指令分派之前，但在 parse 之後 —— 已知指令要解除待命
而不是被記成待辦。「以 / 開頭才解除」行不通：Rich Menu 的「記一筆」
送的是沒有斜線的裸文字，會被記成一筆叫「記一筆」的待辦。
內容先寫進 Notion 才問缺的欄位：中斷時至少那件事已經記住了。
```

要 add 的檔案：`command_router.py tests/test_pending_todo_router.py`

---

### Task 13: `todo_add_start` / `todo_set_due` postback

**Files:**
- Modify: `command_router.py:1046-1056`（`handle_postback`）
- Test: `tests/test_todo_postback.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_todo_postback.py`：

```python
"""➕ 與截止日按鈕的 postback。"""

import json
from datetime import date, timedelta

import pytest

import command_router as cr
import personal


@pytest.fixture(autouse=True)
def _clean():
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()
    yield
    personal._PENDING_TODO.clear()
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()


def _text(reply):
    return json.dumps(reply, ensure_ascii=False, default=str)


# ── ➕ ────────────────────────────────────────────────────

def test_plus_enters_pending_state():
    cr.handle_postback("action=todo_add_start", "U1")

    assert personal.is_pending_todo("U1") is True


def test_plus_says_go_ahead():
    """使用者要知道機器人在等他講話。"""
    reply = cr.handle_postback("action=todo_add_start", "U1")

    assert "請說" in _text(reply)


def test_pressing_plus_twice_is_harmless():
    cr.handle_postback("action=todo_add_start", "U1")
    cr.handle_postback("action=todo_add_start", "U1")

    assert personal.is_pending_todo("U1") is True


# ── 截止日按鈕 ────────────────────────────────────────────

def test_today_sets_todays_date(monkeypatch):
    monkeypatch.setattr(cr, "_due_from_key",
                        lambda key, today: date(2026, 9, 5) if key == "today" else None)
    tid = personal.add_todo("U1", "交資料")

    cr.handle_postback(f"action=todo_set_due&id={tid}&d=today", "U1")

    assert personal.list_todos("U1")[0]["start"] == date(2026, 9, 5)


def test_none_leaves_it_unset():
    """「不設」不是「設成今天」。"""
    tid = personal.add_todo("U1", "交資料")

    reply = cr.handle_postback(f"action=todo_set_due&id={tid}&d=none", "U1")

    assert personal.list_todos("U1")[0]["start"] is None
    assert "不設" in _text(reply)


def test_missing_todo_id_is_reported():
    reply = cr.handle_postback("action=todo_set_due&id=99&d=today", "U1")

    assert "找不到" in _text(reply)


def test_reply_shows_the_date_that_was_set():
    tid = personal.add_todo("U1", "交資料")

    reply = _text(cr.handle_postback(f"action=todo_set_due&id={tid}&d=tomorrow", "U1"))

    assert "已設" in reply or "📅" in reply


# ── _due_from_key（純邏輯）────────────────────────────────

SAT = date(2026, 9, 5)


def test_key_today():
    assert cr._due_from_key("today", SAT) == SAT


def test_key_tomorrow():
    assert cr._due_from_key("tomorrow", SAT) == SAT + timedelta(days=1)


def test_key_friday_takes_the_next_friday():
    """週六按「週五」→ 下一個週五 9/11，不是已經過去的 9/04。
    按鈕設出一個昨天的截止日，等於一按就逾期。"""
    assert cr._due_from_key("friday", SAT) == date(2026, 9, 11)


def test_key_friday_on_a_friday_is_today():
    """在週五按「週五」就是今天 —— 「今天要交」是最常見的說法。"""
    assert cr._due_from_key("friday", date(2026, 9, 11)) == date(2026, 9, 11)


def test_key_next_monday():
    assert cr._due_from_key("next_monday", SAT) == date(2026, 9, 7)


def test_key_none():
    assert cr._due_from_key("none", SAT) is None


def test_unknown_key():
    assert cr._due_from_key("whatever", SAT) is None
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_postback.py -q
```

預期：`AttributeError: module 'command_router' has no attribute '_due_from_key'`

- [ ] **Step 3: 寫最小實作**

在 `command_router.py` 的 `_record_spoken_todo` 之後加：

```python
def _due_from_key(key, today):
    """防呆按鈕的 key → date。'none' 與認不出來的 key 都回 None。

    複用 todo_parse 的星期演算法，不要在這裡重寫一次 —— 那支已經有
    跨月跨年的測試守著。
    """
    from datetime import timedelta

    import todo_parse

    if key == "today":
        return today
    if key == "tomorrow":
        return today + timedelta(days=1)
    if key == "friday":
        # 下一個週五，今天是週五就取今天。**不能用「本週」**：
        # 週六按下去會得到昨天，一按就逾期。
        return todo_parse._weekday_date(today, None, 4)
    if key == "next_monday":
        return todo_parse._weekday_date(today, "下", 0)
    return None
```

在 `handle_postback` 的 `if action == "todo_complete":` 之前加：

```python
        if action == "todo_add_start":
            import personal
            personal.start_pending_todo(user_id)
            return ("請說。\n\n"
                    "可以一句話講完，例如：\n"
                    "　P0 下週一交社宅資料\n\n"
                    "（10 分鐘內沒說就自動取消）")

        if action == "todo_set_due":
            tid = _int_param("id")
            key = (parsed.get("d") or [""])[0]
            import personal
            from tz_utils import today_tpe

            due = _due_from_key(key, today_tpe())
            if due is None:
                # 「不設」不是「設成今天」——沒有截止日的待辦仍然存在，
                # 只是不會進每日信。LINE 打「待辦」照樣看得到。
                if key == "none":
                    return "好，這筆不設截止日。（LINE 打「待辦」還是看得到）"
                return "認不出那個日期選項。"

            if not personal.set_todo_due(user_id, tid, due):
                return f"找不到編號 {tid} 的待辦。"
            return f"✅ 已設截止日\n📅 {_format_due(due, None)}"
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_todo_postback.py -q
```

預期：`13 passed`

- [ ] **Step 5: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q
```

預期：`1228 passed`

- [ ] **Step 6: Commit**

commit message：

```
feat: ➕ 與截止日按鈕的 postback

_due_from_key 複用 todo_parse._weekday_date，不重寫星期演算法
（那支已經有跨月跨年的測試守著）。
「不設」不是「設成今天」：沒有截止日的待辦仍然存在，只是不進信。
```

要 add 的檔案：`command_router.py tests/test_todo_postback.py`

---

## Segment D：每日信

### Task 14: `todos_due_today` + `format_today_todos`

**Files:**
- Modify: `personal.py`（`format_todos` 之後）
- Test: `tests/test_today_todos.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_today_todos.py`：

```python
"""每日信的「今日待辦」篩選與排版。

篩選規則（使用者 2026-09-05 確認）：截止日 ≤ 今天  OR  優先度 = P0

兩個維度刻意不合併：截止日回答「什麼時候該做」，P0 回答「不管什麼
時候都得盯著」。只用日期篩，沒設日期的重要事情會消失；只用優先度篩，
時效性就沒了。
"""

from datetime import date

import pytest

import personal

TODAY = date(2026, 9, 5)


@pytest.fixture(autouse=True)
def _clean():
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()
    yield
    personal._TODOS.clear()
    personal._TODO_NEXT_ID.clear()
    personal._TODOS_LOADED_USERS.clear()


def _add(text, start=None, end=None, priority=None):
    return personal.add_todo("U1", text, start=start, end=end, priority=priority)


def _texts(rows):
    return [r["text"] for r in rows]


# ── 篩選 ──────────────────────────────────────────────────

def test_due_today_is_included():
    _add("繳健保費", start=TODAY)

    assert _texts(personal.todos_due_today("U1", TODAY)) == ["繳健保費"]


def test_overdue_is_included():
    _add("交資料", start=date(2026, 9, 1))

    assert _texts(personal.todos_due_today("U1", TODAY)) == ["交資料"]


def test_future_is_excluded():
    _add("下週的事", start=date(2026, 9, 12))

    assert personal.todos_due_today("U1", TODAY) == []


def test_p0_without_a_date_is_included():
    """沒設日期的重要事情不該消失。"""
    _add("準備面試", priority="P0")

    assert _texts(personal.todos_due_today("U1", TODAY)) == ["準備面試"]


def test_p0_in_the_future_is_included():
    """P0 不管什麼時候都得盯著。"""
    _add("面試", start=date(2026, 9, 20), priority="P0")

    assert _texts(personal.todos_due_today("U1", TODAY)) == ["面試"]


def test_non_p0_without_a_date_is_excluded():
    """沒日期又不重要的事不進信 —— 那會讓信回到「全部列出來」，
    跟使用者要的安靜相反。LINE 打「待辦」照樣看得到。"""
    _add("有空再說")

    assert personal.todos_due_today("U1", TODAY) == []


def test_end_date_is_the_deadline():
    """一段期間的待辦，截止日看 end 不看 start。
    9/01-9/10 的事在 9/05 還沒到期。"""
    _add("出差", start=date(2026, 9, 1), end=date(2026, 9, 10))

    assert personal.todos_due_today("U1", TODAY) == []


def test_range_ending_today_is_included():
    _add("出差", start=date(2026, 9, 1), end=TODAY)

    assert _texts(personal.todos_due_today("U1", TODAY)) == ["出差"]


def test_other_users_todos_are_not_mine():
    personal.add_todo("U2", "別人的事", start=TODAY)

    assert personal.todos_due_today("U1", TODAY) == []


# ── 排序 ──────────────────────────────────────────────────

def test_overdue_comes_first():
    _add("今天的", start=TODAY)
    _add("逾期的", start=date(2026, 9, 1))
    _add("P0沒日期", priority="P0")

    assert _texts(personal.todos_due_today("U1", TODAY)) == [
        "逾期的", "今天的", "P0沒日期"]


def test_more_overdue_comes_earlier():
    _add("逾期兩天", start=date(2026, 9, 3))
    _add("逾期四天", start=date(2026, 9, 1))

    assert _texts(personal.todos_due_today("U1", TODAY))[0] == "逾期四天"


# ── 排版 ──────────────────────────────────────────────────

def test_format_marks_how_overdue():
    _add("交資料", start=date(2026, 9, 2))

    out = personal.format_today_todos("U1", TODAY)

    assert "逾期 3 天" in out
    assert "交資料" in out


def test_format_marks_today():
    _add("繳健保費", start=TODAY)

    assert "今天" in personal.format_today_todos("U1", TODAY)


def test_format_shows_priority():
    _add("準備面試", priority="P0")

    assert "P0" in personal.format_today_todos("U1", TODAY)


def test_format_returns_none_when_empty():
    """空的區塊直接不放，不要留一張「今天沒待辦」的空卡片 ——
    跟其他每日信區塊同一套。"""
    assert personal.format_today_todos("U1", TODAY) is None


def test_format_does_not_leak_other_days():
    _add("下週的事", start=date(2026, 9, 12))

    assert personal.format_today_todos("U1", TODAY) is None


def test_format_todos_is_untouched():
    """LINE 打「待辦」的行為維持原樣 —— 那支要顯示全部。
    動它會弄壞指令查詢，而那個壞法在信上看不出來。"""
    _add("下週的事", start=date(2026, 9, 12))

    assert "下週的事" in personal.format_todos("U1")
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_today_todos.py -q
```

預期：`AttributeError: module 'personal' has no attribute 'todos_due_today'`

- [ ] **Step 3: 寫最小實作**

在 `personal.py` 的 `format_todos` 之後加：

```python
def _deadline(item):
    """待辦的截止日：end 有值就用 end，沒有就用 start。

    單日待辦（只講「明天交」）不需要被迫填兩個日期，所以 end 常常是
    None；一段期間的待辦則是 end 那天才到期。
    """
    return item.get("end") or item.get("start")


def todos_due_today(user_id, today):
    """每日信要顯示的待辦：**截止日 ≤ 今天 或 優先度 = P0**。

    兩個維度刻意不合併：截止日回答「什麼時候該做」，P0 回答「不管什麼
    時候都得盯著」。只用日期篩，沒設日期的重要事情會消失；只用優先度篩，
    時效性就沒了。

    排序：逾期最久的最前面 → 今天到期 → P0 且沒設日期。
    """
    out = []
    for t in list_todos(user_id):
        due = _deadline(t)
        if due and due <= today:
            out.append(t)
        elif t.get("priority") == "P0":
            out.append(t)

    def _key(t):
        due = _deadline(t)
        # 沒設日期的 P0 排最後：它沒有時效性，只是重要
        return (0, due) if due else (1, today)

    out.sort(key=_key)
    return out


def format_today_todos(user_id, today=None):
    """每日信的待辦區塊。沒東西回 None（呼叫端據此整塊不放）。

    刻意跟 format_todos 分開：那支給 LINE 的「待辦」指令用，要顯示全部。
    共用一支會讓「信裡安靜」與「查詢看得到」互相打架。
    """
    if today is None:
        today = now_tpe().date()

    items = todos_due_today(user_id, today)
    if not items:
        return None

    lines = []
    for t in items:
        due = _deadline(t)
        priority = t.get("priority")
        if due and due < today:
            mark = f"⚠️ 逾期 {(today - due).days} 天"
        elif due:
            mark = "今天"
        else:
            # 沒日期的一定是 P0（否則不會被 todos_due_today 選進來）
            mark = priority or ""
        suffix = f"（{mark}）" if mark else ""
        # 逾期 / 今天的行才另外標優先度；沒日期那行的 mark 已經是 P0 了
        if priority and due:
            suffix += f"　{priority}"
        lines.append(f"⬜ [{t['id']}] {t['text']}{suffix}")

    return "\n".join(lines)
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_today_todos.py -q
```

預期：`17 passed`

- [ ] **Step 5: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q
```

預期：`1245 passed`

- [ ] **Step 6: Commit**

commit message：

```
feat: todos_due_today + format_today_todos

篩選：截止日 ≤ 今天 OR P0。兩個維度不合併 —— 只用日期篩，
沒設日期的重要事情會消失；只用優先度篩，時效性就沒了。
format_todos 一個字都不動：LINE 指令要顯示全部，共用一支會讓
「信裡安靜」與「查詢看得到」互相打架。
```

要 add 的檔案：`personal.py tests/test_today_todos.py`

---

### Task 15: 每日信接上

**Files:**
- Modify: `daily_report.py`（`_personal_todos`，在 `_email_personal_report` 內）
- Modify: `docs/HANDOFF.md`
- Test: `tests/test_personal_report.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_personal_report.py`：

```python
# ── 每日信只放今日待辦（2026-09-06）──────────────────────

def test_daily_email_uses_the_filtered_todo_list():
    """信裡那張卡標題是「📋 今日待辦」。在此之前它顯示的是**全部**
    未完成待辦 —— 「今日」兩個字是假的。"""
    import inspect

    src = inspect.getsource(daily_report._email_personal_report)

    assert "format_today_todos" in src
    assert "personal.format_todos(" not in src
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_personal_report.py -k filtered_todo -q
```

預期：`assert 'format_today_todos' in src` FAIL

- [ ] **Step 3: 寫最小實作**

`daily_report.py` 的 `_personal_todos` 改成：

```python
    def _personal_todos():
        user_id = _personal_user_id()
        if not user_id:
            return None          # 沒設就跳過這區塊，跟 mailer 的 gate 同一套
        import personal
        # 只放「截止日 ≤ 今天 或 P0」——在此之前這張卡顯示的是全部
        # 未完成待辦，標題的「今日」兩個字是假的。
        # LINE 打「待辦」仍然看得到全部（personal.format_todos）。
        return personal.format_today_todos(user_id, today_tpe())
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_personal_report.py -q
```

預期：`26 passed`

- [ ] **Step 5: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q
```

預期：`1246 passed`

- [ ] **Step 6: 更新 HANDOFF**

在 `docs/HANDOFF.md` 第 3 節「真實環境驗證狀態」的表格末尾加三列：

```markdown
| 待辦起訖日 + 優先度 | ❌ **沒在真實 Notion 建過欄位**。由 `_ensure_properties` 開機自動補 |
| 待辦對話式新增（➕ → 請說）| ❌ **沒在 LINE 上按過**。待命狀態只活在記憶體，Railway 重啟會清掉 |
| 每日信只放今日待辦 | ❌ 沒有真的收過信。**現有待辦兩欄都是空的，所以上線後這張卡會是空的**，直到補日期或標 P0 |
```

- [ ] **Step 7: Commit**

commit message：

```
feat: 每日信只放今日待辦

在此之前那張卡標題寫「今日待辦」，內容卻是全部未完成待辦 ——
「今日」兩個字是假的。LINE 打「待辦」仍然看得到全部。
```

要 add 的檔案：`daily_report.py docs/HANDOFF.md tests/test_personal_report.py`

---

## 收尾

- [ ] **跑全套 + 檢查換行字元**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q && python -c "
import io, glob
files = ['todo_parse.py','personal.py','notion_db.py','command_router.py','flex_builder.py','daily_report.py','prompts.py']
files += glob.glob('tests/test_todo*.py') + glob.glob('tests/test_pending*.py') + ['tests/test_today_todos.py']
for p in files:
    b = io.open(p,'rb').read()
    bare = b.count(b'\n') - b.count(b'\r\n')
    print(('OK    ' if bare==0 else 'MIXED '), p, 'bare LF:', bare)
"
```

預期：`1246 passed`，所有檔案 `bare LF: 0`

- [ ] **合併回 main 並推上去**

使用 `superpowers:finishing-a-development-branch`。推上去會觸發 Railway 部署
（約 60-90 秒）。

---

## 上線之後使用者要做的事

1. **不用建表。** `期間` 與 `優先度` 由 `_ensure_properties` 開機時自動補上，
   現有待辦一筆都不會動到。
2. **現有待辦要補資料才會回到每日信裡。** 去 Notion 補截止日，或標 P0。
   不補的話它們仍然在，LINE 打「待辦」看得到，只是不進信。
3. **驗證步驟：**
   - LINE 打「待辦」→ 卡片底部應該有一顆 **➕ 加一件事**
   - 按 ➕ → 機器人回「請說。」
   - 打 `P0 下週一交社宅資料` → 回「✅ 已記下：交社宅資料 / 📅 9/xx（一）　P0」（日期是下週一）
   - 打 `隨便一件事`（不講日期）→ 回「已記下」+ 一排日期按鈕
   - 按 ➕ 之後改按「快過期」→ 回「已取消新增待辦。」+ 快過期的結果
   - 觸發一次每日信（`/admin/run-personal`）→ 待辦卡只有今天到期的與 P0

---

## 刻意不做（YAGNI，見規格第 6 節）

| 不做 | 理由 |
|---|---|
| 重複待辦（每週一交報告）| 使用者沒提；會把 schema 與篩選邏輯撐複雜一個量級 |
| 待辦到期推播提醒 | 已經有 `Reminders` 那一套，兩個系統混在一起會互相干擾 |
| 改 Rich Menu 加「加待辦」格 | 要重產選單圖 + 重跑 `setup-richmenu`，清單卡片上那顆 ➕ 已經夠用 |
| 自然語言優先度（「很急」）| 主觀詞猜錯會毀掉信任，按鈕點一下就解決 |
| 待辦編輯（改內容 / 改日期）| 去 Notion 改。做成 LINE 對話流程要一整套狀態機 |
| 「下下週」以外的複雜相對詞（「月底前」）| 規則不接，交給 AI 補位那條路 |
