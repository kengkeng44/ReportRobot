# 共同消費分攤 + 記帳按鈕統計調整 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓「記一筆」分得出個人消費與共同消費,共同消費自動均分並只把使用者負擔的那半記進帳,同時讓金額 Quick Reply 只看最近 90 天且近期權重更高。

**Architecture:** 「交易明細」的「金額」欄語意維持「我實際負擔多少」不變,新增「原始總額」與「分攤類型」兩欄承載額外資訊 —— 這讓六處既有報表一行都不用改。互動維持既有的無狀態設計,分攤類型插在第三段(`記一筆 晚餐 600 共同`),四種狀態靠 `arg` 內容判斷。

**Tech Stack:** Python 3、pytest、notion-client、LINE Messaging API Quick Reply

**Spec:** `docs/superpowers/specs/2026-08-30-shared-expense-split-design.md`

**分支:** `feat/shared-expense-split`(已建立,spec 已 commit)

---

## 檔案結構

| 檔案 | 這次負責什麼 |
|---|---|
| `finance_report.py` | 純邏輯:分攤計算、解析、按鈕統計、報表文字。不碰 Notion 也不碰 LINE |
| `notion_db.py` | schema 定義 + 兩欄的讀寫與舊資料 fallback |
| `command_router.py` | 四態分流 + 個人/共同按鈕 + 說明文字 |
| `tests/test_shared_expense.py` | **新增** —— 分攤計算、解析、fingerprint、報表 |
| `tests/test_manual_entry_quickreply.py` | 既有 —— 統計函式與四態分流,本次擴充 |
| `tests/test_transactions_load.py` | 既有 —— 新欄位的讀取與 fallback,本次擴充 |
| `tests/test_notion_schema.py` | 既有 —— schema 有沒有長出新欄位,本次擴充 |

**分檔理由:** 分攤是一個新的概念(政策 + 計算 + 解析 + 呈現),集中在一個新測試檔比散進四個既有檔好找。統計函式與四態分流則是既有檔案的延伸行為,留在原地。

**跑測試:** 所有指令都在專案根目錄 `C:\Users\acer\projects\ReportRobot` 下執行。

---

### Task 1: 分攤計算

**Files:**
- Modify: `finance_report.py`(在 `_FOOD_HINTS` 定義之後、`guess_category` 之前)
- Test: `tests/test_shared_expense.py`(新建)

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_shared_expense.py`:

```python
"""共同消費分攤 —— 均分、解析、去重鍵、報表呈現。

「金額」欄的語意是「我實際負擔多少」，這一點不能被這次改動動搖：
六處既有報表都讀那一欄，語意一變就會靜靜地高估支出。
"""

import finance_report as fr


# ── 分攤計算 ─────────────────────────────────────────────

def test_my_share_halves_even_amounts():
    assert fr.my_share_of(600) == 300


def test_my_share_rounds_half_up_not_bankers():
    """內建 round() 在這兩個 .5 會給 302 與 304 —— 同樣是 .5 卻一個往下
    一個往上。共同消費除以 2 大量產生 .5，忽上忽下的話對帳查不出規律。"""
    assert fr.my_share_of(605) == 303      # 302.5 → 往上
    assert fr.my_share_of(607) == 304      # 303.5 → 往上


def test_my_share_of_zero_and_none():
    assert fr.my_share_of(0) == 0
    assert fr.my_share_of(None) == 0


def test_my_share_ratio_is_a_named_constant():
    """0.5 是政策決定不是數學。散落的 / 2 讀起來像數學，改的時候會漏。"""
    assert fr.MY_SHARE == 0.5
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_shared_expense.py -v`
Expected: FAIL — `AttributeError: module 'finance_report' has no attribute 'my_share_of'`

- [ ] **Step 3: 實作**

在 `finance_report.py` 的 `_FOOD_HINTS` 那行之後加入:

```python


# ── 共同消費分攤 ─────────────────────────────────────────
# 共同消費裡我負擔的比例。寫成常數而不是散落各處的 / 2 ——
# 這是一個政策決定，不是數學。散落的 / 2 讀起來像數學，改的時候會漏。
MY_SHARE = 0.5


def my_share_of(total):
    """共同消費裡我負擔多少。四捨五入到整數 —— 台幣沒有小數。

    不用內建 round()：那是 banker's rounding，round(302.5) 得 302 而
    round(303.5) 得 304，同樣是 .5 卻一個往下一個往上。共同消費除以 2
    在金額為奇數時大量產生 .5，忽上忽下對帳時查不出規律。
    """
    return int((total or 0) * MY_SHARE + 0.5)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_shared_expense.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add finance_report.py tests/test_shared_expense.py
git commit -m "feat(finance): 共同消費均分計算

MY_SHARE 寫成常數而非散落的 / 2 —— 這是政策決定不是數學。
進位用 int(x + 0.5) 不用內建 round()：banker's rounding 在 .5 時
一下往上一下往下，共同消費除以 2 大量產生 .5，對帳查不出規律。"
```

---

### Task 2: `parse_manual` 支援第三段 + fingerprint 分流

**Files:**
- Modify: `finance_report.py:238-277`(`make_manual_fingerprint` 與 `parse_manual`)
- Test: `tests/test_shared_expense.py`

- [ ] **Step 1: 寫失敗測試**

追加到 `tests/test_shared_expense.py` 尾端:

```python


# ── 解析三段輸入 ─────────────────────────────────────────

def test_parse_reads_trailing_split_type():
    got = fr.parse_manual("晚餐 600 共同")

    assert got["split_type"] == "共同"
    assert got["total"] == 600
    assert got["amount"] == 300          # 金額欄 = 我實際負擔
    assert got["shop"] == "晚餐"


def test_parse_personal_keeps_full_amount():
    got = fr.parse_manual("午餐 120 個人")

    assert got["split_type"] == "個人"
    assert got["total"] == 120
    assert got["amount"] == 120


def test_parse_without_split_type_leaves_it_none():
    """第三段還沒選。呼叫端要靠這個 None 決定跳個人/共同按鈕。"""
    got = fr.parse_manual("晚餐 600")

    assert got["split_type"] is None
    assert got["amount"] == 600
    assert got["total"] == 600


def test_split_keyword_only_matches_at_the_end():
    """「共同基金 3000」的共同是商店名的一部分，不是分攤類型。"""
    got = fr.parse_manual("共同基金 3000")

    assert got["split_type"] is None
    assert got["shop"] == "共同基金"


def test_income_never_asks_about_splitting():
    """薪水不用跟人分。留成 None 會讓收入也跳出個人/共同那一段。"""
    got = fr.parse_manual("薪水 50000")

    assert got["direction"] == "收入"
    assert got["split_type"] == "個人"
    assert got["amount"] == 50000


def test_parse_still_returns_none_without_amount():
    """既有行為：沒金額不猜。記一筆金額錯的帳比沒記更難發現。"""
    assert fr.parse_manual("午餐") is None
    assert fr.parse_manual("") is None
    assert fr.parse_manual("晚餐 共同") is None


# ── 去重鍵 ───────────────────────────────────────────────

def test_personal_fingerprint_format_unchanged():
    """既有資料的比對基準不能動 —— 個人維持四段格式。"""
    assert (fr.make_manual_fingerprint("2026-08-30", 120, "午餐")
            == fr.make_manual_fingerprint("2026-08-30", 120, "午餐", "個人"))


def test_shared_and_personal_with_same_share_do_not_collide():
    """個人 300 與共同 600（分攤 300）的「金額」欄都是 300。
    不加區別就會產生相同 fingerprint，其中一筆會被當成重複。"""
    personal = fr.parse_manual("晚餐 300 個人")
    shared = fr.parse_manual("晚餐 600 共同")

    assert personal["amount"] == shared["amount"] == 300
    assert personal["fingerprint"] != shared["fingerprint"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_shared_expense.py -v`
Expected: FAIL — `KeyError: 'split_type'`(前幾個測試)

- [ ] **Step 3: 實作**

在 `finance_report.py` 的 `make_manual_fingerprint` 之前加入:

```python
# 分攤類型只認這兩個詞。不加「一起」「共用」這類同義詞 ——
# 「一起吃飯 300」會被誤判成共同消費，而使用者是用按鈕選的，
# 同義詞只擴大誤判面不增加可用性。
_SPLIT_TYPES = ("個人", "共同")


def _strip_split_type(text):
    """從尾端剝離「個人」/「共同」。回 (剩下的文字, split_type or None)。

    只認尾端：「共同基金 3000」的共同在開頭，那是商店名不是分攤類型。
    """
    cleaned = (text or "").strip()
    for name in _SPLIT_TYPES:
        if cleaned.endswith(name):
            return cleaned[: -len(name)].strip(), name
    return cleaned, None
```

把 `make_manual_fingerprint` 整個換成:

```python
def make_manual_fingerprint(day, amount, shop, split_type=None):
    """個人維持既有四段格式，共同才加後綴。

    個人 300 與共同 600（分攤 300）的「金額」欄都是 300，不加區別就會
    算出相同 fingerprint。個人不動格式是為了不改變既有資料的比對基準。
    """
    raw = f"手動|{day}|{amount}|{shop}"
    if split_type == "共同":
        raw += "|共同"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
```

把 `parse_manual` 整個換成:

```python
def parse_manual(text, today=None):
    """「午餐 120」或「晚餐 600 共同」→ 交易 dict。沒有金額就回 None。

    沒金額不猜 —— 記一筆金額錯的帳，比沒記更難發現也更難修。

    split_type 的三態約定，呼叫端靠它決定下一步：
      沒有金額        → 回 None，呼叫端跳金額按鈕
      「晚餐 600」    → split_type=None，呼叫端跳個人/共同按鈕
      「晚餐 600 共同」→ split_type="共同"，呼叫端寫入 Notion
    split_type=None 時 amount 先等於 total，但那是還沒決定分攤前的暫定值，
    呼叫端不該拿去寫入。
    """
    if not text:
        return None
    cleaned, split_type = _strip_split_type(text)

    m = _AMOUNT_RE.search(cleaned)
    if not m:
        return None
    total = float(m.group(1))
    total = int(total) if total == int(total) else total

    shop = (cleaned[:m.start()] + " " + cleaned[m.end():]).strip()
    shop = re.sub(r"\s+", " ", shop)
    if not shop:
        shop = "未命名"

    day = (today or date.today()).isoformat()
    direction = "收入" if any(k in shop for k in _INCOME_HINTS) else "支出"

    # 收入不跟人分攤。留成 None 的話「薪水 50000」也會跳出個人/共同那一段。
    if direction == "收入" and split_type is None:
        split_type = "個人"

    amount = my_share_of(total) if split_type == "共同" else total

    return {
        "date": day,
        "amount": amount,               # 我實際負擔 —— 六處報表都讀這個
        "total": total,                 # 掏出去的全額
        "split_type": split_type,
        "shop": shop,
        "category": guess_category(shop),
        "direction": direction,
        "status": "已結帳",              # 手動輸入就是最終金額，不需要對帳
        "source": "手動",
        "fingerprint": make_manual_fingerprint(day, total, shop, split_type),
    }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_shared_expense.py tests/test_finance_report.py -v`
Expected: PASS。`test_finance_report.py` 既有的 6 個 `parse_manual` 測試必須全綠 —— 它們沒帶分攤類型,`amount` 應該仍等於原值。

- [ ] **Step 5: Commit**

```bash
git add finance_report.py tests/test_shared_expense.py
git commit -m "feat(finance): parse_manual 認得第三段分攤類型

split_type 三態約定讓呼叫端分得出「沒金額」「沒選分攤」「齊全」。
只認尾端關鍵字：「共同基金 3000」的共同是商店名。
收入直接當個人 —— 薪水不用跟人分，不該多問一段。
fingerprint 共同才加後綴：個人 300 與共同 600 的金額欄都是 300。"
```

---

### Task 3: Notion 兩個新欄位

**Files:**
- Modify: `notion_db.py:183-207`(`_SCHEMAS["交易明細"]`)、`notion_db.py:1030-1066`(`transaction_add`)、`notion_db.py:1110-1160`(`transactions_load`)
- Test: `tests/test_notion_schema.py`、`tests/test_transactions_load.py`

- [ ] **Step 1: 寫失敗測試**

追加到 `tests/test_notion_schema.py` 尾端:

```python


def test_transaction_schema_has_split_columns():
    """共同消費要存兩件事：分的是哪一種、整桌多少錢。

    _ensure_properties 只補不刪，線上既有 DB 會自動長出這兩欄，
    既有資料列不動。
    """
    schema = notion_db._SCHEMAS["交易明細"]

    assert "分攤類型" in schema
    assert "原始總額" in schema

    names = [o["name"] for o in schema["分攤類型"]["select"]["options"]]
    assert names == ["個人", "共同"]
```

追加到 `tests/test_transactions_load.py` 尾端:

```python


class OneRow:
    """單列假 Notion，用來測欄位讀取與舊資料 fallback。"""

    def __init__(self, props):
        self._props = props
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"results": [{"properties": self._props}],
                "has_more": False, "next_cursor": None}


def _install_row(monkeypatch, props):
    dbs = OneRow(props)
    monkeypatch.setattr(notion_db, "_TOKEN", "secret_fake")
    monkeypatch.setattr(notion_db, "_PARENT_PAGE", "page_fake")
    monkeypatch.setattr(notion_db, "_client", FakeClient(dbs))
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db_交易明細")
    return dbs


def test_reads_split_columns(monkeypatch):
    _install_row(monkeypatch, {
        "日期": {"date": {"start": "2026-08-30"}},
        "金額": {"number": 300},
        "原始總額": {"number": 600},
        "分攤類型": {"select": {"name": "共同"}},
    })

    row = notion_db.transactions_load(limit=1)[0]

    assert row["split_type"] == "共同"
    assert row["total"] == 600
    assert row["amount"] == 300


def test_old_rows_without_split_columns_default_to_personal(monkeypatch):
    """遷移前的資料沒有這兩欄。既有的國泰同步資料本來就是自己刷的，
    一律當個人；原始總額回退成金額 —— 個人消費兩者本來就相等。

    沒有這兩條 fallback，所有統計都得特判 None。"""
    _install_row(monkeypatch, {
        "日期": {"date": {"start": "2026-08-01"}},
        "金額": {"number": 361},
        "來源": {"select": {"name": "國泰消費彙整"}},
    })

    row = notion_db.transactions_load(limit=1)[0]

    assert row["split_type"] == "個人"
    assert row["total"] == 361
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_notion_schema.py tests/test_transactions_load.py -v`
Expected: FAIL — `KeyError: '分攤類型'` 與 `KeyError: 'split_type'`

- [ ] **Step 3: 實作**

在 `notion_db.py` 的 `_SCHEMAS["交易明細"]` 裡,`"Fingerprint"` 那行**之前**插入:

```python
        # 共同消費把「金額」存成我實際負擔的那半，整桌多少錢存這裡。
        # 「金額」欄的語意（我實際負擔）維持不變，六處既有報表才不用改。
        "分攤類型": _select(("個人", "default"), ("共同", "blue")),
        "原始總額": {"number": {"format": "number"}},
```

在 `transaction_add` 的 `candidates` 字典裡,`"Fingerprint"` 那行**之前**插入:

```python
        # 沒帶就不寫這兩欄 —— 國泰同步走的是同一個函式，硬填「個人」
        # 會把「這個來源沒有分攤概念」偽裝成「已經判斷過是個人」。
        "分攤類型": _prop_select(txn.get("split_type")),
        "原始總額": _prop_number(txn.get("total")),
```

在 `transactions_load` 的 `out.append({...})` 裡,`"source"` 那行**之後**插入:

```python
                    # 遷移前的資料沒有這兩欄。國泰同步的本來就是自己刷的，
                    # 一律當個人；原始總額回退成金額 —— 個人消費兩者相等。
                    # 沒有這兩條 fallback，所有統計都得特判 None。
                    "split_type": _read_select(props, "分攤類型") or "個人",
                    "total": (_read_number(props, "原始總額")
                              if _read_number(props, "原始總額") is not None
                              else _read_number(props, "金額")),
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_notion_schema.py tests/test_transactions_load.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add notion_db.py tests/test_notion_schema.py tests/test_transactions_load.py
git commit -m "feat(notion): 交易明細加分攤類型與原始總額兩欄

「金額」欄語意維持「我實際負擔」，六處既有報表一行都不用改。
_ensure_properties 只補不刪，線上既有 DB 自動長出兩欄。
舊資料讀不到就當個人、總額回退成金額，讓統計不必特判 None。"
```

---

### Task 4: 統計加 90 天窗與時間加權

**Files:**
- Modify: `finance_report.py:288-324`(`frequent_expense_items`)
- Modify: `tests/test_manual_entry_quickreply.py:16-19`(`_txn` helper —— 見下方時間炸彈說明)
- Test: `tests/test_manual_entry_quickreply.py`

**先讀這段:既有測試是一顆時間炸彈**

`tests/test_manual_entry_quickreply.py` 的 `_txn()` 把日期寫死成 `"2026-08-19"`。
加了 90 天窗之後,這些記錄會在 2026-11-17 之後全部被判定為過期,
一整組測試會在某個沒人動過程式碼的日子突然變紅。

所以這個 Task 的第一步是把 helper 改成相對日期,並讓測試明確注入 `today`。

- [ ] **Step 1: 改掉既有 helper 的寫死日期**

把 `tests/test_manual_entry_quickreply.py` 開頭的 import 區與 `_txn` 換成:

```python
import pytest
from datetime import date, timedelta

import command_router as cr
import finance_report as fr


# 測試用的「今天」。統計函式一律傳這個進去 —— 寫死日期加上 90 天窗
# 等於埋一顆定時炸彈，會在某個沒人動過程式碼的日子突然全部變紅。
TODAY = date(2026, 8, 30)


def _txn(shop, amount, source="手動", days_ago=0, split_type="個人", total=None):
    day = TODAY - timedelta(days=days_ago)
    return {"date": day.isoformat(), "amount": amount, "shop": shop,
            "category": "餐飲", "direction": "支出", "currency": "TWD",
            "status": "已結帳", "source": source,
            "split_type": split_type,
            "total": total if total is not None else amount}
```

接著只把 `fr.frequent_expense_items(...)` 的呼叫點補上 `today=TODAY`。例如:

```python
assert fr.frequent_expense_items(txns, limit=3, pad=False, today=TODAY) == [
    "午餐", "咖啡", "搭車"]
```

找出全部呼叫點:

```bash
grep -n "frequent_expense_items" tests/test_manual_entry_quickreply.py
```

撰稿當下有 7 處(第 56、66、73、79、84、91、101、107 行附近)。

**`fr.frequent_amounts(...)` 的呼叫點這一步先不要動** —— 那個函式要到 Task 5
才長出 `today` 參數,現在補上去會讓整檔在 Task 4 就爆 `TypeError`,
而且會蓋掉 Task 5 那個「先看到測試失敗」的訊號。

`cr.handle(...)` 那幾個四態測試不用改 —— 它們的資料都是 `days_ago=0`。

- [ ] **Step 2: 追加新行為的失敗測試**

追加到 `tests/test_manual_entry_quickreply.py` 的「常記品項」那一節尾端:

```python


def test_frequent_items_ignores_records_older_than_90_days():
    """物價會漲，兩年前那個 65 元的咖啡不該還卡在按鈕上。"""
    txns = [_txn("舊品項", 100, days_ago=120),
            _txn("舊品項", 100, days_ago=200),
            _txn("午餐", 120, days_ago=5)]

    assert fr.frequent_expense_items(
        txns, limit=6, pad=False, today=TODAY) == ["午餐"]


def test_frequent_items_weight_recent_records_higher():
    """各記一次，近的排前面。沒有權重的話兩者同分，順序只看誰先出現。"""
    txns = [_txn("舊愛", 100, days_ago=75),      # ×1
            _txn("新歡", 200, days_ago=3)]       # ×3

    assert fr.frequent_expense_items(
        txns, limit=6, pad=False, today=TODAY) == ["新歡", "舊愛"]


def test_frequent_items_boundary_at_exactly_90_days():
    """剛好 90 天算數，91 天不算 —— 邊界寫清楚，日後才不會各自解讀。"""
    txns = [_txn("剛好", 100, days_ago=90), _txn("過期", 100, days_ago=91)]

    assert fr.frequent_expense_items(
        txns, limit=6, pad=False, today=TODAY) == ["剛好"]
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: FAIL — `TypeError: frequent_expense_items() got an unexpected keyword argument 'today'`

- [ ] **Step 4: 實作**

在 `finance_report.py` 的 `_DEFAULT_ITEMS` 那行之後加入:

```python

# 距今多久算幾次。近期的算比較多次，物價漲了按鈕會自己跟上，
# 不必手動維護一份「現在午餐多少錢」的清單。
_WEIGHT_WINDOWS = ((30, 3), (60, 2), (90, 1))


def _recency_weight(day, today):
    """這筆記錄在統計裡算幾次。超過 90 天回 0（不計）。

    日期壞掉的列回 0 而不是丟例外 —— Notion 上手改過的列會長出各種
    格式，一列壞掉不該讓整排按鈕消失。
    """
    if not day:
        return 0
    try:
        d = date.fromisoformat(str(day)[:10])
    except ValueError:
        return 0
    delta = max((today - d).days, 0)
    for limit, weight in _WEIGHT_WINDOWS:
        if delta <= limit:
            return weight
    return 0
```

把 `frequent_expense_items` 的簽名與統計迴圈換成:

```python
def frequent_expense_items(txns, limit=6, pad=True, today=None):
    """常記品項：手動記過越多次的排前面，近期的算比較多次。

    只看 source == "手動"。交易明細裡混著信用卡自動同步的資料，商店名
    長這樣「全聯福利中心－板橋板新」—— 放到按鈕上沒有意義，而且 LINE 的
    label 上限 20 字會把它截成半截店名。

    同次數保持第一次出現的順序：每次跳出來的按鈕位置都在動，比排序不準
    更難用。（與 kitchen.frequent_items 同一套理由，過濾條件不同故各自實作。）

    pad=True 時用 _DEFAULT_ITEMS 補到 limit：沒歷史就給空按鈕列，
    等於這個功能第一天不存在。

    today 可注入：沒有這個參數，測試就得依賴系統時鐘，跑起來時好時壞。
    """
    today = today or date.today()
    counts = {}
    order = []
    for t in txns or []:
        if (t.get("source") or "") != "手動":
            continue
        name = (t.get("shop") or "").strip()
        if not name:
            continue
        weight = _recency_weight(t.get("date"), today)
        if not weight:
            continue
        if name not in counts:
            order.append(name)
        counts[name] = counts.get(name, 0) + weight

    ranked = sorted(order, key=lambda n: (-counts[n], order.index(n)))
    out = ranked[:limit]

    if pad:
        for name in _DEFAULT_ITEMS:
            if len(out) >= limit:
                break
            if name not in counts:
                out.append(name)
    return out
```

- [ ] **Step 5: 跑測試確認通過**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add finance_report.py tests/test_manual_entry_quickreply.py
git commit -m "feat(finance): 常記品項只看 90 天並加時間權重

近 30 天 ×3、31-60 天 ×2、61-90 天 ×1，物價漲了按鈕自己跟上。
today 改成可注入 —— 既有測試把日期寫死成 2026-08-19，加上 90 天窗
等於埋定時炸彈，會在某個沒人動過程式碼的日子突然全部變紅。"
```

---

### Task 5: 金額按鈕改用原始總額並推斷慣用型態

**Files:**
- Modify: `finance_report.py:337-372`(`frequent_amounts`)
- Test: `tests/test_manual_entry_quickreply.py`

- [ ] **Step 1: 補上既有 `frequent_amounts` 呼叫點的 `today`**

Task 4 刻意把這批留到現在。找出全部呼叫點:

```bash
grep -n "frequent_amounts" tests/test_manual_entry_quickreply.py
```

撰稿當下有 9 處(第 115、122、128、133、139、148、154、158、165 行附近)。
每一處補上 `today=TODAY`,例如:

```python
assert fr.frequent_amounts(txns, "午餐", limit=5, pad=False, today=TODAY) == [120, 100]
```

- [ ] **Step 2: 寫失敗測試**

追加到 `tests/test_manual_entry_quickreply.py` 的「常用金額」那一節尾端:

```python


def test_amounts_use_gross_not_my_share():
    """按鈕上的數字是使用者要打進去的錢（整桌 600），不是分攤額（300）。
    用金額欄統計的話，共同消費的按鈕每次砍半，愈跳愈小。"""
    txns = [_txn("晚餐", 300, split_type="共同", total=600),
            _txn("晚餐", 300, split_type="共同", total=600)]

    assert fr.frequent_amounts(
        txns, "晚餐", pad=False, today=TODAY) == [600]


def test_amounts_prefer_the_usual_split_type_of_that_item():
    """「個人/共同」問在最後一段，跳金額按鈕時還不知道這筆屬於哪種。
    用品項自己的歷史推斷：晚餐幾乎都是共同的就跳共同價位。"""
    txns = [_txn("晚餐", 300, split_type="共同", total=600),
            _txn("晚餐", 300, split_type="共同", total=600),
            _txn("晚餐", 300, split_type="共同", total=620),
            _txn("晚餐", 150, split_type="個人", total=150)]

    out = fr.frequent_amounts(txns, "晚餐", pad=False, today=TODAY)

    assert 150 not in out
    assert out[0] == 600


def test_amounts_fall_back_to_all_records_when_sample_too_small():
    """一兩筆推不出習慣，硬推會讓按鈕少到不夠用。"""
    txns = [_txn("宵夜", 100, split_type="共同", total=200),
            _txn("宵夜", 80, split_type="個人", total=80)]

    out = fr.frequent_amounts(txns, "宵夜", pad=False, today=TODAY)

    assert sorted(out) == [80, 200]


def test_amounts_ignore_records_older_than_90_days():
    txns = [_txn("午餐", 95, days_ago=150), _txn("午餐", 130, days_ago=2)]

    assert fr.frequent_amounts(
        txns, "午餐", pad=False, today=TODAY) == [130]
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: FAIL — `TypeError: frequent_amounts() got an unexpected keyword argument 'today'`

- [ ] **Step 4: 實作**

在 `finance_report.py` 的 `_SEED_AMOUNTS` 之後加入:

```python

# 樣本少於這個數就不做型態推斷：一兩筆推不出習慣，硬推還會讓按鈕
# 少到不夠用。
_SPLIT_INFERENCE_MIN = 2


def _prefer_usual_split(rows):
    """只留這個品項慣用的分攤型態。rows 是 [(txn, weight)]。

    「個人 / 共同」問在最後一段，跳金額按鈕的當下還不知道這筆屬於哪種，
    個人的午餐 120 會跟共同的晚餐 600 混在同一排。用品項自己的歷史推斷：
    晚餐九成是共同的就跳共同價位，咖啡都是個人的就跳個人價位。

    篩完不足 _SPLIT_INFERENCE_MIN 筆就整組退回，寧可混也不要沒得按。
    """
    weights = defaultdict(int)
    for t, w in rows:
        weights[t.get("split_type") or "個人"] += w
    if not weights:
        return rows
    usual = max(weights, key=lambda k: weights[k])
    picked = [(t, w) for t, w in rows
              if (t.get("split_type") or "個人") == usual]
    return picked if len(picked) >= _SPLIT_INFERENCE_MIN else rows
```

把 `frequent_amounts` 整個換成:

```python
def frequent_amounts(txns, item, limit=5, pad=True, today=None):
    """某個品項的常用金額，記過越多次的排前面，近期的算比較多次。

    依品項分別統計：共用一份全域金額清單會讓咖啡的按鈕上出現 200 元。
    與 frequent_expense_items 一樣只看 source == "手動"。

    統計對象是「原始總額」而不是「金額」：按鈕上的數字是使用者要打進去
    的錢（整桌 600），不是分攤額（300）。用金額欄統計的話，共同消費的
    按鈕每次砍半，愈跳愈小，最後每筆都得手打。

    整數金額回 int，否則按鈕上會出現「120.0」。
    """
    key = (item or "").strip()
    if not key:
        return []
    today = today or date.today()

    rows = []
    for t in txns or []:
        if (t.get("source") or "") != "手動":
            continue
        if (t.get("shop") or "").strip() != key:
            continue
        weight = _recency_weight(t.get("date"), today)
        if not weight:
            continue
        rows.append((t, weight))

    rows = _prefer_usual_split(rows)

    counts = {}
    order = []
    for t, weight in rows:
        # 舊資料沒有 total（transactions_load 已回退成金額，這裡是雙保險：
        # 單元測試與其他呼叫端可能直接餵 dict 進來）
        amount = t.get("total")
        if amount is None:
            amount = t.get("amount")
        if amount is None:
            continue
        amount = int(amount) if float(amount) == int(amount) else amount
        if amount not in counts:
            order.append(amount)
        counts[amount] = counts.get(amount, 0) + weight

    ranked = sorted(order, key=lambda a: (-counts[a], order.index(a)))
    out = ranked[:limit]

    if pad:
        for amount in _SEED_AMOUNTS.get(key, []):
            if len(out) >= limit:
                break
            if amount not in counts:
                out.append(amount)
    return out
```

- [ ] **Step 5: 跑測試確認通過**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add finance_report.py tests/test_manual_entry_quickreply.py
git commit -m "feat(finance): 金額按鈕改用原始總額並推斷品項慣用型態

按鈕上的數字是使用者要打進去的錢（整桌 600）不是分攤額（300）——
用金額欄統計的話共同消費的按鈕每次砍半，愈跳愈小。

分攤類型問在最後一段，跳金額按鈕時還不知道型態，用品項自己的歷史
推斷；樣本不足兩筆就退回全部，寧可混也不要沒得按。"
```

---

### Task 6: 第四態 —— 個人 / 共同按鈕

**Files:**
- Modify: `command_router.py:768-793`(新增 `_manual_split_quick_reply`)、`command_router.py:797-812`(`fin_manual` 分流)
- Test: `tests/test_manual_entry_quickreply.py`

- [ ] **Step 1: 寫失敗測試**

追加到 `tests/test_manual_entry_quickreply.py` 的「三態分流」那一節尾端:

```python


# ── 第四態：個人 / 共同 ───────────────────────────────────

def test_amount_without_split_type_returns_split_buttons(fake_notion):
    fake_notion(txns=[_txn("晚餐", 300, split_type="共同", total=600)])

    reply = cr.handle("記一筆 晚餐 600", _ctx())

    labels = [i["action"]["label"] for i in reply["quickReply"]["items"]]
    assert labels == ["個人", "共同"]


def test_split_buttons_send_complete_commands(fake_notion):
    """按鈕送出的字串必須自己 parse 得回來，不然點了沒反應。"""
    fake_notion(txns=[])

    reply = cr.handle("記一筆 晚餐 600", _ctx())

    sent = [i["action"]["text"] for i in reply["quickReply"]["items"]]
    assert sent == ["記一筆 晚餐 600 個人", "記一筆 晚餐 600 共同"]
    for s in sent:
        assert cr.parse(s)[0] == "fin_manual"


def test_split_buttons_work_without_notion(fake_notion):
    """走到這一段的人已經把品項與金額打完了，不該卡在最後一步。
    兩顆靜態按鈕不碰 Notion。"""
    fake_notion(configured=False)

    reply = cr.handle("記一筆 晚餐 600", _ctx())

    assert reply["quickReply"]["items"][1]["action"]["text"] == "記一筆 晚餐 600 共同"


def test_shared_entry_writes_only_my_share(fake_notion):
    fake = fake_notion(txns=[])

    reply = cr.handle("記一筆 晚餐 600 共同", _ctx())

    assert len(fake.added) == 1
    assert fake.added[0]["amount"] == 300      # 金額欄 = 我實際負擔
    assert fake.added[0]["total"] == 600
    assert fake.added[0]["split_type"] == "共同"
    assert "300" in reply and "600" in reply    # 兩個數字都要看得到


def test_personal_entry_writes_full_amount(fake_notion):
    fake = fake_notion(txns=[])

    cr.handle("記一筆 午餐 120 個人", _ctx())

    assert fake.added[0]["amount"] == 120
    assert fake.added[0]["total"] == 120
    assert fake.added[0]["split_type"] == "個人"


def test_income_skips_the_split_question(fake_notion):
    """薪水不用跟人分 —— 不該多問一段。"""
    fake = fake_notion(txns=[])

    cr.handle("記一筆 薪水 50000", _ctx())

    assert len(fake.added) == 1
    assert fake.added[0]["direction"] == "收入"
```

**同時更新既有測試 `test_full_flow_actually_writes`** —— 它原本走兩段就斷言寫入,
現在要多走一段。把它換成:

```python
def test_full_flow_actually_writes(fake_notion):
    """整條線走完：點品項 → 點金額 → 點個人/共同 → 真的進 Notion。

    2026-08-30 起多了第三段。這是設計變更（分攤類型放在最後一段），
    不是回歸 —— 見 docs/superpowers/specs/2026-08-30-shared-expense-split-design.md
    """
    fake = fake_notion(txns=[_txn("午餐", 120)])

    step1 = cr.handle("記一筆", _ctx())
    item_cmd = step1["quickReply"]["items"][0]["action"]["text"]
    step2 = cr.handle(item_cmd, _ctx())
    amount_cmd = step2["quickReply"]["items"][0]["action"]["text"]
    step3 = cr.handle(amount_cmd, _ctx())
    split_cmd = step3["quickReply"]["items"][0]["action"]["text"]

    cr.handle(split_cmd, _ctx())

    assert len(fake.added) == 1
    assert fake.added[0]["shop"] == "午餐"
    assert fake.added[0]["amount"] == 120
    assert fake.added[0]["split_type"] == "個人"
    assert fake.added[0]["category"] == "餐飲"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: FAIL —— `記一筆 晚餐 600` 目前會直接寫入,回的是字串不是 dict,
所以 `reply["quickReply"]` 會丟 `TypeError: string indices must be integers`

- [ ] **Step 3: 實作**

在 `command_router.py` 的 `_manual_amount_quick_reply` 之後加入:

```python
def _manual_split_quick_reply(item, total):
    """「記一筆 晚餐 600」→ 個人 / 共同按鈕。

    兩顆靜態按鈕，不碰 Notion —— 前兩段沒有 Notion 會退回文字提示，
    但走到這一段的人已經把品項與金額都打完了，不該卡在最後一步。

    順序固定「個人」在前：多數記錄是個人消費，常用的放左邊少移動一次拇指。
    """
    from flex_builder import quick_reply_text

    return quick_reply_text(
        f"{item} NT${total:,} —— 這筆是自己的還是一起分的?",
        [("個人", f"記一筆 {item} {total} 個人"),
         ("共同", f"記一筆 {item} {total} 共同")])
```

把 `_handle_finance` 的 `fin_manual` 區塊換成:

```python
    if kind == "fin_manual":
        if not arg:
            return _manual_item_quick_reply()
        txn = finance_report.parse_manual(arg)
        if not txn:
            # 有品項沒金額 —— 這是兩段式的第二段，不是錯誤
            return _manual_amount_quick_reply(arg.strip())
        if txn["split_type"] is None:
            # 有金額沒分攤類型 —— 第三段。收入不會走到這裡（parse_manual
            # 直接給「個人」），薪水不用跟人分。
            return _manual_split_quick_reply(txn["shop"], txn["total"])
        if not notion_db.transaction_add(txn):
            return "寫入 Notion 失敗,請稍後再試。"
        sign = "+" if txn["direction"] == "收入" else "-"
        if txn["split_type"] == "共同":
            # 兩個數字都要看得到：整桌多少、我付多少
            return (f"✅ 已記錄:{txn['shop']}　共同 NT${txn['total']:,}\n"
                    f"　你分攤 {sign}NT${txn['amount']:,}（{txn['category']}）")
        return (f"✅ 已記錄:{txn['shop']}　{sign}NT${txn['amount']:,}"
                f"（{txn['category']}）")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: PASS

- [ ] **Step 5: 跑全套測試**

Run: `python -m pytest tests/ -q`
Expected: 全綠。有紅的先確認是不是這次的設計變更導致 —— 如果是,更新斷言時
要在測試的 docstring 寫明「這是設計變更」,不要默默改成順著實作走。

- [ ] **Step 6: Commit**

```bash
git add command_router.py tests/test_manual_entry_quickreply.py
git commit -m "feat(line): 記一筆加第四態 —— 個人 / 共同按鈕

維持無狀態設計：按鈕送出的文字自己攜帶進度，四態靠 arg 內容判斷。
兩顆靜態按鈕不碰 Notion，走到最後一步的人不該卡在那裡。

行為變更：「記一筆 午餐 120」不再直接寫入，會多問一段。
想一步到位打「記一筆 午餐 120 個人」。"
```

---

### Task 7: 本月支出加共同分攤那一行

**Files:**
- Modify: `finance_report.py:64-98`(`format_monthly_spending`)
- Test: `tests/test_shared_expense.py`

- [ ] **Step 1: 寫失敗測試**

追加到 `tests/test_shared_expense.py` 尾端:

```python


# ── 報表 ─────────────────────────────────────────────────

def _row(date_, amount, split_type="個人", total=None, category="餐飲"):
    return {"date": date_, "amount": amount, "category": category,
            "shop": "某店", "direction": "支出", "status": "已結帳",
            "currency": "TWD", "split_type": split_type,
            "total": total if total is not None else amount}


def test_monthly_spending_shows_shared_line():
    txns = [_row("2026-08-01", 300, "共同", 600),
            _row("2026-08-02", 250, "共同", 500),
            _row("2026-08-03", 120)]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "670" in text                      # 總額仍是我實際負擔
    assert "共同分攤" in text
    assert "550" in text                      # 我在共同消費裡負擔的
    assert "1,100" in text                    # 整桌加起來


def test_monthly_spending_hides_shared_line_when_none():
    """常態是零的欄位每個月都佔一行，會讓人不再讀它。"""
    txns = [_row("2026-08-01", 120), _row("2026-08-02", 80)]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "共同分攤" not in text


def test_monthly_total_still_counts_my_share_only():
    """金額欄的語意是「我實際負擔」—— 這次改動不能讓總額變成整桌。"""
    txns = [_row("2026-08-01", 300, "共同", 600)]

    text = fr.format_monthly_spending(txns, "2026-08")

    assert "NT$300" in text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_shared_expense.py -v`
Expected: FAIL — `assert '共同分攤' in text`

- [ ] **Step 3: 實作**

在 `finance_report.py` 的 `format_monthly_spending` 裡,`lines = [...]` 那行**之後**、
`for cat, amt in sorted(...)` 迴圈**之前**插入:

```python
    # 「金額」欄已經是我實際負擔，這行是為了看得到整桌花多少。
    # 沒有共同消費的月份不印 —— 常態是零的欄位每個月都佔一行，
    # 會讓人不再讀它。
    shared = [t for t in twd_rows if (t.get("split_type") or "個人") == "共同"]
    if shared:
        mine = sum(t.get("amount") or 0 for t in shared)
        gross = sum((t.get("total") if t.get("total") is not None
                     else t.get("amount")) or 0 for t in shared)
        lines.insert(2, f"　其中共同分攤 NT${_money(mine)}"
                        f"（原始 NT${_money(gross)}）")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_shared_expense.py tests/test_finance_report.py -v`
Expected: PASS。既有的 `test_monthly_total_and_breakdown` 必須仍綠 ——
它的資料沒有 `split_type`,不該長出那一行。

- [ ] **Step 5: Commit**

```bash
git add finance_report.py tests/test_shared_expense.py
git commit -m "feat(finance): 本月支出加共同分攤那一行

總額維持「我實際負擔」不變，多一行讓整桌金額看得到。
沒有共同消費的月份不印 —— 常態是零的欄位會讓人不再讀它。"
```

---

### Task 8: 說明文字同步

**Files:**
- Modify: `command_router.py:121-123`(`HELP_TEXT`)、`command_router.py:731-743`(`_MANUAL_USAGE` 與 `_MANUAL_QUICK_HINT`)
- Test: `tests/test_help_text.py`

- [ ] **Step 1: 寫失敗測試**

追加到 `tests/test_help_text.py` 尾端:

```python


def test_help_mentions_the_split_step():
    """/help 教的流程必須跟實際流程一致，否則使用者會照著一個
    已經不成立的說明操作。"""
    import command_router as cr

    assert "共同" in cr.HELP_TEXT
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_help_text.py -v`
Expected: FAIL — `assert '共同' in cr.HELP_TEXT`

- [ ] **Step 3: 實作**

把 `command_router.py` 的 `HELP_TEXT` 裡那三行換成:

```python
    "  • 記一筆        ← 只打三個字會跳常記品項,再點金額、點個人/共同\n"
    "  • 記一筆 午餐 120 個人   ← 直接打可以一步到位\n"
    "  • 記一筆 晚餐 600 共同   ← 共同消費自動除以 2 記你那半\n"
    "  • 記一筆 薪水 50000     ← 含薪水/獎金/退款會記成收入\n"
```

把 `_MANUAL_USAGE` 換成:

```python
_MANUAL_USAGE = (
    "要記什麼?這樣打:\n"
    "記一筆 午餐 120 個人\n"
    "記一筆 晚餐 600 共同　← 自動除以 2,記你那半\n"
    "記一筆 薪水 50000\n\n"
    "金額一定要有。含「薪水、獎金、退款」等字會自動記成收入。"
)
```

把 `_MANUAL_QUICK_HINT` 換成:

```python
_MANUAL_QUICK_HINT = (
    "要記什麼?點下面常記的,或直接打:\n"
    "記一筆 午餐 120 個人\n\n"
    "點完品項與金額還會問是個人還是共同。\n"
    "含「薪水、獎金、退款」等字會記成收入。"
)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/ -q`
Expected: 全套全綠

- [ ] **Step 5: Commit**

```bash
git add command_router.py tests/test_help_text.py
git commit -m "docs(line): 說明文字同步第三段流程

/help 教的流程要跟實際流程一致，否則使用者會照著一個已經
不成立的說明操作。"
```

---

## 收尾

- [ ] **跑完整測試套件**

Run: `python -m pytest tests/ -q`
Expected: 全綠。既有 42 個手動記帳測試裡,只有 `test_full_flow_actually_writes`
被刻意更新過(多走一段),其餘不該有任何斷言變動。

- [ ] **更新 `docs/HANDOFF.md`**

在「真實環境驗證狀態」表格加一列:

```markdown
| 共同消費分攤 + 第四態按鈕 | ❌ **沒在 LINE 上按過**;Notion 新欄位也還沒實跑建立 |
```

在「下一步」那節加:

```markdown
- [ ] 在 LINE 私訊實測「記一筆」→ 品項 → 金額 → 個人/共同
- [ ] 確認 Notion「交易明細」真的長出「分攤類型」「原始總額」兩欄
      （`_ensure_properties` 在第一次 `get_or_create_db("交易明細")` 時補）
- [ ] 記一筆共同消費後,到 Notion 上核對金額欄是分攤額、原始總額是整桌
```

- [ ] **Commit 收尾**

```bash
git add docs/HANDOFF.md
git commit -m "docs: HANDOFF 補上共同消費分攤的驗證待辦

寫完不等於能用 —— Notion 新欄位與第四態按鈕都還沒在真實環境跑過。"
```

- [ ] **回報使用者,等他決定要不要 merge 與 deploy**

不自行 push 或 merge。專案跑在 Railway,merge 到 `main` 等同上線。

---

## 這次不做

- **1.2 權重分攤** —— 已改為均分,`MY_SHARE = 0.5`
- **月更快取表** —— 即時重算已更即時
- **對方欠款追蹤** —— 這是記帳不是對帳系統
- **改既有報表的讀取欄位** —— 「金額」欄語意未變是這個設計的核心價值
- **動國泰同步路徑** —— 自動同步的資料不帶 `split_type`,讀回來時 fallback 成「個人」
