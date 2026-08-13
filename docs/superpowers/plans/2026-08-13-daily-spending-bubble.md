# 每日推播「最近一天消費」bubble 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每日 07:00 推播的 carousel 加一個 bubble,顯示最近一天的信用卡消費明細與本月累計。

**Architecture:** 純邏輯 formatter 放 `finance_report.py`(不碰 Notion、不碰 LINE),I/O 放 `daily_report._spending_recent()`,畫面放 `flex_builder.daily_report_carousel()` 的新具名參數。完全比照既有 `_kitchen_reminder()` 的分層。

**Tech Stack:** Python 3、pytest、既有 `tz_utils.today_tpe()` 與 `flex_builder.text_bubble()`。

**Spec:** `docs/superpowers/specs/2026-08-13-daily-spending-bubble-design.md`

---

## File Structure

| 檔案 | 動作 | 職責 |
|---|---|---|
| `finance_report.py` | 修改(檔尾新增一節) | `format_latest_day_spending()` 純邏輯,輸出字串或 None |
| `daily_report.py` | 修改 | `_spending_recent()` 取數 + 在 `run_daily_report()` 接上 |
| `flex_builder.py:312-371` | 修改 | `daily_report_carousel()` 加 `spending_text` 具名參數 |
| `tests/test_daily_spending.py` | 新建 | formatter 與 `_spending_recent()` 的測試 |
| `tests/test_flex_carousel.py` | 修改(檔尾追加) | carousel bubble 數與順序 |

**重要慣例:** formatter 回傳的字串**不含標題行**。標題由 bubble header 提供
(`text_bubble(title=...)`),這跟 `kitchen_text` 的做法一致。spec 第 3 節樣式圖裡的
「💳 最近一天消費」那行是 bubble header,不是 body。

---

### Task 1: formatter 骨架 — 正常路徑

**Files:**
- Modify: `finance_report.py`(檔尾新增)
- Test: `tests/test_daily_spending.py`(新建)

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_daily_spending.py`:

```python
"""每日推播的「最近一天消費」段落。

核心行為：國泰彙整信天生延遲一天，所以這裡取的是「資料裡最新的那一天」，
不是字面上的昨天，而且日期要照實寫出來。
"""

from datetime import date

import finance_report


def _txn(day, amount, shop="某店", direction="支出"):
    return {"date": day, "amount": amount, "shop": shop, "direction": direction}


def test_shows_latest_day_total_and_count():
    txns = [
        _txn("2026-08-12", 839, "全聯"),
        _txn("2026-08-12", 351, "統一超商"),
        _txn("2026-08-12", 100, "便利商店"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "8/12" in text
    assert "NT$1,290" in text
    assert "3 筆" in text
    assert "全聯" in text and "NT$839" in text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_daily_spending.py -v`
Expected: FAIL — `AttributeError: module 'finance_report' has no attribute 'format_latest_day_spending'`

- [ ] **Step 3: 寫最小實作**

在 `finance_report.py` 的 import 區把 `date` 補進來(該行目前是 `from datetime import date`,已存在,不用改),然後在檔尾新增:

```python
# ─────────────────────────────────────────────────────────
# 最近一天消費（每日推播用）
# ─────────────────────────────────────────────────────────

_WEEKDAY_ZH = "一二三四五六日"


def _to_date(value):
    """'2026-08-12' 或 '2026-08-12T00:00' → date。壞資料回 None。"""
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def format_latest_day_spending(txns, today, stale_days=3, max_rows=5):
    """資料裡最新一天的支出明細 + 本月累計。沒有任何支出就回 None。

    刻意不是「昨天」：國泰消費彙整信每天彙整前一日，今天早上推播時昨天的
    資料還沒進 Notion（見 spec 第 2 節）。硬寫「昨日」會每天都是空的。

    沒資料時回 None 而不是說明文案 —— 這是每天自動來的推播，不是使用者
    主動按按鈕查詢。剛啟用時天天跳「還沒有紀錄」會讓人略過整則推播。
    """
    rows = []
    for t in txns or []:
        if not _is_spending(t):
            continue
        day = _to_date(t.get("date"))
        if day is None or day > today:
            continue
        rows.append((day, t))

    if not rows:
        return None

    latest = max(day for day, _ in rows)
    day_rows = [t for day, t in rows if day == latest]
    total = sum(t.get("amount") or 0 for t in day_rows)

    head = f"{latest.month}/{latest.day:02d}（{_WEEKDAY_ZH[latest.weekday()]}）"
    lines = [f"{head}　NT${_money(total)}　{len(day_rows)} 筆", ""]

    ordered = sorted(day_rows, key=lambda t: t.get("amount") or 0, reverse=True)
    for t in ordered[:max_rows]:
        name = t.get("shop") or t.get("category") or "消費"
        lines.append(f"・{name}　NT${_money(t.get('amount'))}")

    return "\n".join(lines)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_daily_spending.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_daily_spending.py finance_report.py
git commit -m "feat(finance): 最近一天消費 formatter 骨架"
```

---

### Task 2: 過濾規則 — 收入、壞日期、未來日期、空輸入

**Files:**
- Modify: `finance_report.py`(`format_latest_day_spending` 已含這些邏輯,本 task 補測試驗證)
- Test: `tests/test_daily_spending.py`

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_daily_spending.py`:

```python
def test_returns_none_when_empty():
    assert finance_report.format_latest_day_spending([], date(2026, 8, 13)) is None


def test_returns_none_when_only_income():
    txns = [_txn("2026-08-12", 50000, "薪水", direction="收入")]

    assert finance_report.format_latest_day_spending(txns, date(2026, 8, 13)) is None


def test_ignores_future_dates():
    """資料髒掉時，未來日期不該主導『最新一天』。"""
    txns = [
        _txn("2026-08-12", 100, "正常"),
        _txn("2026-09-30", 999, "壞資料"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "8/12" in text
    assert "壞資料" not in text


def test_ignores_rows_without_valid_date():
    txns = [
        _txn("2026-08-12", 100, "正常"),
        _txn("", 999, "沒日期"),
        _txn("not-a-date", 999, "爛日期"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "正常" in text
    assert "沒日期" not in text and "爛日期" not in text


def test_only_latest_day_is_shown():
    txns = [
        _txn("2026-08-12", 100, "今天的"),
        _txn("2026-08-11", 999, "昨天的"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "今天的" in text
    assert "昨天的" not in text
    assert "1 筆" in text


def test_missing_direction_counts_as_spending():
    """direction 缺值時視為支出 —— 沿用既有 _is_spending 的約定。"""
    txns = [{"date": "2026-08-12", "amount": 100, "shop": "無方向"}]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "無方向" in text


def test_none_amount_counts_as_a_row_but_zero():
    """金額沒解析出來的仍是一筆消費，但不能讓總額變成 None 而炸掉。"""
    txns = [
        _txn("2026-08-12", 100, "有金額"),
        _txn("2026-08-12", None, "沒金額"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "2 筆" in text
    assert "NT$100" in text
    assert "沒金額" in text
    assert "NT$-" in text, "金額不明要顯示 -，不是 0，才看得出是缺資料"
```

- [ ] **Step 2: 跑測試**

Run: `python -m pytest tests/test_daily_spending.py -v`
Expected: 全部 PASS(Task 1 的實作已涵蓋這些規則)。若有 FAIL,依訊息修 `format_latest_day_spending`,不要改測試。

- [ ] **Step 3: Commit**

```bash
git add tests/test_daily_spending.py
git commit -m "test(finance): 補最近一天消費的過濾規則測試"
```

---

### Task 3: 本月累計

**Files:**
- Modify: `finance_report.py`
- Test: `tests/test_daily_spending.py`

- [ ] **Step 1: 寫失敗的測試**

追加:

```python
def test_month_total_uses_today_month_not_latest_txn_month():
    """月初時本月累計會很小甚至 0 —— 那是預期，它回答的是
    『這個月到目前為止花了多少』。"""
    txns = [
        _txn("2026-07-31", 5000, "上月的"),
        _txn("2026-08-02", 300, "本月的"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "本月累計 NT$300" in text


def test_month_total_sums_whole_month():
    txns = [
        _txn("2026-08-01", 1000, "月初"),
        _txn("2026-08-12", 839, "全聯"),
        _txn("2026-08-12", 351, "超商"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "本月累計 NT$2,190" in text


def test_month_total_excludes_income():
    txns = [
        _txn("2026-08-05", 50000, "薪水", direction="收入"),
        _txn("2026-08-12", 100, "全聯"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert "本月累計 NT$100" in text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_daily_spending.py -k month -v`
Expected: FAIL — 輸出裡沒有「本月累計」

- [ ] **Step 3: 實作**

在 `format_latest_day_spending()` 的 `ordered` 迴圈之後、`return` 之前插入:

```python
    month = today.strftime("%Y-%m")
    month_total = sum(
        t.get("amount") or 0 for day, t in rows if day.strftime("%Y-%m") == month
    )

    lines.append("")
    lines.append(f"本月累計 NT${_money(month_total)}")
```

`return "\n".join(lines)` 維持在最後一行。

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_daily_spending.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_daily_spending.py finance_report.py
git commit -m "feat(finance): 最近一天消費加本月累計"
```

---

### Task 4: 明細截斷

**Files:**
- Modify: `finance_report.py`
- Test: `tests/test_daily_spending.py`

- [ ] **Step 1: 寫失敗的測試**

追加:

```python
def test_truncates_detail_rows():
    txns = [_txn("2026-08-12", 100 + i, f"店{i}") for i in range(7)]

    text = finance_report.format_latest_day_spending(
        txns, date(2026, 8, 13), max_rows=5
    )

    assert "…另 2 筆" in text
    assert "7 筆" in text, "筆數要算全部，不是只算顯示出來的"


def test_no_truncation_note_when_within_limit():
    txns = [_txn("2026-08-12", 100, f"店{i}") for i in range(5)]

    text = finance_report.format_latest_day_spending(
        txns, date(2026, 8, 13), max_rows=5
    )

    assert "另" not in text


def test_details_sorted_by_amount_desc():
    txns = [
        _txn("2026-08-12", 100, "小"),
        _txn("2026-08-12", 900, "大"),
        _txn("2026-08-12", 500, "中"),
    ]

    text = finance_report.format_latest_day_spending(txns, date(2026, 8, 13))

    assert text.index("大") < text.index("中") < text.index("小")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_daily_spending.py -k truncat -v`
Expected: FAIL — 沒有「…另 2 筆」

- [ ] **Step 3: 實作**

在 `finance_report.py` 的 `for t in ordered[:max_rows]:` 迴圈**之後**插入:

```python
    if len(ordered) > max_rows:
        lines.append(f"　…另 {len(ordered) - max_rows} 筆")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_daily_spending.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_daily_spending.py finance_report.py
git commit -m "feat(finance): 最近一天消費明細超過 5 筆時截斷"
```

---

### Task 5: 資料過舊警告

**Files:**
- Modify: `finance_report.py`
- Test: `tests/test_daily_spending.py`

- [ ] **Step 1: 寫失敗的測試**

追加:

```python
def test_no_stale_warning_at_threshold():
    """剛好 3 天不算舊 —— 國泰的信本來就延遲一天，加上週末就會到 3 天。"""
    txns = [_txn("2026-08-10", 100, "全聯")]

    text = finance_report.format_latest_day_spending(
        txns, date(2026, 8, 13), stale_days=3
    )

    assert "⚠️" not in text


def test_stale_warning_past_threshold():
    txns = [_txn("2026-08-09", 100, "全聯")]

    text = finance_report.format_latest_day_spending(
        txns, date(2026, 8, 13), stale_days=3
    )

    assert "⚠️ 已 4 天沒新消費資料" in text
    assert "同步中斷" in text, "要講出兩種可能，不然分不出是壞了還是本來就沒花錢"


def test_stale_warning_sits_between_details_and_month_total():
    txns = [_txn("2026-08-01", 100, "全聯")]

    text = finance_report.format_latest_day_spending(
        txns, date(2026, 8, 13), stale_days=3
    )

    assert text.index("全聯") < text.index("⚠️") < text.index("本月累計")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_daily_spending.py -k stale -v`
Expected: FAIL — 輸出裡沒有 ⚠️

- [ ] **Step 3: 實作**

在 `finance_report.py` 檔尾常數區(`_WEEKDAY_ZH` 旁)加:

```python
_STALE_HINT = "可能是沒刷卡,也可能是同步中斷"
```

在 `format_latest_day_spending()` 的截斷那段**之後**、本月累計那段**之前**插入:

```python
    stale = (today - latest).days
    if stale > stale_days:
        lines.append("")
        lines.append(f"⚠️ 已 {stale} 天沒新消費資料")
        lines.append(f"　{_STALE_HINT}")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_daily_spending.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_daily_spending.py finance_report.py
git commit -m "feat(finance): 消費資料過舊時說明原因"
```

---

### Task 6: `daily_report._spending_recent()` 取數

**Files:**
- Modify: `daily_report.py`
- Test: `tests/test_daily_spending.py`

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_daily_spending.py`(檔案上方 import 區補 `import sys`、`import pytest`、`import daily_report`):

```python
class FakeNotion:
    def __init__(self, txns=None, configured=True):
        self._txns = txns or []
        self._configured = configured

    def is_configured(self):
        return self._configured

    def transactions_load(self, limit=200):
        return self._txns


@pytest.fixture
def use_notion(monkeypatch):
    def _install(**kwargs):
        fake = FakeNotion(**kwargs)
        monkeypatch.setitem(sys.modules, "notion_db", fake)
        return fake
    return _install


def test_spending_recent_returns_none_when_notion_not_configured(use_notion):
    use_notion(txns=[_txn("2026-08-12", 100, "全聯")], configured=False)

    assert daily_report._spending_recent() is None


def test_spending_recent_returns_none_when_no_transactions(use_notion):
    use_notion(txns=[])

    assert daily_report._spending_recent() is None


def test_spending_recent_uses_taipei_today(use_notion, monkeypatch):
    """今天用台北時間判斷 —— Railway 容器是 UTC，凌晨會算成前一天。"""
    use_notion(txns=[_txn("2026-08-12", 839, "全聯")])
    monkeypatch.setattr(daily_report, "today_tpe", lambda: date(2026, 8, 13))

    text = daily_report._spending_recent()

    assert "全聯" in text and "8/12" in text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_daily_spending.py -k spending_recent -v`
Expected: FAIL — `AttributeError: module 'daily_report' has no attribute '_spending_recent'`

- [ ] **Step 3: 實作**

在 `daily_report.py` 的 `_kitchen_reminder()` **之後**新增:

```python
def _spending_recent():
    """最近一天的消費明細 + 本月累計。沒有任何支出資料就回 None。

    刻意不是「昨天」：國泰消費彙整信每天彙整前一日，早上 7 點推播時昨天的
    資料還沒進 Notion。寫死「昨日」會每天都是空的（見 spec 第 2 節）。
    """
    import finance_report
    import notion_db

    if not notion_db.is_configured():
        return None

    txns = notion_db.transactions_load()
    return finance_report.format_latest_day_spending(txns, today_tpe())
```

`today_tpe` 已在 `daily_report.py` 頂部 import,不用再加。

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_daily_spending.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_daily_spending.py daily_report.py
git commit -m "feat(daily): 加 _spending_recent 取最近一天消費"
```

---

### Task 7: carousel bubble

**Files:**
- Modify: `flex_builder.py:312-371`
- Test: `tests/test_flex_carousel.py`

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_flex_carousel.py`:

```python
def test_spending_bubble_appears_when_text_given():
    msg = flex_builder.daily_report_carousel(
        extra_text="小知識", weather_text="晴天", premarket_text="盤前",
        today_str="2026-08-13", spending_text="8/12　NT$1,290　3 筆",
    )
    assert "💳 最近一天消費" in _titles(msg)


def test_spending_bubble_absent_when_none():
    msg = flex_builder.daily_report_carousel(
        extra_text="小知識", weather_text="晴天", premarket_text="盤前",
        today_str="2026-08-13",
    )
    assert "💳 最近一天消費" not in _titles(msg)


def test_spending_bubble_is_last():
    """消費是回顧性資訊，優先度最低，排在盤前後面。"""
    msg = flex_builder.daily_report_carousel(
        extra_text="小知識", weather_text="晴天", premarket_text="盤前",
        today_str="2026-08-13", spending_text="8/12　NT$1,290　3 筆",
    )
    titles = _titles(msg)
    assert titles[-1] == "💳 最近一天消費"


def test_existing_callers_without_spending_still_work():
    msg = flex_builder.daily_report_carousel("a", "b", "c", "2026-08-13")
    assert msg is not None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_flex_carousel.py -v`
Expected: FAIL — `TypeError: daily_report_carousel() got an unexpected keyword argument 'spending_text'`

- [ ] **Step 3: 實作**

`flex_builder.py:312-313` 的簽名改成:

```python
def daily_report_carousel(extra_text, weather_text, premarket_text, today_str,
                          kitchen_text=None, spending_text=None):
```

docstring 的「順序」那行改成:

```
    順序：今日一則 → 食材提醒 → 天氣 → 盤前 → 消費。全部缺回 None。
```

在 premarket bubble 那段(`flex_builder.py:354-361`)**之後**、`if not bubbles:` **之前**插入:

```python
    # 排最後：食材是今天要動手的事，消費是回顧，優先度最低
    if spending_text:
        bubbles.append(text_bubble(
            title="💳 最近一天消費",
            subtitle=today_str,
            body=spending_text,
            header_color="#A66F6F",
        ))
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_flex_carousel.py tests/test_daily_kitchen.py -v`
Expected: PASS(含既有的 kitchen carousel 測試,確認沒被打壞)

- [ ] **Step 5: Commit**

```bash
git add tests/test_flex_carousel.py flex_builder.py
git commit -m "feat(flex): 每日 carousel 加最近一天消費 bubble"
```

---

### Task 8: 接進 `run_daily_report()`

**Files:**
- Modify: `daily_report.py:64-101`
- Test: `tests/test_daily_spending.py`

- [ ] **Step 1: 寫失敗的測試**

追加:

```python
def test_run_daily_report_passes_spending_to_carousel(use_notion, monkeypatch):
    """整條線接起來：Notion 有交易 → carousel 收到 spending_text。"""
    import flex_builder

    use_notion(txns=[_txn("2026-08-12", 839, "全聯")])
    monkeypatch.setattr(daily_report, "today_tpe", lambda: date(2026, 8, 13))

    captured = {}

    def fake_carousel(*args, **kwargs):
        captured.update(kwargs)
        return {"type": "flex", "contents": {}}

    monkeypatch.setattr(daily_report, "daily_report_carousel", fake_carousel)
    monkeypatch.setattr(daily_report, "get_weather_report", lambda: ("晴天", None))
    monkeypatch.setattr(daily_report, "build_premarket_report", lambda force=False: None)
    monkeypatch.setattr(daily_report.humor, "get_daily_extra", lambda: "小知識")

    sent = []

    async def fake_push(msg):
        sent.append(msg)

    monkeypatch.setattr(daily_report, "push_message", fake_push)

    import asyncio
    asyncio.run(daily_report.run_daily_report())

    assert "全聯" in captured["spending_text"]
    assert sent, "應該有推出去"


def test_run_daily_report_survives_spending_failure(use_notion, monkeypatch):
    """消費那段炸了不能拖垮整則推播 —— 天氣跟盤前還是要出得去。"""
    monkeypatch.setattr(daily_report, "today_tpe", lambda: date(2026, 8, 13))

    def boom():
        raise RuntimeError("notion 掛了")

    monkeypatch.setattr(daily_report, "_spending_recent", boom)
    monkeypatch.setattr(daily_report, "notify_admin", lambda *a, **k: None)
    monkeypatch.setattr(daily_report, "get_weather_report", lambda: ("晴天", None))
    monkeypatch.setattr(daily_report, "build_premarket_report", lambda force=False: None)
    monkeypatch.setattr(daily_report.humor, "get_daily_extra", lambda: "小知識")

    captured = {}

    def fake_carousel(*args, **kwargs):
        captured.update(kwargs)
        return {"type": "flex", "contents": {}}

    monkeypatch.setattr(daily_report, "daily_report_carousel", fake_carousel)

    sent = []

    async def fake_push(msg):
        sent.append(msg)

    monkeypatch.setattr(daily_report, "push_message", fake_push)

    import asyncio
    asyncio.run(daily_report.run_daily_report())

    assert captured["spending_text"] is None
    assert sent, "消費段失敗仍要推出去"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_daily_spending.py -k run_daily -v`
Expected: FAIL — `KeyError: 'spending_text'`

- [ ] **Step 3: 實作**

`daily_report.py` 的第 4 段(食材提醒)之後加第 5 段:

```python
    # 5. 最近一天消費（沒有任何支出資料就回 None，不佔 bubble）
    spending_text = _safe("消費摘要", _spending_recent)
```

carousel 呼叫改成:

```python
    carousel = daily_report_carousel(extra_text, weather_text, premarket_text, today,
                                     kitchen_text=kitchen_text,
                                     spending_text=spending_text)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_daily_spending.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_daily_spending.py daily_report.py
git commit -m "feat(daily): 每日推播接上最近一天消費"
```

---

### Task 9: 全套測試 + 更新文件

**Files:**
- Modify: `docs/HANDOFF.md`

- [ ] **Step 1: 跑全部測試**

Run: `python -m pytest -q`
Expected: 全部 PASS,總數比原本的 222 多(新增約 20 個)

- [ ] **Step 2: 更新 HANDOFF**

`docs/HANDOFF.md` 第 5 節把「1️⃣ 每日推播加『昨天花了多少』〔下一個要做〕」整段替換成:

```markdown
### 1️⃣ 每日推播加「最近一天消費」〔✅ 已完成〕

早上 7 點推播多一個 bubble：最近一天的消費明細 + 本月累計。

⚠️ **不是「昨天」** —— 國泰消費彙整信每天彙整前一日，早上推播時昨天的資料
還沒進 Notion，寫死「昨日」會每天都是空的。改成顯示資料裡最新的那一天並
寫出實際日期；超過 3 天沒新資料會講明可能是沒刷卡或同步中斷。

規格：`docs/superpowers/specs/2026-08-13-daily-spending-bubble-design.md`
```

同時把第 3 節「真實環境驗證狀態」表格加一列:

```markdown
| 每日推播的消費摘要 | ❌ 沒在真實推播裡看過（單元測試齊全） |
```

- [ ] **Step 3: Commit**

```bash
git add docs/HANDOFF.md
git commit -m "docs: HANDOFF 更新最近一天消費的完成狀態"
```

---

## 完成後

分支 `feat/daily-spending-bubble` 上會有 8 個 commit。**先不要合併 main** ——
main 推上去等於直接上線。合併前請使用者確認要不要現在部署。
