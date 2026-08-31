# 每日個人報 v2:語句庫 + 金句庫 + 消費圓餅圖 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每日個人信新增「今日三句」(英/西走間隔重複、中文金句走隨機不重複),並把「本月消費明細」的整月流水帳換成圓餅圖 + 近三天明細。

**Architecture:** 語句庫與金句庫是 Notion 資料庫,由 `notion_db.py` 既有的 schema-as-code 機制自動建立。排程決策放在純邏輯模組 `phrasebook.py`(不碰 Notion、不碰 AI,測試零 mock);I/O 由 `notion_db.py` 負責;AI 補位沿用 `humor.py` 的 `_ai()` 模式。圓餅圖由新模組 `spending_chart.py` 產 PNG,`mailer.py` 改成 `multipart/related` 內嵌,`digest.py` 擴充成支援圖片卡片。

**Tech Stack:** Python 3、pytest、notion-client、matplotlib(Agg)、anthropic、Gmail API(google-api-python-client)

**Spec:** `docs/superpowers/specs/2026-09-01-phrasebook-and-spending-chart-design.md`

**Branch:** `feat/phrasebook-and-spending-chart`(spec 已 commit 於此)

**跑測試:** 一律在 repo 根目錄執行 `python -m pytest ...`。這個 repo 沒有 pytest.ini,靠 `conftest.py` 把根目錄放進 import path。

---

## 檔案結構

| 檔案 | 責任 | 動作 |
|---|---|---|
| `phrasebook.py` | 間隔重複的排程決策 + 三句組版。純邏輯,不做 I/O | 新增 |
| `spending_chart.py` | 消費類別彙總 + 圓餅圖 PNG | 新增 |
| `notion_db.py` | 語句庫/金句庫的 schema 與讀寫 | 修改 |
| `prompts.py` | AI 造句的 prompt 模板 | 修改 |
| `mailer.py` | 內嵌圖片(multipart/related)+ 寄信重試 | 修改 |
| `digest.py` | 圖片卡片(三元組 blocks) | 修改 |
| `finance_report.py` | `format_recent_days`(有資料的最近三天) | 修改 |
| `daily_report.py` | 把上述接進每日信,區塊順序與標題 | 修改 |

**為什麼 `phrasebook.py` 不併進 `humor.py`:** `humor.py` 的內容是拋棄式的(生完推播、只留歷史防重複),語句庫的內容是**資產**(要留著反覆出現)。兩者生命週期相反,合檔會讓「什麼時候該刪」變模糊。

**為什麼 `spending_chart.py` 不併進 `finance_report.py`:** `finance_report.py` 已經 800+ 行且全是純文字格式化。加進 matplotlib 會讓 `import finance_report` 從此拖著繪圖函式庫 —— 而 `command_router` 每次處理 LINE 指令都會 import 它。相依方向必須是 `spending_chart → finance_report`,不能反過來。

---

# Segment 1:語句庫 / 金句庫的骨架

做完這一段,Notion 會長出兩張空表,使用者當天就能開始貼東西。信件還不會有變化。

## Task 1: `phrasebook.next_due` — 間隔表

**Files:**
- Create: `phrasebook.py`
- Test: `tests/test_phrasebook.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_phrasebook.py`:

```python
"""每日三句的排程決策。

英/西走固定間隔重複,中文金句走隨機不重複 —— 兩種節奏不同的理由見
docs/superpowers/specs/2026-09-01-phrasebook-and-spending-chart-design.md 2.3。

這個模組不碰 Notion 也不碰 AI,所以整份測試沒有任何 mock。
"""

from datetime import date

import phrasebook


D = date(2026, 9, 1)


# ── 間隔表 ────────────────────────────────────────────────

def test_intervals_follow_forgetting_curve():
    """使用者要的是「隔一個月、三個月再重來」,對應第 3、第 4 級。"""
    assert phrasebook.INTERVALS == (1, 7, 30, 90, 180)


def test_next_due_first_appearance_is_tomorrow():
    assert phrasebook.next_due(1, D) == date(2026, 9, 2)


def test_next_due_climbs_through_the_table():
    assert phrasebook.next_due(2, D) == date(2026, 9, 8)     # +7
    assert phrasebook.next_due(3, D) == date(2026, 10, 1)    # +30
    assert phrasebook.next_due(4, D) == date(2026, 11, 30)   # +90


def test_next_due_caps_at_last_interval():
    """背過的東西還是會忘,只是慢一點 —— 封頂而不是停止出現。"""
    assert phrasebook.next_due(5, D) == date(2027, 2, 28)    # +180
    assert phrasebook.next_due(99, D) == date(2027, 2, 28)


def test_next_due_treats_zero_as_first():
    """防呆:Notion 的「出現次數」沒填時讀回來是 0。"""
    assert phrasebook.next_due(0, D) == date(2026, 9, 2)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_phrasebook.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'phrasebook'`

- [ ] **Step 3: 寫最小實作**

建立 `phrasebook.py`:

```python
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_phrasebook.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add phrasebook.py tests/test_phrasebook.py
git commit -m "feat(phrasebook): 間隔重複的間隔表 next_due"
```

---

## Task 2: `phrasebook.pick_due` / `advance` — 挑一句並推進排程

**Files:**
- Modify: `phrasebook.py`
- Test: `tests/test_phrasebook.py`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_phrasebook.py` 末端**追加**:

```python
# ── 挑句 ──────────────────────────────────────────────────

def _row(page_id, sentence, due=None, appeared=0):
    return {
        "page_id": page_id, "sentence": sentence,
        "meaning": "", "note": "", "appeared": appeared, "due": due,
    }


def test_pick_due_returns_none_when_nothing_is_due():
    rows = [_row("a", "hello", due="2026-09-05")]

    assert phrasebook.pick_due(rows, D) is None


def test_pick_due_returns_none_for_empty_library():
    assert phrasebook.pick_due([], D) is None


def test_pick_due_takes_the_most_overdue_first():
    """逾期最久的先還債。"""
    rows = [
        _row("a", "newer", due="2026-08-31"),
        _row("b", "older", due="2026-08-01"),
    ]

    assert phrasebook.pick_due(rows, D)["page_id"] == "b"


def test_pick_due_prefers_freshly_pasted_rows():
    """使用者剛貼進 Notion 的句子「下次出現」是空的,當天就該上場。

    不這樣做的話,貼完還得手動去填一個日期欄位 —— 那張表就變成家事。
    """
    rows = [
        _row("old", "overdue", due="2026-01-01"),
        _row("new", "just pasted", due=None),
    ]

    assert phrasebook.pick_due(rows, D)["page_id"] == "new"


def test_pick_due_includes_today():
    """due 正好是今天要算到期,不是明天。"""
    rows = [_row("a", "hello", due="2026-09-01")]

    assert phrasebook.pick_due(rows, D)["page_id"] == "a"


# ── 推進排程 ──────────────────────────────────────────────

def test_advance_increments_and_reschedules():
    row = _row("a", "hello", due="2026-08-01", appeared=2)

    out = phrasebook.advance(row, D)

    assert out == {
        "appeared": 3,
        "last_seen": D,
        "due": date(2026, 10, 1),      # 第 3 次 → +30
    }


def test_advance_handles_missing_count():
    """Notion 沒填「出現次數」時讀回來是 None。"""
    row = {"page_id": "a", "sentence": "hi", "appeared": None, "due": None}

    out = phrasebook.advance(row, D)

    assert out["appeared"] == 1
    assert out["due"] == date(2026, 9, 2)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_phrasebook.py -v`
Expected: FAIL —— `AttributeError: module 'phrasebook' has no attribute 'pick_due'`

- [ ] **Step 3: 寫最小實作**

在 `phrasebook.py` 的 `next_due` 之後追加:

```python
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
    due = [r for r in rows if not r.get("due") or r["due"] <= today_iso]
    if not due:
        return None
    return min(due, key=lambda r: r.get("due") or "")


def advance(row, today):
    """挑中一句之後,要寫回 Notion 的欄位。

    回 dict 而不是直接寫 Notion:這個模組不做 I/O,而且這樣測試
    看得到「算出來的排程」而不是「有沒有呼叫 API」。
    """
    appeared = (row.get("appeared") or 0) + 1
    return {
        "appeared": appeared,
        "last_seen": today,
        "due": next_due(appeared, today),
    }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_phrasebook.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add phrasebook.py tests/test_phrasebook.py
git commit -m "feat(phrasebook): pick_due 挑句與 advance 推進排程"
```

---

## Task 3: `phrasebook.pick_quote` / `format_daily` — 金句挑選與三句組版

**Files:**
- Modify: `phrasebook.py`
- Test: `tests/test_phrasebook.py`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_phrasebook.py` 末端**追加**:

```python
# ── 金句 ──────────────────────────────────────────────────

def _quote(page_id, text, last_seen=None):
    return {"page_id": page_id, "sentence": text,
            "source": "", "last_seen": last_seen}


def test_pick_quote_returns_none_for_empty_library():
    assert phrasebook.pick_quote([], D) is None


def test_pick_quote_prefers_unseen():
    rows = [
        _quote("seen", "講過了", last_seen="2026-08-01"),
        _quote("fresh", "沒講過"),
    ]

    assert phrasebook.pick_quote(rows, D)["page_id"] == "fresh"


def test_pick_quote_falls_back_to_oldest_when_all_seen():
    """金句庫用完不該讓區塊消失 —— 輪回去重講是可接受的。

    語句庫不能這樣做(那邊有 AI 補位),但金句是拿來被提醒的,
    重看一次不算浪費。硬生的「名言」才是假的。
    """
    rows = [
        _quote("a", "第一句", last_seen="2026-08-20"),
        _quote("b", "第二句", last_seen="2026-01-05"),
    ]

    assert phrasebook.pick_quote(rows, D)["page_id"] == "b"


def test_pick_quote_picks_among_unseen_not_always_first():
    """沒講過的有很多句時要隨機,不能每次都拿 Notion 順序的第一句。"""
    rows = [_quote(str(i), f"句{i}") for i in range(20)]

    picked = {phrasebook.pick_quote(rows, D)["page_id"] for _ in range(30)}

    assert len(picked) > 1


# ── 組版 ──────────────────────────────────────────────────

def test_format_daily_renders_three_languages():
    text = phrasebook.format_daily(
        en={"sentence": "Play it by ear.",
            "meaning": "再看情況決定吧", "note": "口語很常用"},
        es={"sentence": "Me da igual.",
            "meaning": "我都可以", "note": "比 no me importa 更輕鬆"},
        quote={"sentence": "你以為的極限,只是別人的起點。", "source": "佚名"},
    )

    assert "[EN] Play it by ear." in text
    assert "再看情況決定吧" in text
    assert "💡 口語很常用" in text
    assert "[ES] Me da igual." in text
    assert "[中] 你以為的極限,只是別人的起點。" in text
    assert "—— 佚名" in text


def test_format_daily_keeps_language_order():
    text = phrasebook.format_daily(
        en={"sentence": "A"}, es={"sentence": "B"}, quote={"sentence": "C"},
    )

    assert text.index("[EN]") < text.index("[ES]") < text.index("[中]")


def test_format_daily_skips_missing_languages():
    """某一語言撈不到也生不出來時,少那一行,不是整段消失。"""
    text = phrasebook.format_daily(en={"sentence": "A"}, es=None, quote=None)

    assert "[EN] A" in text
    assert "[ES]" not in text
    assert "[中]" not in text


def test_format_daily_omits_blank_meaning_and_note():
    """沒填中文意思時不要留一行空白。"""
    text = phrasebook.format_daily(en={"sentence": "A", "meaning": "", "note": ""})

    assert text == "[EN] A"


def test_format_daily_returns_none_when_nothing_available():
    """三個都沒有 → 呼叫端據此整個區塊不放,不要留一張空卡片。"""
    assert phrasebook.format_daily(None, None, None) is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_phrasebook.py -v`
Expected: FAIL —— `AttributeError: module 'phrasebook' has no attribute 'pick_quote'`

- [ ] **Step 3: 寫最小實作**

在 `phrasebook.py` 的 `advance` 之後追加:

```python
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_phrasebook.py -v`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add phrasebook.py tests/test_phrasebook.py
git commit -m "feat(phrasebook): 金句挑選與今日三句組版"
```

---

## Task 4: Notion schema — 語句庫 / 金句庫兩張表

**Files:**
- Modify: `notion_db.py`(`_SECTIONS` 約 37-47 行、`_SCHEMAS` 約 132 行起)
- Test: `tests/test_notion_schema.py`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_notion_schema.py` 末端**追加**:

```python
# ── 語言學習區塊(2026-09-01)────────────────────────────

def test_language_section_holds_both_dbs():
    assert notion_db._SECTIONS["語言學習"]["dbs"] == ("語句庫", "金句庫")
    assert notion_db._SECTIONS["語言學習"]["icon"] == "📚"


def test_language_dbs_are_routed_to_the_section():
    """_DB_SECTION 是從 _SECTIONS 推導的 —— 漏掉會建到根頁去。"""
    assert notion_db._DB_SECTION["語句庫"] == "語言學習"
    assert notion_db._DB_SECTION["金句庫"] == "語言學習"


def test_phrase_schema_has_scheduling_fields():
    """少任何一個排程欄位,間隔重複就會安靜地退化成隨機出現。"""
    schema = notion_db._SCHEMAS["語句庫"]

    assert "title" in schema["句子"]
    assert schema["出現次數"] == {"number": {"format": "number"}}
    assert schema["上次出現"] == {"date": {}}
    assert schema["下次出現"] == {"date": {}}


def test_phrase_schema_covers_both_languages():
    options = [o["name"] for o in schema_options(notion_db._SCHEMAS["語句庫"], "語言")]

    assert options == ["英文", "西班牙文"]


def test_phrase_schema_marks_ai_generated_rows():
    """AI 補位生的句子要跟老師整理的分得出來,否則無從判斷庫的品質。"""
    options = [o["name"] for o in schema_options(notion_db._SCHEMAS["語句庫"], "來源")]

    assert "AI生成" in options
    assert "Preply課堂" in options


def test_quote_schema_tracks_last_seen_only():
    """金句走隨機不重複,只需要「講過沒」,不需要排程欄位。"""
    schema = notion_db._SCHEMAS["金句庫"]

    assert "title" in schema["金句"]
    assert schema["上次出現"] == {"date": {}}
    assert "下次出現" not in schema
    assert "出現次數" not in schema
```

同時在該檔的 import 區之後加一個小工具(放在檔案上方、`class FakeDatabases` 之前):

```python
def schema_options(schema, field):
    """取某個 select 欄位的選項清單。"""
    return schema[field]["select"]["options"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_notion_schema.py -v -k "language or phrase or quote"`
Expected: FAIL —— `KeyError: '語言學習'`

- [ ] **Step 3: 寫最小實作**

在 `notion_db.py` 的 `_SECTIONS` 加第三個區塊(接在 `煮飯模板` 之後):

```python
    "語言學習": {
        "icon": "📚",
        "dbs": ("語句庫", "金句庫"),
    },
```

在 `_SCHEMAS` 的「今日一則」之後、「── 財務中心 ──」註解之前插入:

```python
    # ── 語言學習(2026-09-01)──────────────────────────
    #
    # Preply 沒有公開 API(spec 第 1 節查證結果),所以老師整理的句子
    # 由使用者直接貼進這張表。程式只負責排程與出題。
    #
    # 「下次出現」為空 = 今天就該出現。使用者貼完不必再填任何欄位,
    # 否則這張表就變成一件家事(phrasebook.pick_due 吃這條規則)。
    "語句庫": {
        "句子": {"title": {}},
        "語言": _select(("英文", "blue"), ("西班牙文", "orange")),
        "中文意思": {"rich_text": {}},
        "情境備註": {"rich_text": {}},          # 老師的補充、用法陷阱
        "來源": _select(("Preply課堂", "green"), ("自己整理", "gray"),
                        ("AI生成", "purple")),
        "加入日期": {"date": {}},
        "出現次數": {"number": {"format": "number"}},
        "上次出現": {"date": {}},
        "下次出現": {"date": {}},
    },

    # 中文金句。刻意跟語句庫分開:出現規則不同(隨機不重複 vs 間隔
    # 重複),合成一張表要多一個「模式」欄位,而那欄的值永遠等於語言 ——
    # 兩張表反而少一個可以填錯的地方(spec 2.2)。
    "金句庫": {
        "金句": {"title": {}},
        "出處": {"rich_text": {}},
        "加入日期": {"date": {}},
        "上次出現": {"date": {}},               # 有值代表講過了
    },
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_notion_schema.py -v`
Expected: 全部 passed(既有測試 + 6 個新的)

- [ ] **Step 5: 確認沒弄壞別的**

Run: `python -m pytest tests/ -q`
Expected: 全部 passed

- [ ] **Step 6: Commit**

```bash
git add notion_db.py tests/test_notion_schema.py
git commit -m "feat(notion): 語言學習區塊 — 語句庫與金句庫 schema"
```

---

## Task 5: Notion 讀寫 — 撈句子、推進排程、寫回 AI 生成的句子

**Files:**
- Modify: `notion_db.py`(新函式加在 `daily_extra_add` 之後、`# ── Todos` 註解之前)
- Test: `tests/test_phrase_store.py`(新檔)

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_phrase_store.py`:

```python
"""語句庫 / 金句庫的 Notion 讀寫。

排程決策在 phrasebook.py(純邏輯,另一份測試),這裡只驗 I/O:
撈回來的欄位對不對、寫回去的 payload 對不對、Notion 掛掉會不會炸。

假 client 的形狀沿用 tests/test_notion_reads.py 的做法。
"""

from datetime import date

import notion_db


D = date(2026, 9, 1)


class RecordingDatabases:
    def __init__(self, pages):
        self._pages = pages
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"results": self._pages, "has_more": False, "next_cursor": None}


class RecordingPages:
    def __init__(self):
        self.created = []
        self.updated = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "new_page"}

    def update(self, **kwargs):
        self.updated.append(kwargs)
        return {"id": kwargs.get("page_id")}


class FakeClient:
    def __init__(self, databases, pages):
        self.databases = databases
        self.pages = pages


def _install(monkeypatch, pages_rows, db_id="db_fake"):
    dbs = RecordingDatabases(pages_rows)
    pages = RecordingPages()
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", "page_fake")
    monkeypatch.setattr(notion_db, "_client", FakeClient(dbs, pages))
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: db_id)
    return dbs, pages


def _phrase_page(page_id, sentence, meaning="", note="",
                 appeared=None, due=None):
    return {
        "id": page_id,
        "properties": {
            "句子": {"title": [{"plain_text": sentence}]},
            "中文意思": {"rich_text": [{"plain_text": meaning}]},
            "情境備註": {"rich_text": [{"plain_text": note}]},
            "出現次數": {"number": appeared},
            "下次出現": {"date": {"start": due} if due else None},
        },
    }


def _quote_page(page_id, text, source="", last_seen=None):
    return {
        "id": page_id,
        "properties": {
            "金句": {"title": [{"plain_text": text}]},
            "出處": {"rich_text": [{"plain_text": source}]},
            "上次出現": {"date": {"start": last_seen} if last_seen else None},
        },
    }


# ── 撈句子 ────────────────────────────────────────────────

def test_phrases_load_filters_by_language(monkeypatch):
    """撈英文時不能把西班牙文一起撈回來 —— 兩個語言各挑各的。"""
    dbs, _ = _install(monkeypatch, [_phrase_page("a", "hello")])

    notion_db.phrases_load("英文")

    assert dbs.queries[0]["filter"] == {
        "property": "語言", "select": {"equals": "英文"},
    }


def test_phrases_load_maps_fields(monkeypatch):
    _install(monkeypatch, [
        _phrase_page("p1", "Play it by ear.", meaning="再看情況決定吧",
                     note="口語常用", appeared=2, due="2026-08-01"),
    ])

    out = notion_db.phrases_load("英文")

    assert out == [{
        "page_id": "p1",
        "sentence": "Play it by ear.",
        "meaning": "再看情況決定吧",
        "note": "口語常用",
        "appeared": 2,
        "due": "2026-08-01",
    }]


def test_phrases_load_defaults_missing_count_to_zero(monkeypatch):
    """使用者手貼的句子不會填「出現次數」,讀回來必須是 0 不是 None。

    None 會讓 phrasebook.advance 的 +1 變成 TypeError。
    """
    _install(monkeypatch, [_phrase_page("p1", "hi")])

    assert notion_db.phrases_load("英文")[0]["appeared"] == 0
    assert notion_db.phrases_load("英文")[0]["due"] is None


def test_phrases_load_survives_notion_failure(monkeypatch):
    """Notion 掛掉回空清單,不 raise —— 上層據此走 AI 補位。"""
    dbs, _ = _install(monkeypatch, [])

    def boom(**kwargs):
        raise RuntimeError("notion down")

    dbs.query = boom

    assert notion_db.phrases_load("英文") == []


def test_phrases_load_returns_empty_when_not_configured(monkeypatch):
    monkeypatch.setattr(notion_db, "_TOKEN", "")
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", "")

    assert notion_db.phrases_load("英文") == []


# ── 推進排程 ──────────────────────────────────────────────

def test_phrase_advance_writes_all_three_fields(monkeypatch):
    _, pages = _install(monkeypatch, [])

    ok = notion_db.phrase_advance("p1", {
        "appeared": 3, "last_seen": D, "due": date(2026, 10, 1),
    })

    assert ok is True
    assert pages.updated[0]["page_id"] == "p1"
    assert pages.updated[0]["properties"] == {
        "出現次數": {"number": 3},
        "上次出現": {"date": {"start": "2026-09-01"}},
        "下次出現": {"date": {"start": "2026-10-01"}},
    }


def test_phrase_advance_survives_notion_failure(monkeypatch):
    """寫不回去只是排程沒推進,信已經寄了 —— 不能因此炸掉整封。"""
    _, pages = _install(monkeypatch, [])

    def boom(**kwargs):
        raise RuntimeError("notion down")

    pages.update = boom

    assert notion_db.phrase_advance("p1", {
        "appeared": 1, "last_seen": D, "due": D,
    }) is False


# ── 寫回 AI 生成的句子 ────────────────────────────────────

def test_phrase_add_marks_source_and_schedules_next(monkeypatch):
    """AI 生的要進複習循環,否則生完就丟,庫永遠長不大(spec 4.3)。"""
    _, pages = _install(monkeypatch, [])

    ok = notion_db.phrase_add(
        "Me da igual.", "西班牙文",
        meaning="我都可以", note="口語",
        source="AI生成", day=D, due=date(2026, 9, 2),
    )

    props = pages.created[0]["properties"]
    assert ok is True
    assert props["句子"]["title"][0]["text"]["content"] == "Me da igual."
    assert props["語言"]["select"]["name"] == "西班牙文"
    assert props["來源"]["select"]["name"] == "AI生成"
    assert props["出現次數"]["number"] == 1
    assert props["上次出現"]["date"]["start"] == "2026-09-01"
    assert props["下次出現"]["date"]["start"] == "2026-09-02"


def test_phrase_add_refuses_empty_sentence(monkeypatch):
    """AI 回空字串時不要在庫裡留一筆空白。"""
    _, pages = _install(monkeypatch, [])

    assert notion_db.phrase_add("", "英文", day=D, due=D) is False
    assert pages.created == []


# ── 金句 ──────────────────────────────────────────────────

def test_quotes_load_maps_fields(monkeypatch):
    _install(monkeypatch, [
        _quote_page("q1", "你以為的極限", source="佚名", last_seen="2026-08-01"),
    ])

    assert notion_db.quotes_load() == [{
        "page_id": "q1",
        "sentence": "你以為的極限",
        "source": "佚名",
        "last_seen": "2026-08-01",
    }]


def test_quotes_load_keeps_unseen_as_none(monkeypatch):
    """沒講過的 last_seen 必須是 None —— phrasebook.pick_quote 靠它分類。"""
    _install(monkeypatch, [_quote_page("q1", "沒講過")])

    assert notion_db.quotes_load()[0]["last_seen"] is None


def test_quote_mark_seen_writes_date(monkeypatch):
    _, pages = _install(monkeypatch, [])

    ok = notion_db.quote_mark_seen("q1", D)

    assert ok is True
    assert pages.updated[0]["properties"] == {
        "上次出現": {"date": {"start": "2026-09-01"}},
    }
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_phrase_store.py -v`
Expected: FAIL —— `AttributeError: module 'notion_db' has no attribute 'phrases_load'`

- [ ] **Step 3: 寫最小實作**

在 `notion_db.py` 的 `daily_extra_add` 之後、`# ── Todos` 分隔線之前插入:

```python
# ─────────────────────────────────────────────────────────
# 語句庫 / 金句庫(2026-09-01)
# ─────────────────────────────────────────────────────────

def _query_all(db_id, client, limit, **extra):
    """分頁撈到 limit 筆。Notion 單頁上限 100,不分頁會安靜地少拿。

    transactions_load 的註解已經記過這個坑:只查一次就回,limit 傳 200
    也只拿得到 100 筆,而且不會報錯。
    """
    out, cursor = [], None
    while len(out) < limit:
        kwargs = dict(extra)
        kwargs["database_id"] = db_id
        kwargs["page_size"] = min(limit - len(out), 100)
        if cursor:
            kwargs["start_cursor"] = cursor
        res = client.databases.query(**kwargs)
        out.extend(res.get("results", []))
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    return out


def phrases_load(language, limit=500):
    """撈某語言的全部句子。到期判斷交給 phrasebook.pick_due。

    刻意不在 Notion 端 filter 到期日:「下次出現為空」要寫成 is_empty
    的 or 分支,那個分支寫錯不會報錯 —— 只會讓使用者剛貼的句子永遠
    不出現。語句庫是幾百筆等級,全撈一次再在 Python 判斷更安全。
    """
    db_id = get_or_create_db("語句庫")
    client = _get_client()
    if not db_id or not client:
        return []
    out = []
    try:
        rows = _query_all(
            db_id, client, limit,
            filter={"property": "語言", "select": {"equals": language}},
        )
        for r in rows:
            props = r.get("properties", {}) or {}
            out.append({
                "page_id": r.get("id"),
                "sentence": _read_title(props, "句子"),
                "meaning": _read_rich_text(props, "中文意思"),
                "note": _read_rich_text(props, "情境備註"),
                # 手貼的句子不會填這欄。None 會讓 advance 的 +1 變 TypeError
                "appeared": _read_number(props, "出現次數") or 0,
                "due": _read_date(props, "下次出現"),
            })
    except Exception as e:
        print(f"[notion] phrases_load 失敗：{e}")
    return out


def phrase_advance(page_id, fields):
    """寫回出現次數 / 上次出現 / 下次出現。fields 來自 phrasebook.advance()。

    失敗只回 False:信這時已經寄出去了,排程沒推進頂多明天同一句再出現
    一次,不值得把整個每日 job 拉進 error listener。
    """
    client = _get_client()
    if not client or not page_id:
        return False
    try:
        client.pages.update(page_id=page_id, properties={
            "出現次數": {"number": fields["appeared"]},
            "上次出現": {"date": {"start": fields["last_seen"].isoformat()}},
            "下次出現": {"date": {"start": fields["due"].isoformat()}},
        })
        return True
    except Exception as e:
        print(f"[notion] phrase_advance 失敗：{e}")
        return False


def phrase_add(sentence, language, meaning="", note="",
               source="AI生成", day=None, due=None):
    """新增一句到語句庫。AI 補位生的句子走這裡。

    due 由呼叫端算好再傳進來,不在這裡 import phrasebook ——
    notion_db 是底層,反過來相依會變成循環。
    """
    db_id = get_or_create_db("語句庫")
    client = _get_client()
    if not db_id or not client or not sentence:
        return False
    day = day or datetime.now().date()
    due = due or day
    try:
        client.pages.create(parent={"database_id": db_id}, properties={
            "句子": {"title": [{"text": {"content": sentence}}]},
            "語言": {"select": {"name": language}},
            "中文意思": {"rich_text": [{"text": {"content": meaning or ""}}]},
            "情境備註": {"rich_text": [{"text": {"content": note or ""}}]},
            "來源": {"select": {"name": source}},
            "加入日期": {"date": {"start": day.isoformat()}},
            # 生出來當天就用掉了,所以是 1 不是 0
            "出現次數": {"number": 1},
            "上次出現": {"date": {"start": day.isoformat()}},
            "下次出現": {"date": {"start": due.isoformat()}},
        })
        return True
    except Exception as e:
        print(f"[notion] phrase_add 失敗：{e}")
        return False


def quotes_load(limit=500):
    """撈全部中文金句。挑選交給 phrasebook.pick_quote。"""
    db_id = get_or_create_db("金句庫")
    client = _get_client()
    if not db_id or not client:
        return []
    out = []
    try:
        for r in _query_all(db_id, client, limit):
            props = r.get("properties", {}) or {}
            out.append({
                "page_id": r.get("id"),
                "sentence": _read_title(props, "金句"),
                "source": _read_rich_text(props, "出處"),
                # 沒講過必須是 None（不是 ""）—— pick_quote 靠它分類
                "last_seen": _read_date(props, "上次出現"),
            })
    except Exception as e:
        print(f"[notion] quotes_load 失敗：{e}")
    return out


def quote_mark_seen(page_id, today):
    """標記這句金句今天講過了。"""
    client = _get_client()
    if not client or not page_id:
        return False
    try:
        client.pages.update(page_id=page_id, properties={
            "上次出現": {"date": {"start": today.isoformat()}},
        })
        return True
    except Exception as e:
        print(f"[notion] quote_mark_seen 失敗：{e}")
        return False
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_phrase_store.py -v`
Expected: 13 passed

- [ ] **Step 5: 確認沒弄壞別的**

Run: `python -m pytest tests/ -q`
Expected: 全部 passed

- [ ] **Step 6: Commit**

```bash
git add notion_db.py tests/test_phrase_store.py
git commit -m "feat(notion): 語句庫與金句庫的讀寫"
```

> **Segment 1 完成。** 部署後 `ensure_all_dbs()` 會在 Notion 建出「📚 語言學習」區塊與兩張表。使用者可以開始貼句子、匯入金句。信件尚未變化。

---

# Segment 2:信件加「今日三句」+ AI 補位

## Task 6: AI 造句的 prompt

**Files:**
- Modify: `prompts.py`(檔案末端追加)
- Test: `tests/test_phrase_ai.py`(新檔)

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_phrase_ai.py`:

```python
"""AI 補位:語句庫沒有到期的句子時,現生一句。

「現生」不等於「拋棄式」—— 生出來的句子會寫回語句庫並進入複習循環,
否則使用者不貼檔的日子庫永遠長不大(spec 4.3)。
"""

import prompts


def test_prompt_takes_language_and_avoid_block():
    text = prompts.DAILY_PHRASE_PROMPT.format(
        language="西班牙文", avoid_block="",
    )

    assert "西班牙文" in text


def test_prompt_asks_for_three_labelled_lines():
    """解析靠這三個標籤,prompt 改掉標籤就會安靜地解析失敗。"""
    text = prompts.DAILY_PHRASE_PROMPT.format(language="英文", avoid_block="")

    assert "句子：" in text
    assert "意思：" in text
    assert "提示：" in text


def test_avoid_block_is_injected():
    text = prompts.DAILY_PHRASE_PROMPT.format(
        language="英文", avoid_block="- Play it by ear.",
    )

    assert "Play it by ear." in text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_phrase_ai.py -v`
Expected: FAIL —— `AttributeError: module 'prompts' has no attribute 'DAILY_PHRASE_PROMPT'`

- [ ] **Step 3: 寫最小實作**

在 `prompts.py` 末端追加:

```python
# 語句庫沒有到期句子時的補位。刻意要求固定三行標籤:
# 自由格式的回覆解析起來要寫正則猜結構,而猜錯不會報錯,只會在信裡
# 出現半句話。
DAILY_PHRASE_PROMPT = """給我一句「{language}」的實用生活句子。

要求:
- 日常對話真的會用到,不是教科書例句
- 3 到 10 個單字
- 附繁體中文意思,以及一句情境或用法陷阱的提示(20 字內)
{avoid_block}
只輸出以下三行,不要任何其他文字、不要編號、不要引號:
句子：<{language}原句>
意思：<繁體中文>
提示：<情境或陷阱>
"""

AVOID_PHRASE_BLOCK = """
下面這些已經在庫裡了,換一句完全不同的（不同情境、不同句型,
不要只是換個單字）:
{recent}
"""
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_phrase_ai.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add prompts.py tests/test_phrase_ai.py
git commit -m "feat(prompts): AI 補位造句的 prompt 模板"
```

---

## Task 7: `phrasebook.parse_ai` + `daily_three` — 串起 Notion 與 AI

**Files:**
- Modify: `phrasebook.py`
- Test: `tests/test_phrase_ai.py`

- [ ] **Step 1: 寫失敗的測試(解析)**

在 `tests/test_phrase_ai.py` 末端**追加**:

```python
from datetime import date

import phrasebook


D = date(2026, 9, 1)


# ── 解析 AI 回覆 ──────────────────────────────────────────

def test_parse_ai_reads_three_lines():
    out = phrasebook.parse_ai(
        "句子：Play it by ear.\n意思：再看情況決定吧\n提示：口語很常用"
    )

    assert out == {
        "sentence": "Play it by ear.",
        "meaning": "再看情況決定吧",
        "note": "口語很常用",
    }


def test_parse_ai_tolerates_halfwidth_colon_and_spaces():
    """模型偶爾會回半形冒號。為了這個丟掉一句已經生好的句子不划算。"""
    out = phrasebook.parse_ai(
        "  句子: Me da igual. \n  意思: 我都可以\n  提示: 口語"
    )

    assert out["sentence"] == "Me da igual."
    assert out["meaning"] == "我都可以"


def test_parse_ai_returns_none_without_a_sentence():
    """沒有句子就沒有東西可教 —— 意思和提示都是配角。"""
    assert phrasebook.parse_ai("意思：某某\n提示：某某") is None
    assert phrasebook.parse_ai("") is None


def test_parse_ai_allows_missing_meaning_and_note():
    out = phrasebook.parse_ai("句子：Hello.")

    assert out == {"sentence": "Hello.", "meaning": "", "note": ""}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_phrase_ai.py -v -k parse_ai`
Expected: FAIL —— `AttributeError: module 'phrasebook' has no attribute 'parse_ai'`

- [ ] **Step 3: 寫最小實作(解析)**

在 `phrasebook.py` 的 import 區加 `import re`,並在 `format_daily` 之前插入:

```python
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_phrase_ai.py -v -k parse_ai`
Expected: 4 passed

- [ ] **Step 5: 寫失敗的測試(整合)**

在 `tests/test_phrase_ai.py` 末端**追加**:

```python
# ── daily_three:Notion + AI 的整合 ──────────────────────

class FakeStore:
    """把 notion_db 的五個函式換成記憶體版。"""

    def __init__(self, phrases=None, quotes=None):
        self._phrases = phrases or {}
        self.quotes = quotes or []
        self.advanced = []
        self.added = []
        self.marked = []

    def phrases_load(self, language, limit=500):
        return list(self._phrases.get(language, []))

    def phrase_advance(self, page_id, fields):
        self.advanced.append((page_id, fields))
        return True

    def phrase_add(self, sentence, language, meaning="", note="",
                   source="AI生成", day=None, due=None):
        self.added.append({"sentence": sentence, "language": language,
                           "source": source, "due": due})
        return True

    def quotes_load(self, limit=500):
        return list(self.quotes)

    def quote_mark_seen(self, page_id, today):
        self.marked.append((page_id, today))
        return True


def _install(monkeypatch, store, ai=None):
    monkeypatch.setattr(phrasebook, "_store", lambda: store)
    monkeypatch.setattr(phrasebook, "_ai", ai or (lambda prompt: ""))


def _row(page_id, sentence, due=None, appeared=0):
    return {"page_id": page_id, "sentence": sentence, "meaning": "",
            "note": "", "appeared": appeared, "due": due}


def test_daily_three_uses_library_when_something_is_due(monkeypatch):
    store = FakeStore(phrases={
        "英文": [_row("e1", "Play it by ear.", due="2026-08-01")],
        "西班牙文": [_row("s1", "Me da igual.", due="2026-08-01")],
    }, quotes=[{"page_id": "q1", "sentence": "金句", "source": "",
                "last_seen": None}])
    called = []
    _install(monkeypatch, store, ai=lambda p: called.append(p) or "")

    text = phrasebook.daily_three(D)

    assert "Play it by ear." in text
    assert "Me da igual." in text
    assert "金句" in text
    assert called == []          # 庫裡有貨就不該花 AI 的錢


def test_daily_three_advances_schedule_for_picked_rows(monkeypatch):
    store = FakeStore(phrases={"英文": [_row("e1", "A", due="2026-08-01")]})
    _install(monkeypatch, store)

    phrasebook.daily_three(D)

    page_id, fields = store.advanced[0]
    assert page_id == "e1"
    assert fields["appeared"] == 1
    assert fields["due"] == date(2026, 9, 2)


def test_daily_three_marks_quote_as_seen(monkeypatch):
    store = FakeStore(quotes=[{"page_id": "q1", "sentence": "金句",
                               "source": "", "last_seen": None}])
    _install(monkeypatch, store)

    phrasebook.daily_three(D)

    assert store.marked == [("q1", D)]


def test_daily_three_falls_back_to_ai_when_nothing_due(monkeypatch):
    """庫是空的、或都還沒到期 —— 使用者每天都該有一句可看。"""
    store = FakeStore()
    _install(monkeypatch, store,
             ai=lambda p: "句子：Fresh one.\n意思：新的\n提示：測試")

    text = phrasebook.daily_three(D)

    assert "Fresh one." in text


def test_ai_generated_rows_go_back_into_the_library(monkeypatch):
    """生完就丟的話,不貼檔的日子庫永遠長不大(spec 4.3)。"""
    store = FakeStore()
    _install(monkeypatch, store,
             ai=lambda p: "句子：Fresh one.\n意思：新的\n提示：測試")

    phrasebook.daily_three(D)

    added = [a for a in store.added if a["language"] == "英文"][0]
    assert added["sentence"] == "Fresh one."
    assert added["source"] == "AI生成"
    assert added["due"] == date(2026, 9, 2)     # 明天,進入複習循環


def test_daily_three_survives_ai_failure(monkeypatch):
    """AI 掛掉不能讓整封信少掉別的區塊。"""
    store = FakeStore(quotes=[{"page_id": "q1", "sentence": "金句",
                               "source": "", "last_seen": None}])

    def boom(prompt):
        raise RuntimeError("anthropic down")

    _install(monkeypatch, store, ai=boom)

    text = phrasebook.daily_three(D)

    assert "金句" in text
    assert "[EN]" not in text


def test_daily_three_survives_notion_failure(monkeypatch):
    """Notion 掛掉時 phrases_load 回 [] —— 走 AI 補位,不是整段消失。"""
    store = FakeStore()
    _install(monkeypatch, store,
             ai=lambda p: "句子：Fallback.\n意思：備援\n提示：x")

    assert "Fallback." in phrasebook.daily_three(D)


def test_daily_three_returns_none_when_everything_fails(monkeypatch):
    store = FakeStore()

    def boom(prompt):
        raise RuntimeError("down")

    _install(monkeypatch, store, ai=boom)

    assert phrasebook.daily_three(D) is None
```

- [ ] **Step 6: 跑測試確認失敗**

Run: `python -m pytest tests/test_phrase_ai.py -v -k "daily_three or ai_generated"`
Expected: FAIL —— `AttributeError: module 'phrasebook' has no attribute 'daily_three'`

- [ ] **Step 7: 寫實作**

在 `phrasebook.py` 末端追加:

```python
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
    日子庫也在長大,而不是生完就丟(spec 4.3)。
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
```

- [ ] **Step 8: 跑測試確認通過**

Run: `python -m pytest tests/test_phrase_ai.py tests/test_phrasebook.py -v`
Expected: 全部 passed

- [ ] **Step 9: Commit**

```bash
git add phrasebook.py tests/test_phrase_ai.py
git commit -m "feat(phrasebook): daily_three 串接語句庫、金句庫與 AI 補位"
```

---

## Task 8: 把「今日三句」接進每日信

**Files:**
- Modify: `daily_report.py`(`_build_personal_sections` 約 96-121 行、`_email_personal_report`)
- Test: `tests/test_personal_digest_sections.py`

- [ ] **Step 1: 更新既有測試 + 加新測試**

`_build_personal_sections` 要多一個參數,既有三個測試會因為缺參數而失敗。
把 `tests/test_personal_digest_sections.py` 裡**每一個** `dr._build_personal_sections(...)`
呼叫都補上 `phrases=None`,然後在檔案末端追加:

```python
# ── 今日三句(2026-09-01)────────────────────────────────

def test_phrases_go_right_after_todos():
    """學習內容放信尾容易被滑過去,但待辦仍然排最前 —— 那是當天要做的事。"""
    sections = dr._build_personal_sections(
        phrases="[EN] Play it by ear.",
        todos="⬜ [1] 繳健保費",
        reminders="⏰ 08/27 09:30 → 牙醫回診",
        monthly_detail="■ 08/26　・全家　NT$85",
        spending="最新消費 NT$85",
        kitchen="高麗菜　今天到期",
        weather="板橋 28°C",
    )
    titles = _titles(sections)

    assert "待辦" in titles[0]
    phrase_at = min(i for i, t in enumerate(titles) if "三句" in t)
    finance_at = min(i for i, t in enumerate(titles) if "消費" in t)
    assert phrase_at < finance_at


def test_phrases_section_dropped_when_empty():
    sections = dr._build_personal_sections(
        phrases=None, todos="x", reminders=None, monthly_detail=None,
        spending=None, kitchen=None, weather=None,
    )

    assert not any("三句" in t for t in _titles(sections))
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_personal_digest_sections.py -v`
Expected: FAIL —— `TypeError: _build_personal_sections() got an unexpected keyword argument 'phrases'`

- [ ] **Step 3: 寫實作**

把 `daily_report.py` 的 `_build_personal_sections` 整個換成:

```python
def _build_personal_sections(todos, reminders, monthly_detail,
                             spending, kitchen, weather, phrases=None):
    """個人版每日信的區塊與順序。

    待辦 → **今日三句** → 財務 → 買菜(使用者指定順序 2026-08-26,
    三句是 2026-09-01 加的)。

    三句排在待辦之後而不是信尾:學習內容放最後容易被滑過去。待辦仍然
    排最前 —— 那是當天要做的事。

    本月明細排在最新消費前面 —— 使用者要的是「整個月的花銷」,
    那是主角,最新消費只是補充。

    天氣範本裡沒有但現有信件有,保留並排最後。
    空的區塊直接不放:留一張空卡片比沒有還糟。
    """
    candidates = [
        ("📋 今日待辦", todos),
        ("⏰ 進行中提醒", reminders),
        ("🗣️ 今日三句", phrases),
        ("💳 本月消費明細", monthly_detail),
        ("🧾 最新消費", spending),
        ("🍳 冰箱快過期・煮什麼", kitchen),
        ("🌤️ 天氣", weather),
    ]
    return [(title, text) for title, text in candidates if text]
```

在 `_email_personal_report` 裡,`monthly_text = _safe(...)` 那行之後追加:

```python
    def _daily_phrases():
        import phrasebook
        return phrasebook.daily_three(today_tpe())

    phrases_text = _safe("個人版今日三句", _daily_phrases)
```

並把 `sections = _build_personal_sections(...)` 的呼叫補上 `phrases=phrases_text,`。

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_personal_digest_sections.py -v`
Expected: 全部 passed

- [ ] **Step 5: 確認沒弄壞別的**

Run: `python -m pytest tests/ -q`
Expected: 全部 passed

- [ ] **Step 6: Commit**

```bash
git add daily_report.py tests/test_personal_digest_sections.py
git commit -m "feat(daily): 每日個人信加上今日三句"
```

> **Segment 2 完成。** 隔天早上的信會多一張「🗣️ 今日三句」卡片。庫是空的也有 AI 補位,不會開天窗。

---

# Segment 3:消費圓餅圖 + mailer 內嵌圖

## Task 9: `spending_chart.summarize` — 類別彙總

**Files:**
- Create: `spending_chart.py`
- Test: `tests/test_spending_chart.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_spending_chart.py`:

```python
"""本月消費的類別彙總與圓餅圖。

刻意跟 finance_report 分檔:那個模組被 command_router 每次處理 LINE
指令都 import,不該從此拖著 matplotlib(見計畫的檔案結構那節)。

這份測試不驗 PNG 像素 —— 驗圖片內容是脆的,而且真正會出錯的是彙總。
"""

import spending_chart


def _txn(day, amount, category, currency="TWD", direction="支出"):
    return {"date": day, "amount": amount, "category": category,
            "currency": currency, "direction": direction}


def test_summarize_groups_by_category():
    rows = [
        _txn("2026-09-01", 100, "餐飲"),
        _txn("2026-09-02", 50, "餐飲"),
        _txn("2026-09-02", 300, "超市∕量販"),
    ]

    assert spending_chart.summarize(rows, "2026-09") == [
        ("超市∕量販", 300), ("餐飲", 150),
    ]


def test_summarize_sorts_by_amount_desc():
    rows = [_txn("2026-09-01", 10, "餐飲"), _txn("2026-09-01", 900, "旅遊")]

    assert [c for c, _ in spending_chart.summarize(rows, "2026-09")] == ["旅遊", "餐飲"]


def test_summarize_ignores_other_months():
    rows = [_txn("2026-08-31", 999, "餐飲"), _txn("2026-09-01", 100, "餐飲")]

    assert spending_chart.summarize(rows, "2026-09") == [("餐飲", 100)]


def test_summarize_ignores_income():
    """收入混進支出圓餅圖會讓每一片的百分比都錯。"""
    rows = [
        _txn("2026-09-01", 100, "餐飲"),
        _txn("2026-09-02", 50000, "其他", direction="收入"),
    ]

    assert spending_chart.summarize(rows, "2026-09") == [("餐飲", 100)]


def test_summarize_ignores_foreign_currency():
    """把 US$15 加進台幣會得到一個沒有意義、而且看不出哪裡怪的數字。"""
    rows = [
        _txn("2026-09-01", 100, "餐飲"),
        _txn("2026-09-02", 15, "旅遊", currency="USD"),
    ]

    assert spending_chart.summarize(rows, "2026-09") == [("餐飲", 100)]


def test_summarize_fills_missing_category():
    """國泰偶爾送沒有類別的筆數 —— 落到「其他」而不是消失。"""
    rows = [_txn("2026-09-01", 100, None)]

    assert spending_chart.summarize(rows, "2026-09") == [("其他", 100)]


def test_summarize_collapses_tail_into_other():
    """14 片的圓餅圖是色票不是圖表 —— 前 6 大 + 其他。"""
    rows = [_txn("2026-09-01", (10 - i) * 100, f"類別{i}") for i in range(9)]

    out = spending_chart.summarize(rows, "2026-09", top_n=6)

    assert len(out) == 7
    assert out[-1][0] == "其他"
    # 第 7-9 名:200 + 300 + 400（i=8,7,6 → 200,300,400）
    assert out[-1][1] == 900


def test_other_is_always_last_even_when_large():
    """「其他」是分類殘渣,排序上不跟真類別競爭 —— 永遠壓在最後一片。"""
    rows = [
        _txn("2026-09-01", 9999, "其他"),
        _txn("2026-09-01", 100, "餐飲"),
    ]

    out = spending_chart.summarize(rows, "2026-09")

    assert out[-1][0] == "其他"


def test_summarize_returns_empty_without_spending():
    assert spending_chart.summarize([], "2026-09") == []
    assert spending_chart.summarize([_txn("2026-08-01", 100, "餐飲")], "2026-09") == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_spending_chart.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'spending_chart'`

- [ ] **Step 3: 寫最小實作**

建立 `spending_chart.py`:

```python
"""本月消費的類別彙總 + 圓餅圖。

為什麼獨立成檔而不是塞進 finance_report:那個模組被 command_router
每次處理 LINE 指令時都會 import,加進 matplotlib 等於每一則訊息都拖著
繪圖函式庫。相依方向必須是 spending_chart → finance_report。

matplotlib 用 Agg backend(無視窗環境),中文字型沿用 weather.py 那套
載入邏輯 —— 那個坑已經踩平了,不要再寫第二份。
"""

import os
import tempfile
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')  # 無視窗環境。必須在 import pyplot 之前
import matplotlib.pyplot as plt

# 跟 finance_report 共用同一套「什麼算支出」的判斷 —— 各寫一份遲早會漂移
# （mailer.py 借用 line_sender._strip_html 是同樣的取捨）
from finance_report import _currency, _is_spending, _money

OTHER = "其他"

# 圓餅圖畫幾片。類別總共有 14 種（notion_db._SPEND_CATEGORIES），
# 全畫是色票不是圖表。
TOP_N = 6


def summarize(txns, month, top_n=TOP_N):
    """當月 TWD 支出按類別彙總,回 [(類別, 金額)] 由大到小。

    第 top_n 名之後併成「其他」,而且「其他」永遠排最後 —— 它是分類
    殘渣,不該在排序上跟真類別競爭。

    回 [] 代表當月沒有任何 TWD 支出。
    """
    totals = defaultdict(float)
    for t in txns or []:
        if not (t.get("date") or "").startswith(month):
            continue
        if not _is_spending(t) or _currency(t) != "TWD":
            continue
        totals[t.get("category") or OTHER] += t.get("amount") or 0

    if not totals:
        return []

    tail = totals.pop(OTHER, 0)
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)

    head = ranked[:top_n]
    tail += sum(amount for _, amount in ranked[top_n:])

    out = [(name, _round(amount)) for name, amount in head]
    if tail:
        out.append((OTHER, _round(tail)))
    return out


def _round(n):
    """金額累加後可能出現浮點尾巴。整數就回 int,顯示與比較都乾淨。"""
    return int(n) if float(n) == int(n) else round(n, 2)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_spending_chart.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add spending_chart.py tests/test_spending_chart.py
git commit -m "feat(chart): 消費類別彙總 summarize"
```

---

## Task 10: `spending_chart.build_pie` — 畫圖並回摘要文字

**Files:**
- Modify: `spending_chart.py`
- Test: `tests/test_spending_chart.py`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_spending_chart.py` 末端**追加**:

```python
import os


# ── 出圖 ──────────────────────────────────────────────────

def test_build_pie_writes_a_png_and_summary():
    rows = [
        _txn("2026-09-01", 300, "餐飲"),
        _txn("2026-09-02", 700, "超市∕量販"),
    ]

    path, summary = spending_chart.build_pie(rows, "2026-09")

    assert path and os.path.exists(path)
    assert os.path.getsize(path) > 0
    assert "1,000" in summary          # 合計
    assert "2 筆" in summary


def test_summary_survives_in_plain_text_email():
    """純文字版信件沒有圖,合計數字必須也活在文字裡(spec 4.4)。"""
    rows = [_txn("2026-09-01", 1234, "餐飲")]

    _, summary = spending_chart.build_pie(rows, "2026-09")

    assert "NT$1,234" in summary


def test_build_pie_returns_none_without_spending():
    """當月沒有 TWD 支出 → 呼叫端整個區塊不放。"""
    assert spending_chart.build_pie([], "2026-09") == (None, None)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_spending_chart.py -v -k "build_pie or plain_text"`
Expected: FAIL —— `AttributeError: module 'spending_chart' has no attribute 'build_pie'`

- [ ] **Step 3: 寫實作**

在 `spending_chart.py` 末端追加:

```python
# 版面沿用 digest.py 的米色系,信裡才不會突兀
_BG = '#f5f2ec'
_TEXT = '#5b4636'

# 手挑的暖色盤,對齊卡片版型。不用 matplotlib 預設(那組偏冷、
# 相鄰兩片在手機上分不出來)
_COLORS = ['#c96f4a', '#d9a05b', '#8fa87d', '#6d8fa8',
           '#9b7aa8', '#c98fa0', '#b0a89b']


def build_pie(txns, month, top_n=TOP_N):
    """畫當月消費圓餅圖。回 (png 路徑, 摘要文字);沒資料回 (None, None)。

    摘要文字是給純文字版信件用的 —— 那邊沒有圖,合計數字必須有地方活。
    """
    slices = summarize(txns, month, top_n=top_n)
    if not slices:
        return None, None

    rows = [t for t in txns or []
            if (t.get("date") or "").startswith(month)
            and _is_spending(t) and _currency(t) == "TWD"]
    total = sum(amount for _, amount in slices)

    from weather import get_chinese_font
    font = get_chinese_font()

    labels = [name for name, _ in slices]
    values = [amount for _, amount in slices]

    fig, ax = plt.subplots(figsize=(7, 5), dpi=120)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    wedges, _ = ax.pie(
        values,
        colors=_COLORS[:len(values)],
        startangle=90,
        counterclock=False,
        wedgeprops={'edgecolor': _BG, 'linewidth': 2},
    )

    # 百分比與金額放圖例而不是圖上:類別名是中文,標在小片上會疊在一起
    legend_labels = [
        f"{name}　NT${_money(amount)}（{amount / total * 100:.0f}%）"
        for name, amount in slices
    ]
    ax.legend(
        wedges, legend_labels,
        loc='center left', bbox_to_anchor=(1.0, 0.5),
        frameon=False, prop=font, labelcolor=_TEXT,
    )
    ax.set_aspect('equal')

    chart_path = os.path.join(tempfile.gettempdir(), 'spending_pie.png')
    plt.tight_layout()
    plt.savefig(chart_path, facecolor=_BG, bbox_inches='tight')
    plt.close(fig)

    summary = f"本月合計 NT${_money(total)}（共 {len(rows)} 筆）"
    return chart_path, summary
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_spending_chart.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add spending_chart.py tests/test_spending_chart.py
git commit -m "feat(chart): 消費圓餅圖 build_pie"
```

---

## Task 11: `mailer` 支援內嵌圖片

**Files:**
- Modify: `mailer.py`(`_build_message`、`send_email`)
- Test: `tests/test_mailer.py`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_mailer.py` 末端**追加**:

```python
# ── 內嵌圖片(2026-09-01)────────────────────────────────

def _png(tmp_path):
    """最小的合法 PNG(1x1 透明)。不用真的畫圖就能驗 MIME 結構。"""
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4"
        "nGNgAAIAAAUAAY27m/MAAAAASUVORK5CYII="
    )
    path = tmp_path / "pie.png"
    path.write_bytes(data)
    return str(path)


def test_no_images_keeps_the_original_structure(configured, gmail_spy):
    """不給 images 時行為必須跟改動前一模一樣。"""
    mailer.send_email("主旨", "內文")

    msg = _decode(gmail_spy[0])

    assert msg.get_body(("plain",)).get_content().strip() == "內文"
    assert msg.get_body(("html",)) is not None
    assert not list(msg.iter_attachments())


def test_image_is_embedded_with_content_id(configured, gmail_spy, tmp_path):
    mailer.send_email(
        "主旨", "內文",
        html='<div><img src="cid:spending"></div>',
        images={"spending": _png(tmp_path)},
    )

    msg = _decode(gmail_spy[0])
    cids = [p["Content-ID"] for p in msg.walk()
            if p.get_content_type() == "image/png"]

    assert cids == ["<spending>"]


def test_html_and_plain_survive_alongside_the_image(configured, gmail_spy, tmp_path):
    """加了圖不能把純文字版擠掉 —— 不吃 HTML 的收信端還要有東西看。"""
    mailer.send_email(
        "主旨", "本月合計 NT$1,000",
        html='<div><img src="cid:spending">本月合計 NT$1,000</div>',
        images={"spending": _png(tmp_path)},
    )

    msg = _decode(gmail_spy[0])

    assert "NT$1,000" in msg.get_body(("plain",)).get_content()
    assert "cid:spending" in msg.get_body(("html",)).get_content()


def test_missing_image_file_still_sends(configured, gmail_spy, tmp_path):
    """圖檔不見了要照寄 —— 少一張圖遠好過整封信不見。"""
    ok = mailer.send_email(
        "主旨", "內文",
        html='<div><img src="cid:spending"></div>',
        images={"spending": str(tmp_path / "not-there.png")},
    )

    assert ok is True
    assert len(gmail_spy) == 1
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_mailer.py -v`
Expected: FAIL —— `TypeError: send_email() got an unexpected keyword argument 'images'`

- [ ] **Step 3: 寫實作**

把 `mailer.py` 的 `_build_message` 與 `send_email` 換成:

```python
def _attach_images(msg, images):
    """把圖片掛成 multipart/related 的一部分,讓 HTML 用 cid: 引用。

    掛在 html part 上而不是整封信上:掛在最外層會變成「附件」,
    Gmail 會在信末多出一排下載圖示,而不是在文中顯示。

    單張圖讀不到就跳過那張,不中斷整封信 —— 少一張圖遠好過信不見。
    """
    html_part = msg.get_body(("html",))
    if html_part is None:
        return
    for cid, path in (images or {}).items():
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            print(f"[mailer] 讀不到圖片 {path}：{e}，這張跳過")
            continue
        html_part.add_related(
            data, maintype="image", subtype="png", cid=f"<{cid}>",
        )


def _build_message(subject, body, html=None, images=None):
    """html 給了就原樣寄(digest.py 產的卡片版型已經是完整 HTML,
    再包一層 div 會把版面弄壞);沒給就維持原本的簡易轉換。

    純文字版一律保留 —— 收信端不吃 HTML 時還有東西可看,而且純文字版
    看不到圖,所以圖裡的數字必須也出現在文字裡(見 spending_chart 的
    summary_text)。
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender()
    msg["To"] = recipient()
    msg.set_content(strip_html(body))
    msg.add_alternative(
        html or f'<div style="font-family:sans-serif;line-height:1.7">{to_html(body)}</div>',
        subtype="html",
    )
    if images:
        _attach_images(msg, images)
    return msg


def send_email(subject, body, html=None, images=None):
    """寄一封信,成功回 True。

    images 是 {cid: 檔案路徑};HTML 裡用 <img src="cid:那個 cid"> 引用。
    不給就跟改動前完全一樣。

    沒設定就回 False 而不是丟例外 —— 呼叫端(每日排程)當作沒這功能,
    不該因為少一個 env var 就讓整個 job 進 error listener。
    """
    if not is_configured():
        print("[mailer] 沒設 GMAIL_USER / SEND_TOKEN_PICKLE_B64，跳過寄信")
        return False

    msg = _build_message(subject, body, html=html, images=images)
    # Gmail API 收的是 RFC822 全文的 urlsafe base64，不是 MIME 物件
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _service().users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"[mailer] 已寄出：{subject} → {recipient()}")
    return True
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_mailer.py -v`
Expected: 全部 passed(既有 8 個 + 4 個新的)

- [ ] **Step 5: Commit**

```bash
git add mailer.py tests/test_mailer.py
git commit -m "feat(mailer): multipart/related 內嵌圖片"
```

---

## Task 12: `digest` 支援圖片卡片

**Files:**
- Modify: `digest.py`(`build_digest_html`)
- Test: `tests/test_digest.py`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_digest.py` 末端**追加**:

```python
# ── 圖片卡片(2026-09-01)────────────────────────────────

def test_three_tuple_block_renders_an_image():
    html = digest.build_digest_html("2026-09-01", [
        ("📊 本月消費分布", "本月合計 NT$12,345", "spending"),
    ])

    assert 'src="cid:spending"' in html
    assert "本月合計 NT$12,345" in html


def test_two_tuple_blocks_still_work():
    """既有呼叫端全是兩元組 —— 向後相容不能破。"""
    html = digest.build_digest_html("2026-09-01", [("📋 待辦", "x")])

    assert "待辦" in html
    assert "<img" not in html


def test_mixed_tuples_render_together():
    html = digest.build_digest_html("2026-09-01", [
        ("📋 待辦", "繳費"),
        ("📊 分布", "合計 NT$100", "pie"),
    ])

    assert html.index("待辦") < html.index("分布")
    assert 'cid:pie' in html


def test_image_block_without_cid_is_plain():
    """cid 給 None 時退回純文字卡片,不要產出壞掉的 <img src="cid:None">。"""
    html = digest.build_digest_html("2026-09-01", [("📊 分布", "合計", None)])

    assert "<img" not in html
    assert "合計" in html


def test_image_block_with_empty_text_is_dropped():
    """有圖但沒文字仍然算空 —— 純文字版會看到一張空卡片。"""
    html = digest.build_digest_html("2026-09-01", [
        ("📋 待辦", "x"), ("📊 分布", "", "pie"),
    ])

    assert "分布" not in html


def test_image_is_responsive():
    """信在手機上開的機會比桌機高,圖不能撐破卡片。"""
    html = digest.build_digest_html("2026-09-01", [("📊 分布", "合計", "pie")])

    assert "max-width:100%" in html
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_digest.py -v`
Expected: FAIL —— `ValueError: too many values to unpack (expected 2)`

- [ ] **Step 3: 寫實作**

在 `digest.py` 的樣式常數區追加:

```python
_IMG_STYLE = (
    "max-width:100%;height:auto;display:block;"
    "border-radius:8px;margin:0 0 12px;"
)
```

把 `build_digest_html` 換成:

```python
def build_digest_html(date_str, blocks):
    """blocks: [(標題, 內容純文字)] 或 [(標題, 內容, cid)],照給的順序渲染。

    三元組的 cid 對應 mailer 內嵌圖片的 Content-ID,渲染成
    <img src="cid:...">。兩元組維持原行為 —— 既有呼叫端全是兩元組。

    內容是空的區塊直接不出現(即使有圖):留一張空卡片比沒有還糟,
    而純文字版根本看不到圖。
    全部都空回 None,呼叫端據此決定不寄信。
    """
    cards = []
    for block in blocks or []:
        title, body = block[0], block[1]
        cid = block[2] if len(block) > 2 else None
        if not body:
            continue
        img = (f'<img src="cid:{escape(str(cid))}" style="{_IMG_STYLE}">'
               if cid else "")
        cards.append(
            f'<div style="{_CARD_STYLE}">'
            f'<div style="{_CARD_TITLE_STYLE}">{_as_html(title)}</div>'
            f'{img}'
            f'<div style="{_CARD_BODY_STYLE}">{_as_html(body)}</div>'
            f'</div>'
        )

    if not cards:
        return None

    header = (
        f'<div style="font-size:20px;font-weight:800;color:{_HEADING};'
        f'padding:4px 2px 16px;">🌅 早安 · {_as_html(date_str)}</div>'
    )
    footer = (
        f'<div style="font-size:12px;color:{_FOOTER};text-align:center;'
        f'padding:8px 2px 0;">ReportRobot · 每日自動寄送</div>'
    )
    return (
        f'<div style="margin:0;padding:20px;background:{_BG};font-family:{_FONT};">'
        f'<div style="max-width:600px;margin:0 auto;">'
        f'{header}{"".join(cards)}{footer}'
        f'</div></div>'
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_digest.py -v`
Expected: 全部 passed(既有 10 個 + 6 個新的)

- [ ] **Step 5: Commit**

```bash
git add digest.py tests/test_digest.py
git commit -m "feat(digest): 卡片支援內嵌圖片（三元組 blocks）"
```

---

## Task 13: 把圓餅圖接進每日信

**Files:**
- Modify: `daily_report.py`(`_build_personal_sections`、`_email_personal_report`)
- Test: `tests/test_personal_digest_sections.py`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_personal_digest_sections.py` 末端**追加**:

```python
# ── 消費圓餅圖(2026-09-01)──────────────────────────────

def test_chart_section_carries_its_cid():
    """圖片區塊要回三元組,digest 才知道要插 <img>。"""
    sections = dr._build_personal_sections(
        phrases=None, todos="x", reminders=None,
        monthly_chart=("本月合計 NT$1,000", "spending"),
        monthly_detail=None, spending=None, kitchen=None, weather=None,
    )
    chart = [s for s in sections if "分布" in s[0]][0]

    assert len(chart) == 3
    assert chart[1] == "本月合計 NT$1,000"
    assert chart[2] == "spending"


def test_chart_goes_before_the_line_items():
    """分布是主角,流水帳是補充。"""
    sections = dr._build_personal_sections(
        phrases=None, todos="x", reminders=None,
        monthly_chart=("合計", "spending"),
        monthly_detail="■ 09/01", spending=None, kitchen=None, weather=None,
    )
    titles = [s[0] for s in sections]

    assert titles.index("📊 本月消費分布") < titles.index("💳 本月消費明細")


def test_chart_section_dropped_when_unavailable():
    """月初還沒有任何消費時 build_pie 回 (None, None)。"""
    sections = dr._build_personal_sections(
        phrases=None, todos="x", reminders=None, monthly_chart=None,
        monthly_detail=None, spending=None, kitchen=None, weather=None,
    )

    assert not any("分布" in s[0] for s in sections)


def test_chart_stays_before_weather_when_detail_missing():
    """明細是空的時候,分布不能掉到天氣後面 —— 天氣永遠壓最後。"""
    sections = dr._build_personal_sections(
        phrases=None, todos="x", reminders=None,
        monthly_chart=("合計", "spending"),
        monthly_detail=None, spending=None, kitchen=None, weather="板橋 28°C",
    )
    titles = [s[0] for s in sections]

    assert titles.index("📊 本月消費分布") < titles.index("🌤️ 天氣")
```

同時把 `_titles` 改成能吃三元組:

```python
def _titles(sections):
    return [s[0] for s in sections]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_personal_digest_sections.py -v`
Expected: FAIL —— `TypeError: _build_personal_sections() got an unexpected keyword argument 'monthly_chart'`

- [ ] **Step 3: 寫實作**

把 `daily_report.py` 的 `_build_personal_sections` 換成:

```python
def _build_personal_sections(todos, reminders, monthly_detail,
                             spending, kitchen, weather,
                             phrases=None, monthly_chart=None):
    """個人版每日信的區塊與順序。

    待辦 → 今日三句 → 財務 → 買菜(使用者指定順序 2026-08-26,
    三句與圓餅圖是 2026-09-01 加的)。

    三句排在待辦之後而不是信尾:學習內容放最後容易被滑過去。待辦仍然
    排最前 —— 那是當天要做的事。

    圓餅圖排在明細前面:使用者要的是「一個月花在哪」的分布,那是主角,
    逐筆流水帳只是補充。

    monthly_chart 是 (摘要文字, cid) 或 None。有 cid 的區塊回三元組,
    digest.build_digest_html 據此插 <img>。

    空的區塊直接不放:留一張空卡片比沒有還糟。
    """
    candidates = [
        ("📋 今日待辦", todos),
        ("⏰ 進行中提醒", reminders),
        ("🗣️ 今日三句", phrases),
        ("💳 本月消費明細", monthly_detail),
        ("🧾 最新消費", spending),
        ("🍳 冰箱快過期・煮什麼", kitchen),
        ("🌤️ 天氣", weather),
    ]
    out = [(title, text) for title, text in candidates if text]

    if monthly_chart and monthly_chart[0]:
        summary, cid = monthly_chart
        # 插在「近三天消費」/「本月消費明細」之前。用索引搜尋而不是寫死
        # 位置 —— 空區塊會被濾掉,位置每天都不一樣。
        titles = [s[0] for s in out]
        if "💳 本月消費明細" in titles:
            at = titles.index("💳 本月消費明細")
        else:
            # 天氣永遠壓最後,分布不能掉到它後面
            at = len(out) - (1 if "🌤️ 天氣" in titles else 0)
        out.insert(at, ("📊 本月消費分布", summary, cid))

    return out
```

- [ ] **Step 4: 在 `_email_personal_report` 產圖並掛上**

把 `_monthly_detail` 的定義之後、`todos_text = _safe(...)` 之前插入:

```python
    def _monthly_chart():
        """圓餅圖 + 摘要文字。當月沒有 TWD 支出時回 None。"""
        import notion_db
        import spending_chart
        txns = notion_db.transactions_load(limit=400)
        path, summary = spending_chart.build_pie(
            txns, today_tpe().strftime("%Y-%m")
        )
        return (path, summary) if path else None
```

在 `monthly_text = _safe(...)` 之後追加:

```python
    chart = _safe("個人版消費圓餅圖", _monthly_chart)
    chart_path, chart_summary = chart if chart else (None, None)
```

把 `sections = _build_personal_sections(...)` 補上:

```python
        phrases=phrases_text,
        monthly_chart=(chart_summary, CHART_CID) if chart_path else None,
```

純文字版的組法要能吃三元組,把那一行換成:

```python
    plain = SEP.join(f"{s[0]}{NL}{s[1]}" for s in sections)
```

寄信改成帶圖:

```python
    mailer.send_email(
        f"📮 每日個人報 {today}", plain, html=html,
        images={CHART_CID: chart_path} if chart_path else None,
    )
```

並在 `daily_report.py` 的模組常數區(`NL = chr(10)` 附近)加:

```python
# 圓餅圖在信裡的 Content-ID。mailer 掛圖與 digest 產 <img> 都引用它 ——
# 兩邊寫死同一個字串遲早會漂移
CHART_CID = "spending"
```

- [ ] **Step 5: 跑測試確認通過**

Run: `python -m pytest tests/ -q`
Expected: 全部 passed

- [ ] **Step 6: Commit**

```bash
git add daily_report.py tests/test_personal_digest_sections.py
git commit -m "feat(daily): 每日個人信加上本月消費圓餅圖"
```

> **Segment 3 完成。** 信裡會出現一張「📊 本月消費分布」卡片,含圓餅圖與合計。

---

# Segment 4:近三天取代整月 + 標題帶金額 + 寄信重試

## Task 14: `finance_report.format_recent_days`

**Files:**
- Modify: `finance_report.py`(加在 `format_latest_day_spending` 之後)
- Test: `tests/test_finance_report.py`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_finance_report.py` 末端**追加**:

```python
# ── 近三天(2026-09-01)──────────────────────────────────

from datetime import date as _date


def _t(day, amount, shop, direction="支出", currency="TWD"):
    return {"date": day, "amount": amount, "shop": shop,
            "direction": direction, "currency": currency}


def test_recent_days_uses_days_with_data_not_calendar_days():
    """國泰彙整信下午才到,早上寄信時昨天的資料還沒進 Notion。

    用日曆算,月初與同步中斷時使用者會看到一片空白(spec 2.6)。
    """
    txns = [
        _t("2026-08-20", 100, "全家"),
        _t("2026-08-15", 200, "全聯"),
        _t("2026-08-10", 300, "7-11"),
        _t("2026-08-05", 400, "星巴克"),
    ]

    out = finance_report.format_recent_days(txns, _date(2026, 9, 1), days=3)

    assert "全家" in out and "全聯" in out and "7-11" in out
    assert "星巴克" not in out          # 第四舊的那天不列


def test_recent_days_orders_newest_first():
    txns = [_t("2026-08-10", 300, "舊"), _t("2026-08-20", 100, "新")]

    out = finance_report.format_recent_days(txns, _date(2026, 9, 1))

    assert out.index("新") < out.index("舊")


def test_recent_days_shows_per_day_subtotal():
    txns = [_t("2026-08-20", 100, "全家"), _t("2026-08-20", 250, "全聯")]

    out = finance_report.format_recent_days(txns, _date(2026, 9, 1))

    assert "NT$350" in out


def test_recent_days_ignores_income():
    txns = [_t("2026-08-20", 100, "全家"),
            _t("2026-08-20", 50000, "薪水", direction="收入")]

    out = finance_report.format_recent_days(txns, _date(2026, 9, 1))

    assert "薪水" not in out


def test_recent_days_ignores_future_dates():
    """授權中的筆數偶爾帶未來日期,列出來會看起來像資料錯亂。"""
    txns = [_t("2026-09-05", 100, "未來"), _t("2026-08-20", 200, "過去")]

    out = finance_report.format_recent_days(txns, _date(2026, 9, 1))

    assert "未來" not in out
    assert "過去" in out


def test_recent_days_marks_foreign_currency():
    txns = [_t("2026-08-20", 15, "Amazon", currency="USD")]

    out = finance_report.format_recent_days(txns, _date(2026, 9, 1))

    assert "USD 15" in out
    assert "NT$15" not in out


def test_recent_days_returns_none_without_data():
    """每日信的區塊:沒資料就整塊不放,不要說明文案。"""
    assert finance_report.format_recent_days([], _date(2026, 9, 1)) is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_finance_report.py -v -k recent_days`
Expected: FAIL —— `AttributeError: module 'finance_report' has no attribute 'format_recent_days'`

- [ ] **Step 3: 寫實作**

在 `finance_report.py` 的 `format_latest_day_spending` 之後追加:

```python
def format_recent_days(txns, today, days=3):
    """最近幾個**有資料的**日期的逐筆消費。沒有任何支出回 None。

    刻意不是日曆上的近三天:國泰消費彙整信每天彙整前一日、當天下午
    才寄到,早上寄信時昨天的資料還沒進 Notion。用日曆算,月初與同步
    中斷時使用者會看到一片空白 —— format_latest_day_spending 的註解
    已經記過同一個坑。

    取代每日信裡的「本月消費明細」+「最新消費」兩塊:三天的明細已經
    包含最近一天,再放一次是重複。那兩個函式本身保留不動 —— LINE 的
    指令查詢還在用,刪掉會弄壞它們,而那個壞法在每日信上看不出來。
    """
    by_day = defaultdict(list)
    for t in txns or []:
        if not _is_spending(t):
            continue
        day = _to_date(t.get("date"))
        # 授權中的筆數偶爾帶未來日期,列出來看起來像資料錯亂
        if day is None or day > today:
            continue
        by_day[day].append(t)

    if not by_day:
        return None

    lines = []
    for day in sorted(by_day, reverse=True)[:days]:
        rows = by_day[day]
        head = f"■ {day.month}/{day.day:02d}（{_WEEKDAY_ZH[day.weekday()]}）"
        twd_total = 0.0
        body = []
        for t in sorted(rows, key=lambda x: x.get("amount") or 0, reverse=True):
            amount = t.get("amount") or 0
            currency = _currency(t)
            shop = t.get("shop") or t.get("category") or "消費"
            if currency == "TWD":
                body.append(f"　・{shop}　NT${_money(amount)}")
                twd_total += amount
            else:
                # 外幣不併進台幣小計 —— 加起來會得到一個沒有意義的數字
                body.append(f"　・{shop}　{currency} {_money(amount)}")
        if twd_total:
            head = f"{head}　NT${_money(twd_total)}"
        lines.append(head)
        lines.extend(body)
        lines.append("")

    stale = (today - max(by_day)).days
    if stale > 3:
        lines.append(f"⚠️ 已 {stale} 天沒新消費資料")
        lines.append(f"　{_STALE_HINT}")

    return "\n".join(lines).rstrip()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_finance_report.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add finance_report.py tests/test_finance_report.py
git commit -m "feat(finance): format_recent_days — 有資料的最近三天"
```

---

## Task 15: 每日信改用近三天

**Files:**
- Modify: `daily_report.py`(`_build_personal_sections`、`_email_personal_report`)
- Test: `tests/test_personal_digest_sections.py`

- [ ] **Step 1: 更新測試**

把 `tests/test_personal_digest_sections.py` 裡所有
`monthly_detail=...` / `spending=...` 參數改成單一的 `recent_days=...`,
並把 `test_chart_goes_before_the_line_items` 的斷言改成:

```python
def test_chart_goes_before_the_line_items():
    """分布是主角,流水帳是補充。"""
    sections = dr._build_personal_sections(
        phrases=None, todos="x", reminders=None,
        monthly_chart=("合計", "spending"),
        recent_days="■ 9/01（二）　NT$100", kitchen=None, weather=None,
    )
    titles = [s[0] for s in sections]

    assert titles.index("📊 本月消費分布") < titles.index("🧾 近三天消費")
```

在末端追加:

```python
def test_monthly_line_items_are_gone():
    """整月逐筆流水帳已由圓餅圖 + 近三天取代(2026-09-01)。"""
    sections = dr._build_personal_sections(
        phrases=None, todos="x", reminders=None,
        monthly_chart=("合計", "spending"),
        recent_days="■ 9/01（二）", kitchen=None, weather=None,
    )
    titles = [s[0] for s in sections]

    assert "💳 本月消費明細" not in titles
    assert "🧾 最新消費" not in titles
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_personal_digest_sections.py -v`
Expected: FAIL —— `TypeError: ... unexpected keyword argument 'recent_days'`

- [ ] **Step 3: 寫實作**

把 `_build_personal_sections` 換成:

```python
def _build_personal_sections(todos, reminders, recent_days, kitchen, weather,
                             phrases=None, monthly_chart=None):
    """個人版每日信的區塊與順序。

    待辦 → 今日三句 → 財務 → 買菜 → 天氣。

    2026-09-01:原本的「本月消費明細」(整月逐筆)與「最新消費」合併成
    「📊 本月消費分布」(圓餅圖) + 「🧾 近三天消費」。整月流水帳長到
    沒人看,使用者要的是分布;流水帳只需要最近幾天。

    monthly_chart 是 (摘要文字, cid) 或 None。有 cid 的區塊回三元組,
    digest.build_digest_html 據此插 <img>。

    空的區塊直接不放:留一張空卡片比沒有還糟。
    """
    candidates = [
        ("📋 今日待辦", todos),
        ("⏰ 進行中提醒", reminders),
        ("🗣️ 今日三句", phrases),
        ("🧾 近三天消費", recent_days),
        ("🍳 冰箱快過期・煮什麼", kitchen),
        ("🌤️ 天氣", weather),
    ]
    out = [(title, text) for title, text in candidates if text]

    if monthly_chart and monthly_chart[0]:
        summary, cid = monthly_chart
        titles = [s[0] for s in out]
        if "🧾 近三天消費" in titles:
            at = titles.index("🧾 近三天消費")
        else:
            # 天氣永遠壓最後,分布不能掉到它後面
            at = len(out) - (1 if "🌤️ 天氣" in titles else 0)
        out.insert(at, ("📊 本月消費分布", summary, cid))

    return out
```

在 `_email_personal_report` 裡:

1. 刪掉 `_monthly_detail()` 的定義與 `monthly_text = _safe(...)` 那行
   (`format_monthly_detail` 與 `_EMPTY_MONTH` 的 import 一併移除)
2. 把 `_spending_recent()` 換成:

```python
def _spending_recent():
    """有資料的最近三天消費明細。沒有任何支出資料就回 None。

    刻意不是「昨天」:國泰消費彙整信每天彙整前一日、當天下午才寄到,
    早上寄信時昨天的資料還沒進 Notion。寫死「昨日」會每天都是空的。
    """
    import finance_report
    import notion_db

    if not notion_db.is_configured():
        return None

    txns = notion_db.transactions_load(limit=400)
    return finance_report.format_recent_days(txns, today_tpe(), days=3)
```

3. 把 `sections = _build_personal_sections(...)` 的呼叫改成:

```python
    sections = _build_personal_sections(
        todos=todos_text,
        reminders=reminders_text,
        recent_days=spending_text,
        kitchen=kitchen.get("text"),
        weather=weather_text,
        phrases=phrases_text,
        monthly_chart=(chart_summary, CHART_CID) if chart_path else None,
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/ -q`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add daily_report.py tests/test_personal_digest_sections.py
git commit -m "feat(daily): 近三天消費取代整月逐筆明細"
```

---

## Task 16: 寄信重試

**Files:**
- Modify: `mailer.py`(`send_email`)
- Test: `tests/test_mailer.py`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_mailer.py` 末端**追加**:

```python
# ── 重試(2026-09-01)────────────────────────────────────

def test_send_retries_on_transient_failure(configured, monkeypatch):
    """前兩次失敗第三次成功 —— 一次網路抖動不該讓整天的信不見。"""
    attempts = []

    class _FlakyRequest:
        def execute(self):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("temporary failure")
            return {"id": "ok"}

    class _M:
        def send(self, userId, body):
            return _FlakyRequest()

    class _U:
        def messages(self):
            return _M()

    class _S:
        def users(self):
            return _U()

    monkeypatch.setattr(mailer, "_service", lambda: _S())
    monkeypatch.setattr(mailer.time, "sleep", lambda s: None)

    assert mailer.send_email("主旨", "內文") is True
    assert len(attempts) == 3


def test_send_gives_up_after_max_attempts(configured, monkeypatch):
    """三次都失敗要丟例外 —— 呼叫端的 try 會把它送進 admin 通知。

    安靜地回 False 才是真的壞:信沒寄出去而且沒有人知道。
    """
    attempts = []

    class _DeadRequest:
        def execute(self):
            attempts.append(1)
            raise RuntimeError("gmail down")

    class _M:
        def send(self, userId, body):
            return _DeadRequest()

    class _U:
        def messages(self):
            return _M()

    class _S:
        def users(self):
            return _U()

    monkeypatch.setattr(mailer, "_service", lambda: _S())
    monkeypatch.setattr(mailer.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError):
        mailer.send_email("主旨", "內文")

    assert len(attempts) == mailer.SEND_ATTEMPTS
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_mailer.py -v -k "retries or gives_up"`
Expected: FAIL —— `AttributeError: module 'mailer' has no attribute 'time'`

- [ ] **Step 3: 寫實作**

在 `mailer.py` 的 import 區加 `import time`,常數區加:

```python
# 寄幾次才放棄。一次網路抖動不該讓當天的信整封不見;三次還不通
# 就是真的壞了,該讓例外往上冒進 admin 通知。
SEND_ATTEMPTS = 3
SEND_RETRY_SECONDS = 2
```

把 `send_email` 的最後三行換成:

```python
    msg = _build_message(subject, body, html=html, images=images)
    # Gmail API 收的是 RFC822 全文的 urlsafe base64，不是 MIME 物件
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            _service().users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            print(f"[mailer] 已寄出：{subject} → {recipient()}")
            return True
        except Exception as e:
            if attempt == SEND_ATTEMPTS:
                # 最後一次仍然失敗就往上丟 —— 安靜地回 False 才是真的壞:
                # 信沒寄出去而且沒有人知道
                raise
            print(f"[mailer] 第 {attempt} 次寄信失敗（{e}），{SEND_RETRY_SECONDS} 秒後重試")
            time.sleep(SEND_RETRY_SECONDS)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_mailer.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add mailer.py tests/test_mailer.py
git commit -m "feat(mailer): 寄信失敗重試三次"
```

---

## Task 17: 信件標題帶本月金額

**Files:**
- Modify: `daily_report.py`(`_email_personal_report`)
- Test: `tests/test_personal_report.py`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_personal_report.py` 末端**追加**:

```python
# ── 標題(2026-09-01)────────────────────────────────────

import daily_report as _dr


def test_subject_carries_the_month_total():
    """手機通知列直接看到數字,不用點開。"""
    out = _dr._subject("2026-09-01", "本月合計 NT$12,345（共 87 筆）")

    assert out == "📮 每日個人報 2026-09-01 · 本月 NT$12,345"


def test_subject_falls_back_without_a_summary():
    assert _dr._subject("2026-09-01", None) == "📮 每日個人報 2026-09-01"


def test_subject_falls_back_when_summary_has_no_amount():
    """摘要格式變了也不能產出半句話的標題。"""
    assert _dr._subject("2026-09-01", "本月沒有消費") == "📮 每日個人報 2026-09-01"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_personal_report.py -v -k subject`
Expected: FAIL —— `AttributeError: module 'daily_report' has no attribute '_subject'`

- [ ] **Step 3: 寫實作**

在 `daily_report.py` 的 `_build_personal_sections` 之前插入:

```python
# 從圓餅圖的摘要文字裡把金額挖出來塞進標題。摘要的格式是
# 「本月合計 NT$12,345（共 87 筆）」（spending_chart.build_pie）。
_SUBJECT_AMOUNT_RE = re.compile(r"NT\$[\d,]+")


def _subject(today, chart_summary):
    """信件標題。有金額就帶上 —— 手機通知列直接看到,不用點開。

    抓不到金額就退回原本的標題:標題是每天都會出現的東西,寧可少一段
    資訊,也不要因為摘要格式變了就產出半句話。
    """
    base = f"📮 每日個人報 {today}"
    if not chart_summary:
        return base
    m = _SUBJECT_AMOUNT_RE.search(chart_summary)
    return f"{base} · 本月 {m.group(0)}" if m else base
```

在 `daily_report.py` 的 import 區加 `import re`。

把 `_email_personal_report` 末尾的寄信改成:

```python
    mailer.send_email(
        _subject(today, chart_summary), plain, html=html,
        images={CHART_CID: chart_path} if chart_path else None,
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/ -q`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add daily_report.py tests/test_personal_report.py
git commit -m "feat(daily): 信件標題帶上本月消費金額"
```

---

# 收尾

- [ ] **全套測試**

Run: `python -m pytest tests/ -q`
Expected: 全部 passed(既有 222+ 個 + 本次新增約 70 個)

- [ ] **更新 `docs/HANDOFF.md`**

在「3. 真實環境驗證狀態」表格追加三列(照既有格式,誠實標未驗證):

```markdown
| 語句庫 / 金句庫（Notion 建表） | ❌ **尚未在真實 Notion 建過**，只有單元測試 |
| 每日信「今日三句」 | ❌ 沒有真的收過信 |
| 消費圓餅圖（內嵌圖片） | ❌ 沒有真的收過信，Gmail 對 cid: 的呈現未驗證 |
```

在「環境變數」表下方的說明追加一段:

```markdown
> **語句庫需要使用者手動填內容。** 第一次部署後 `ensure_all_dbs()` 會建出
> 「📚 語言學習」區塊與兩張空表。金句庫的內容要從 renhezheng44 帳號的
> Notion 匯出 CSV 再匯入 —— Notion 的 integration token 綁 workspace，
> 跨帳號分享頁面只解決「人看得到」，解決不了「機器人讀得到」。
```

- [ ] **Commit**

```bash
git add docs/HANDOFF.md
git commit -m "docs: HANDOFF 補上語句庫與圓餅圖的驗證狀態"
```

- [ ] **上線後的人工驗證(這份計畫做不到,必須真的收信)**

1. 打 `/admin/daily-report`(或等隔天 07:00),確認信收得到
2. 圓餅圖在 Gmail **手機 App** 上有顯示(桌機版對 cid: 較寬容,手機才是真考驗)
3. 中文字型沒變成豆腐方塊 —— Railway 容器的字型與本機不同
4. 「今日三句」的西班牙文重音字沒亂碼
5. Notion 的「語句庫」出現次數確實 +1、下次出現被推到明天

---

# 已知風險

| 風險 | 徵狀 | 對策 |
|---|---|---|
| Railway 容器缺中文字型 | 圓餅圖圖例變豆腐方塊 | `weather.get_chinese_font()` 已在生產環境驗證過(每日溫度圖天天在跑),沿用同一套即可 |
| Gmail 手機 App 擋 cid: 圖片 | 卡片只有文字沒有圖 | 摘要文字獨立存在(`summary_text`),沒圖也讀得到合計 |
| 語句庫很大時 `phrases_load` 變慢 | 每日 job 拉長 | 上限 500 筆、每語言各撈一次。超過這個量級再改成 Notion 端 filter |
| AI 每天多兩次呼叫 | token 成本上升 | 只在「庫裡沒有到期的」時才呼叫;庫養起來之後幾乎不會觸發 |
| `_SCHEMAS` 欄位只增不減 | 改欄位名會留下孤兒欄位 | Task 4 的 schema 一次定好;真要改名要手動去 Notion 刪舊欄 |
