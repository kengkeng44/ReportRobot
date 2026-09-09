# 三餐補記提醒 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天晚上檢查前一天缺哪幾餐，缺才推播；使用者按一顆按鈕、講一句話就把三餐花費記進交易明細。

**Architecture:** 三個新單元，責任分明 —— `meal_track.py` 只做純邏輯（餐別推斷、缺餐計算、AI 回覆解析），不碰網路；`personal.py` 管對話待命狀態與寫入；`meal_reminder.py` 是每晚跑的排程任務，把前兩者接起來並推播。資料層在交易明細加「時間」「餐別」兩欄，把國泰 parser 一直有解析、卻在寫入時被丟掉的授權時間接上。

**Tech Stack:** Python 3、pytest、Notion API（`notion-client`）、LINE Messaging API、APScheduler、Anthropic SDK（僅在 `_ai` 接縫後面）

**Spec:** `docs/superpowers/specs/2026-09-09-meal-tracking-reminder-design.md`

---

## 開工前必讀（三條硬規則）

違反其中任何一條都會產生**不會報錯但會壞掉**的結果，請先讀完再動手。

### 1. 行尾：所有要改的既有檔案都是 CRLF

| 檔案 | 工作目錄行尾 |
|---|---|
| `notion_db.py`、`personal.py`、`command_router.py`、`flex_builder.py`、`prompts.py`、`finance_sync.py`、`tests/*.py` | **CRLF** |
| `server.py` | **LF** |

git 存的是 LF（checkout 時 autocrlf 轉成 CRLF）。用 `cat >>` 或 Python 的 `"\n"` 附加內容會在檔案中間插入裸 LF，產生混合行尾 —— **git diff 看起來正常，但整支檔案會被標成全部改過**。

附加內容到 CRLF 檔案的正確作法：

```python
block = '''
def new_function():
    return 1
'''
with open("personal.py", "a", encoding="utf-8", newline="") as f:
    f.write(block.replace("\n", "\r\n"))
```

改完一定要驗：

```bash
python -c "d=open('personal.py','rb').read(); print('CRLF',d.count(b'\r\n'),'bare LF',d.count(b'\n')-d.count(b'\r\n'))"
```

`bare LF` 必須是 0。**改 `server.py` 時反過來**：它是純 LF，`bare LF` 應該等於總行數、`CRLF` 應該是 0。

新建的檔案（`meal_track.py`、`meal_reminder.py`、`tests/test_meal_*.py`）用 LF 寫出即可，git 會處理。

### 2. 主控台是 CP950，不是 UTF-8

這台機器的 PowerShell 預設碼頁是 CP950。`print()` 中文到主控台會變亂碼，**但檔案內容是好的**。看到 `�w�ץ���` 這種東西不要以為壞了 —— 去檢查檔案，不要檢查主控台輸出。

要讓輸出可讀就先設 `$env:PYTHONIOENCODING="utf-8"`。

### 3. commit message 用 bash heredoc，不要用 PowerShell here-string

Bash 工具跑的是 Git Bash。PowerShell 的 `@'...'@` 在裡面會變成字面的 `@` 字元，直接混進 commit message。

正確作法：

```bash
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: 標題

內文。
MSG
```

### 4. 本機沒有 NOTION_TOKEN，測試不會碰網路

`python -m pytest` 直接跑，不需要 `infisical run`。所有測試都必須是純邏輯，或把 `_ai` / `notion_db` 整個換掉。**任何需要網路才會過的測試都是寫錯了。**

---

## File Structure

| 檔案 | 責任 | 動作 |
|---|---|---|
| `meal_track.py` | 純邏輯：時間 → 餐別、哪幾餐該有、缺哪幾餐、AI 回覆解析。**不碰 Notion、不碰 LINE** | 新建（Task 3-5, 7） |
| `meal_reminder.py` | 每晚排程本體：讀昨天交易 → 算缺餐 → 組卡片 → 推播 | 新建（Task 13） |
| `notion_db.py` | 交易明細 schema 加「時間」「餐別」；寫入與讀回 | 改（Task 1-2） |
| `finance_sync.py` | 把 parser 的 `time` 往下傳，寫入前套 `infer_meal` | 改（Task 6） |
| `prompts.py` | `MEAL_PARSE_PROMPT` | 改（Task 7） |
| `personal.py` | `_PENDING_MEAL` 待命狀態 + `record_meals` 寫入 | 改（Task 8-9） |
| `flex_builder.py` | `meal_prompt_flex` 推播卡片 | 改（Task 10） |
| `command_router.py` | postback `meal_add_start` / `meal_skip`；待命攔截 | 改（Task 11-12） |
| `server.py` | `MEAL_CRON` 排程註冊 | 改（Task 14） |

測試檔：`tests/test_meal_track.py`（Task 3-5, 7）、`tests/test_personal_meal.py`（Task 8-9）、`tests/test_meal_reminder.py`（Task 13）、既有 `tests/test_notion_db.py`（Task 1-2）。

**分支：** 開工前先開分支，不要直接在 main 上做。

```bash
git -C C:/Users/acer/projects/ReportRobot checkout -b feat/meal-tracking-reminder
```

---

## Task 1: 交易明細加「時間」「餐別」兩欄

國泰 parser 從一開始就解析了授權時間（`parsers/cathay_daily.py:136` 的 `"time"`），但 `transaction_add` 的欄位清單裡沒有它，值在寫入 Notion 的那一刻就消失了。這個 task 把欄位建起來。

**Files:**
- Modify: `notion_db.py:226-253`（`"交易明細"` schema）、`notion_db.py:1409-1433`（`transaction_add` 的 `candidates`）
- Test: `tests/test_notion_db.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_notion_db.py` 結尾（記得 CRLF，見開工前必讀第 1 條）：

```python
def test_transaction_schema_has_time_and_meal():
    """時間與餐別要在 schema 裡，_ensure_properties 才會補到既有 DB 上。"""
    import notion_db
    props = notion_db.DB_SCHEMAS["交易明細"]
    assert "時間" in props
    assert "餐別" in props
    names = [o["name"] for o in props["餐別"]["select"]["options"]]
    assert names == ["早餐", "午餐", "晚餐"]


def test_transaction_add_omits_time_and_meal_when_absent():
    """沒帶就不寫這兩欄 —— 硬填會把「不知道」偽裝成「已判斷」。"""
    import notion_db
    props = notion_db._transaction_properties({
        "date": "2026-09-08", "amount": 120, "total": 120,
        "shop": "午餐", "fingerprint": "x1",
    })
    assert "時間" not in props
    assert "餐別" not in props


def test_transaction_add_writes_time_and_meal_when_given():
    import notion_db
    props = notion_db._transaction_properties({
        "date": "2026-09-08", "amount": 120, "total": 120,
        "shop": "午餐", "fingerprint": "x2",
        "time": "12:34", "meal": "午餐",
    })
    assert props["時間"]["rich_text"][0]["text"]["content"] == "12:34"
    assert props["餐別"]["select"]["name"] == "午餐"
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_notion_db.py -q -k "time_and_meal"
```

預期：FAIL。前兩個是 `KeyError: '時間'` / `AttributeError: module 'notion_db' has no attribute '_transaction_properties'`。

- [ ] **Step 3: schema 加兩欄**

在 `notion_db.py` 的 `"交易明細"` 區塊，`"Fingerprint"` 那一行**之前**插入：

```python
        # 國泰 parser 一直有解析授權時間（parsers/cathay_daily.py:136），
        # 但這裡沒有對應欄位，值在寫入時就蒸發了。有時間才分得出
        # 12:30 那筆是午餐、19:40 那筆是晚餐。
        "時間": {"rich_text": {}},                              # HH:MM，24 小時制
        # 「類別」回答「在哪買」，「餐別」回答「這是哪一餐」——
        # 兩個維度不互相取代：早餐買超商的那筆，類別是超市∕量販、餐別是早餐。
        "餐別": _select(("早餐", "yellow"), ("午餐", "orange"), ("晚餐", "blue")),
```

- [ ] **Step 4: 把 `transaction_add` 的欄位組裝抽成純函式**

現在 `transaction_add` 把「組 properties」和「打 API」混在一起，測不到組裝結果。把組裝抽出來 —— 這也讓後面幾個 task 的測試不用碰網路。

在 `notion_db.py` 的 `def transaction_add(` **之前**插入新函式：

```python
def _transaction_properties(txn):
    """交易 dict → Notion properties。純轉換，不打 API（所以測得到）。

    值為 None 的欄位整個不送。這是既有慣例：沒帶類別就不寫類別，
    硬填「其他」會把「不知道」偽裝成「已分類」。
    """
    title = txn.get("shop") or txn.get("category") or "消費"
    candidates = {
        "摘要": {"title": [{"text": {"content": title}}]},
        "日期": {"date": {"start": txn["date"]}},
        "金額": _prop_number(txn.get("amount")),
        # 沒給幣別就當台幣：手動記帳與既有資料都不會帶這欄
        "幣別": _prop_select(txn.get("currency") or "TWD"),
        "消費地區": _prop_select(txn.get("region")),
        "卡末四碼": ({"rich_text": [{"text": {"content": txn["card_last4"]}}]}
                     if txn.get("card_last4") else None),
        "方向": _prop_select(txn.get("direction")),
        # 正規化擋在這裡，所有來源（國泰、手動記帳、日後新 parser）一併受保護。
        # 沒帶類別就維持不寫這欄。
        "類別": (_prop_select(normalize_spend_category(txn["category"]))
                 if txn.get("category") else None),
        "商店": {"rich_text": [{"text": {"content": txn.get("shop") or ""}}]},
        "狀態": _prop_select(txn.get("status")),
        "來源": _prop_select(txn.get("source")),
        # 沒帶就不寫這兩欄 —— 國泰同步走的是同一個函式，硬填「個人」
        # 會把「這個來源沒有分攤概念」偽裝成「已經判斷過是個人」。
        "分攤類型": _prop_select(txn.get("split_type")),
        "原始總額": _prop_number(txn.get("total")),
        # 時間可能是空字串（parser 抓不到授權時間）—— 空字串跟沒有一樣，
        # 寫一個空的 rich_text 只會讓 Notion 上多一欄看起來有值的空白。
        "時間": ({"rich_text": [{"text": {"content": txn["time"]}}]}
                 if txn.get("time") else None),
        "餐別": _prop_select(txn.get("meal")),
        "Fingerprint": {"rich_text": [{"text": {"content": txn["fingerprint"]}}]},
    }
    if txn.get("mail_url"):
        candidates["原信連結"] = {"url": txn["mail_url"]}
    return {k: v for k, v in candidates.items() if v is not None}
```

- [ ] **Step 5: 讓 `transaction_add` 改用新函式**

把 `transaction_add` 裡從 `title = txn.get("shop")` 到 `props = {k: v for k, v in candidates.items() if v is not None}` 的整段（原 `notion_db.py:1409-1436`）換成一行：

```python
    props = _transaction_properties(txn)
```

`transaction_add` 開頭的 `db_id = get_or_create_db("交易明細")` 與結尾的 `client.pages.create(...)` 都不動。

- [ ] **Step 6: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_notion_db.py -q
```

預期：全部 PASS。

- [ ] **Step 7: 跑全套確認沒打壞既有行為**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q 2>&1 | tail -3
```

預期：`1270 passed` 以上（新增的測試會讓數字往上）。

- [ ] **Step 8: 驗行尾**

```bash
cd C:/Users/acer/projects/ReportRobot && python -c "
for p in ('notion_db.py','tests/test_notion_db.py'):
    d=open(p,'rb').read()
    print(p,'CRLF',d.count(b'\r\n'),'bare LF',d.count(b'\n')-d.count(b'\r\n'))"
```

預期：兩個檔案的 `bare LF` 都是 0。

- [ ] **Step 9: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add notion_db.py tests/test_notion_db.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: 交易明細加「時間」「餐別」兩欄

國泰 parser 從一開始就解析了授權時間（parsers/cathay_daily.py:136），
但 transaction_add 沒有對應欄位，值在寫入 Notion 的那一刻就消失了。
沒有時間就分不出 12:30 那筆是午餐、19:40 那筆是晚餐。

順手把「組 properties」從 transaction_add 抽成 _transaction_properties，
組裝邏輯因此測得到，不必為了驗一個欄位去 mock Notion client。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 2: `transactions_load` 讀回時間與餐別

寫進去了但讀不回來，下游只會安靜地拿到 `None` —— 跟「那天沒有餐別」長得一模一樣。

**Files:**
- Modify: `notion_db.py:1515-1535`（`transactions_load` 的 `out.append`）
- Test: `tests/test_transactions_load.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_transactions_load.py` 結尾：

```python
def test_transactions_load_reads_time_and_meal(monkeypatch):
    """讀不回來的話，下游拿到的 None 跟「那天沒有餐別」無法區分。"""
    import notion_db

    page = {"properties": {
        "日期": {"date": {"start": "2026-09-08"}},
        "金額": {"number": 120},
        "商店": {"rich_text": [{"plain_text": "午餐"}]},
        "時間": {"rich_text": [{"plain_text": "12:34"}]},
        "餐別": {"select": {"name": "午餐"}},
    }}

    class _FakeDatabases:
        def query(self, **kwargs):
            return {"results": [page], "has_more": False}

    class _FakeClient:
        databases = _FakeDatabases()

    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db1")
    monkeypatch.setattr(notion_db, "_get_client", lambda: _FakeClient())

    rows = notion_db.transactions_load(limit=10)
    assert rows[0]["time"] == "12:34"
    assert rows[0]["meal"] == "午餐"


def test_transactions_load_missing_time_and_meal_are_none(monkeypatch):
    """遷移前的資料沒有這兩欄。空字串要收斂成 None，下游才好判斷。"""
    import notion_db

    page = {"properties": {
        "日期": {"date": {"start": "2026-08-15"}},
        "金額": {"number": 688},
    }}

    class _FakeDatabases:
        def query(self, **kwargs):
            return {"results": [page], "has_more": False}

    class _FakeClient:
        databases = _FakeDatabases()

    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db1")
    monkeypatch.setattr(notion_db, "_get_client", lambda: _FakeClient())

    rows = notion_db.transactions_load(limit=10)
    assert rows[0]["time"] is None
    assert rows[0]["meal"] is None
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_transactions_load.py -q -k "time_and_meal"
```

預期：FAIL，`KeyError: 'time'`。

- [ ] **Step 3: 讀回兩個欄位**

在 `notion_db.py` 的 `transactions_load` 裡，`out.append({...})` 那個 dict 中，`"split_type"` 那一行**之前**插入：

```python
                    # 空字串收斂成 None：遷移前的資料沒有這兩欄，
                    # _read_rich_text 會回空字串，而 "" 跟「不知道」
                    # 在下游要走不同分支。
                    "time": _read_rich_text(props, "時間") or None,
                    "meal": _read_select(props, "餐別") or None,
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_transactions_load.py -q
```

預期：全部 PASS。

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add notion_db.py tests/test_transactions_load.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: transactions_load 讀回時間與餐別

空字串收斂成 None —— 遷移前的資料沒有這兩欄，_read_rich_text 會回
空字串，而「空字串」跟「不知道」在下游要走不同分支。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 3: `meal_track.infer_meal` —— 時間推餐別

**Files:**
- Create: `meal_track.py`
- Test: `tests/test_meal_track.py`

- [ ] **Step 1: 寫失敗的測試**

新建 `tests/test_meal_track.py`：

```python
"""meal_track 純邏輯測試。不碰 Notion、不碰 LINE、不碰 AI。"""

import meal_track


# ── infer_meal ────────────────────────────────────────

def test_restaurant_in_morning_is_breakfast():
    assert meal_track.infer_meal("餐飲", "美而美", "07:40") == "早餐"


def test_restaurant_at_noon_is_lunch():
    assert meal_track.infer_meal("餐飲", "１０１美食街", "12:15") == "午餐"


def test_restaurant_in_evening_is_dinner():
    assert meal_track.infer_meal("餐飲", "爭鮮迴轉壽司", "19:40") == "晚餐"


def test_afternoon_tea_is_not_a_meal():
    """14:30–16:30 之間刻意留白。硬塞進三餐會讓晚餐的數字失去意義。"""
    assert meal_track.infer_meal("餐飲", "星巴克", "15:20") is None


def test_late_night_is_not_a_meal():
    assert meal_track.infer_meal("餐飲", "滷味", "23:10") is None


def test_boundary_1030_is_lunch():
    """10:30 整算午餐 —— 早餐區間是「小於 10:30」。"""
    assert meal_track.infer_meal("餐飲", "小吃店", "10:30") == "午餐"


def test_boundary_1429_is_lunch_1430_is_nothing():
    assert meal_track.infer_meal("餐飲", "小吃店", "14:29") == "午餐"
    assert meal_track.infer_meal("餐飲", "小吃店", "14:30") is None


def test_convenience_store_in_morning_is_breakfast():
    """使用者早餐常在超商解決而且有刷卡。不認的話會被追問，
    然後手動再記一筆 —— 同一筆錢記兩次。"""
    assert meal_track.infer_meal("超市∕量販", "全家便利商店－板橋廣榮店",
                                 "07:12") == "早餐"


def test_convenience_store_at_noon_is_not_a_meal():
    """午晚餐時段的超商更可能是飲料、日用品，不算一餐。"""
    assert meal_track.infer_meal("超市∕量販", "全家便利商店－板橋廣榮店",
                                 "12:40") is None


def test_supermarket_in_morning_is_not_a_meal():
    """全聯是採買不是早餐。只有便利商店適用這條規則。"""
    assert meal_track.infer_meal("超市∕量販", "全聯福利中心－板橋板新",
                                 "08:30") is None


def test_no_time_means_no_meal():
    """遷移前的資料沒有時間。猜一個餐別比留空更糟。"""
    assert meal_track.infer_meal("餐飲", "爭鮮", None) is None
    assert meal_track.infer_meal("餐飲", "爭鮮", "") is None


def test_malformed_time_means_no_meal():
    assert meal_track.infer_meal("餐飲", "爭鮮", "晚上七點") is None
    assert meal_track.infer_meal("餐飲", "爭鮮", "25:99") is None


def test_shopping_is_never_a_meal():
    assert meal_track.infer_meal("一般購物", "誠品生活", "12:30") is None
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_track.py -q
```

預期：FAIL，`ModuleNotFoundError: No module named 'meal_track'`。

- [ ] **Step 3: 建立 `meal_track.py`**

```python
"""三餐追蹤的純邏輯：時間推餐別、哪幾餐該有、缺哪幾餐、AI 回覆解析。

這個模組刻意**不碰 Notion、不碰 LINE、不碰 AI**（AI 只在檔案末端的
`_ai` 接縫後面）—— 只做決策，I/O 由呼叫端負責。測試因此不需要
mock 任何東西，跟 phrasebook.py 同一套手法。

Spec: docs/superpowers/specs/2026-09-09-meal-tracking-reminder-design.md
"""

import re
from datetime import time

# 三餐時段。界線之間刻意留白：14:30–16:30 是下午茶、22:00 之後是宵夜，
# 兩者都不算正餐。硬塞進三餐會讓「這天晚餐花 1,200」這種數字失去意義。
BREAKFAST_END = time(10, 30)
LUNCH_START, LUNCH_END = time(10, 30), time(14, 30)
DINNER_START, DINNER_END = time(16, 30), time(22, 0)

MEALS = ("早餐", "午餐", "晚餐")

# 用子字串比對而非完全相等：國泰給的店名是「全家便利商店－板橋廣榮店」，
# 分店會變但「全家」不會。跟 finance_report._SHARED_SHOPS 同一個風格。
_CVS_KEYWORDS = ("全家", "統一超商", "萊爾富", "ＯＫ超商", "OK超商")

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _parse_hhmm(hhmm):
    """"12:34" → time(12, 34)。認不出來回 None，不 raise。

    認不出來的來源有兩種：遷移前的資料沒有時間欄（空字串），以及
    parser 遇到格式外的內容。兩種都該安靜地回「不知道」——
    這裡 raise 會讓一筆髒資料炸掉整晚的排程。
    """
    m = _TIME_RE.match((hhmm or "").strip())
    if not m:
        return None
    return time(int(m.group(1)), int(m.group(2)))


def _is_convenience_store(shop):
    return any(k in (shop or "") for k in _CVS_KEYWORDS)


def infer_meal(category, shop, hhmm):
    """這筆消費是哪一餐。判斷不出來回 None。

    兩條規則：
    1. 類別是「餐飲」且時段命中 → 對應餐別
    2. 便利商店且時段在早餐區間 → 早餐

    第 2 條是因為使用者早餐常在超商解決而且有刷卡。不認的話系統會
    判定「缺早餐」→ 推播追問 → 使用者手動再記一筆，同一筆錢記兩次。

    **刻意不改類別。** 早餐買超商那筆的類別仍然是「超市∕量販」——
    類別回答「在哪買」，餐別回答「這是哪一餐」，改類別會讓便利商店
    支出從報表上憑空消失。
    """
    t = _parse_hhmm(hhmm)
    if t is None:
        return None

    if category == "餐飲":
        if t < BREAKFAST_END:
            return "早餐"
        if LUNCH_START <= t < LUNCH_END:
            return "午餐"
        if DINNER_START <= t < DINNER_END:
            return "晚餐"
        return None

    if _is_convenience_store(shop) and t < BREAKFAST_END:
        return "早餐"

    return None
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_track.py -q
```

預期：13 passed。

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add meal_track.py tests/test_meal_track.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: meal_track.infer_meal 由時間推餐別

早餐時段的便利商店消費算早餐（使用者早餐常在超商且有刷卡），
但不改類別 —— 類別回答「在哪買」，餐別回答「這是哪一餐」，
改類別會讓便利商店支出從報表上憑空消失。

14:30–16:30 與 22:00 之後刻意留白：下午茶和宵夜不算正餐。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 4: `meal_track.meals_needed` —— 那天該有哪幾餐

**Files:**
- Modify: `meal_track.py`
- Test: `tests/test_meal_track.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_meal_track.py`：

```python
# ── meals_needed ──────────────────────────────────────

from datetime import date


def test_weekday_skips_lunch():
    """平日午餐由使用者自己煮 —— 固定行為，問了只是噪音。"""
    monday = date(2026, 9, 7)
    assert monday.weekday() == 0
    assert meal_track.meals_needed(monday) == ("早餐", "晚餐")


def test_friday_still_skips_lunch():
    friday = date(2026, 9, 11)
    assert friday.weekday() == 4
    assert meal_track.meals_needed(friday) == ("早餐", "晚餐")


def test_saturday_needs_all_three():
    saturday = date(2026, 9, 12)
    assert saturday.weekday() == 5
    assert meal_track.meals_needed(saturday) == ("早餐", "午餐", "晚餐")


def test_sunday_needs_all_three():
    sunday = date(2026, 9, 13)
    assert sunday.weekday() == 6
    assert meal_track.meals_needed(sunday) == ("早餐", "午餐", "晚餐")
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_track.py -q -k "meals_needed or weekday or friday or saturday or sunday"
```

預期：FAIL，`AttributeError: module 'meal_track' has no attribute 'meals_needed'`。

- [ ] **Step 3: 實作**

附加到 `meal_track.py` 結尾：

```python
def meals_needed(day):
    """那一天該有哪幾餐。回 tuple。

    平日午餐由使用者自己煮 —— 固定行為，問了只是噪音，所以整個
    不列入。**也刻意不寫一筆 0 元紀錄**：0 元會污染餐飲筆數統計，
    而且因為系統從頭到尾就不問平日午餐，「自煮」和「漏記」本來
    就不可區分，寫 0 元並沒有換回任何資訊。
    """
    if day.weekday() < 5:          # 週一(0) ~ 週五(4)
        return ("早餐", "晚餐")
    return MEALS
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_track.py -q
```

預期：17 passed。

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add meal_track.py tests/test_meal_track.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: meal_track.meals_needed 平日兩餐、假日三餐

平日午餐自煮，不列入也不寫 0 元紀錄 —— 0 元會污染餐飲筆數統計，
而且系統從頭到尾就不問平日午餐，「自煮」和「漏記」本來就不可區分。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 5: `meal_track.missing_meals` —— 缺哪幾餐

**Files:**
- Modify: `meal_track.py`
- Test: `tests/test_meal_track.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_meal_track.py`：

```python
# ── missing_meals ─────────────────────────────────────

def _row(meal=None, category="餐飲", shop="", hhmm=None):
    return {"meal": meal, "category": category, "shop": shop, "time": hhmm}


def test_nothing_recorded_means_everything_missing():
    monday = date(2026, 9, 7)
    assert meal_track.missing_meals(monday, []) == ("早餐", "晚餐")


def test_recorded_meal_is_not_missing():
    monday = date(2026, 9, 7)
    rows = [_row(meal="早餐")]
    assert meal_track.missing_meals(monday, rows) == ("晚餐",)


def test_all_recorded_means_nothing_missing():
    monday = date(2026, 9, 7)
    rows = [_row(meal="早餐"), _row(meal="晚餐")]
    assert meal_track.missing_meals(monday, rows) == ()


def test_lunch_on_a_weekday_does_not_break_anything():
    """平日午餐沒被問，但使用者出去吃了也照樣記得下 —— 不該報錯。"""
    monday = date(2026, 9, 7)
    rows = [_row(meal="早餐"), _row(meal="午餐"), _row(meal="晚餐")]
    assert meal_track.missing_meals(monday, rows) == ()


def test_weekend_needs_lunch_too():
    saturday = date(2026, 9, 12)
    rows = [_row(meal="早餐"), _row(meal="晚餐")]
    assert meal_track.missing_meals(saturday, rows) == ("午餐",)


def test_rows_without_meal_field_are_inferred():
    """既有資料沒有餐別欄位，但有時間就推得出來 —— 不推的話會
    把已經在帳上的餐再問一次。"""
    monday = date(2026, 9, 7)
    rows = [_row(category="餐飲", shop="美而美", hhmm="07:40")]
    assert meal_track.missing_meals(monday, rows) == ("晚餐",)


def test_rows_with_neither_meal_nor_time_are_ignored():
    """遷移前的資料兩個都沒有。當作沒有紀錄，寧可多問一次。"""
    monday = date(2026, 9, 7)
    rows = [_row(category="餐飲", shop="爭鮮")]
    assert meal_track.missing_meals(monday, rows) == ("早餐", "晚餐")


def test_order_follows_the_day_not_the_rows():
    """回傳順序永遠是早、午、晚 —— 卡片上的順序不該取決於刷卡順序。"""
    saturday = date(2026, 9, 12)
    assert meal_track.missing_meals(saturday, []) == ("早餐", "午餐", "晚餐")
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_track.py -q -k "missing"
```

預期：FAIL，`AttributeError: module 'meal_track' has no attribute 'missing_meals'`。

- [ ] **Step 3: 實作**

附加到 `meal_track.py` 結尾：

```python
def meal_of(row):
    """一筆交易屬於哪一餐。優先讀「餐別」欄，沒有就從時間推。

    先讀欄位再推斷，是因為手動補記的那些筆本來就直接寫了餐別，
    而它們沒有時間（使用者不會講「我 7:40 吃早餐」）。
    """
    return row.get("meal") or infer_meal(
        row.get("category"), row.get("shop"), row.get("time")
    )


def missing_meals(day, rows):
    """那一天還缺哪幾餐。全都有就回空 tuple（呼叫端據此決定不推播）。

    rows 是那一天的交易明細。順序永遠照 早→午→晚，不跟著刷卡順序跑 ——
    卡片上「還缺：晚餐、早餐」讀起來像壞掉。
    """
    have = {meal_of(r) for r in rows}
    return tuple(m for m in meals_needed(day) if m not in have)
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_track.py -q
```

預期：25 passed。

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add meal_track.py tests/test_meal_track.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: meal_track.missing_meals 算出那天還缺哪幾餐

meal_of 先讀「餐別」欄再從時間推：手動補記的筆直接寫了餐別但沒有
時間（使用者不會講「我 7:40 吃早餐」），國泰同步的則相反。

回傳順序固定早→午→晚，不跟著刷卡順序跑。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 6: `finance_sync` 把時間傳下去並套 `infer_meal`

前五個 task 建好了欄位和推斷邏輯，但沒有人把它們接起來 —— 國泰同步寫進去的每一筆時間仍然是空的。

**Files:**
- Modify: `finance_sync.py:113-127`（寫入前的政策套用段）
- Test: `tests/test_finance_sync.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_finance_sync.py` 結尾：

```python
def test_apply_meal_rule_fills_meal_from_time():
    """parser 給了 time，寫入前要推出餐別 —— 這是「錢算哪一餐」的政策，
    不是「信件怎麼讀」的解析，所以套在寫入端而不是 parser 裡。"""
    import finance_sync

    txn = {"category": "餐飲", "shop": "爭鮮迴轉壽司", "time": "19:40"}
    out = finance_sync.apply_meal_rule(txn)
    assert out["meal"] == "晚餐"


def test_apply_meal_rule_leaves_meal_absent_when_undecidable():
    """推不出來就不要寫這個 key —— 硬填會把「不知道」偽裝成「已判斷」。"""
    import finance_sync

    txn = {"category": "一般購物", "shop": "誠品生活", "time": "12:30"}
    out = finance_sync.apply_meal_rule(txn)
    assert "meal" not in out


def test_apply_meal_rule_without_time_is_a_noop():
    import finance_sync

    txn = {"category": "餐飲", "shop": "爭鮮"}
    out = finance_sync.apply_meal_rule(txn)
    assert "meal" not in out
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_finance_sync.py -q -k "meal_rule"
```

預期：FAIL，`AttributeError: module 'finance_sync' has no attribute 'apply_meal_rule'`。

- [ ] **Step 3: 實作 `apply_meal_rule`**

在 `finance_sync.py` 的 `def sync(` **之前**插入：

```python
def apply_meal_rule(txn):
    """寫入前補上「餐別」。推不出來就不加這個 key。

    套在寫入端而不是 parser 裡，跟 apply_shared_rule 同一個理由：
    這是「錢算哪一餐」的政策，不是「信件怎麼讀」的解析。混在一起
    之後兩邊都難改。

    推不出來時**不寫這個 key**（而不是寫 None）—— transaction_add
    對 None 和缺鍵的處理相同，但缺鍵讀起來就是「這裡沒有意見」。
    """
    import meal_track

    meal = meal_track.infer_meal(
        txn.get("category"), txn.get("shop"), txn.get("time")
    )
    if meal:
        txn["meal"] = meal
    return txn
```

- [ ] **Step 4: 在同步迴圈裡套用**

在 `finance_sync.py` 的 `sync()` 裡，把

```python
                txn = finance_report.apply_shared_rule(txn)
```

改成

```python
                txn = finance_report.apply_shared_rule(txn)
                # parser 一直有解析授權時間，直到現在才有欄位收它。
                # 兩條政策都套在這裡，parser 維持只負責讀信。
                txn = apply_meal_rule(txn)
```

- [ ] **Step 5: 確認 parser 的 `time` 真的到得了這裡**

`parsers/cathay_daily.py:136` 已經把 `"time"` 放進每一筆 txn dict，`sync()` 直接拿 parser 的輸出，中間沒有欄位過濾。跑這個檢查確認：

```bash
cd C:/Users/acer/projects/ReportRobot && python -c "
import parsers.cathay_daily as p
import inspect
src = inspect.getsource(p)
assert '\"time\": time_text' in src, 'parser 沒有 time 欄位了，計畫要重看'
print('parser 有 time 欄位')"
```

預期：印出 `parser 有 time 欄位`。

- [ ] **Step 6: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_finance_sync.py tests/test_meal_track.py -q
```

預期：全部 PASS。

- [ ] **Step 7: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q 2>&1 | tail -3
```

預期：全部 PASS。

- [ ] **Step 8: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add finance_sync.py tests/test_finance_sync.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: 國泰同步寫入前補上餐別

apply_meal_rule 套在寫入端而不是 parser 裡，跟 apply_shared_rule
同一個理由：這是「錢算哪一餐」的政策，不是「信件怎麼讀」的解析。

推不出來就不寫這個 key，不寫 None —— 缺鍵讀起來就是「這裡沒有意見」。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 7: AI 解析使用者那句話

**Files:**
- Modify: `prompts.py`（附加 `MEAL_PARSE_PROMPT`）、`meal_track.py`（附加 `parse_meal_reply` 與 `_ai`）
- Test: `tests/test_meal_track.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_meal_track.py`：

```python
# ── parse_meal_reply（AI 接縫整個換掉，不 mock SDK）──────

def _fake_ai(answer):
    return lambda prompt, max_tokens=128: answer


def test_parses_two_meals(monkeypatch):
    monkeypatch.setattr(meal_track, "_ai", _fake_ai('{"早餐": 60, "晚餐": 200}'))
    out = meal_track.parse_meal_reply("早餐 60 晚餐 200", ("早餐", "晚餐"))
    assert out == {"早餐": 60, "晚餐": 200}


def test_parses_one_meal_only(monkeypatch):
    """只講一餐就只記一餐。沒講到的不補、不猜。"""
    monkeypatch.setattr(meal_track, "_ai", _fake_ai('{"晚餐": 200}'))
    out = meal_track.parse_meal_reply("晚餐兩百", ("早餐", "晚餐"))
    assert out == {"晚餐": 200}


def test_keeps_a_meal_that_was_not_asked_for(monkeypatch):
    """平日沒問午餐，但使用者說他出去吃了 —— 他比規則清楚那天發生什麼事。"""
    monkeypatch.setattr(meal_track, "_ai", _fake_ai('{"午餐": 150}'))
    out = meal_track.parse_meal_reply("午餐 150", ("早餐", "晚餐"))
    assert out == {"午餐": 150}


def test_drops_unknown_meal_names(monkeypatch):
    """「宵夜」不是三餐之一。丟掉而不是寫進去 —— 餐別欄是 select，
    未定義的值會讓 Notion 自己擴充 schema。"""
    monkeypatch.setattr(meal_track, "_ai",
                        _fake_ai('{"晚餐": 200, "宵夜": 80}'))
    out = meal_track.parse_meal_reply("晚餐200 宵夜80", ("晚餐",))
    assert out == {"晚餐": 200}


def test_drops_non_positive_amounts(monkeypatch):
    """0 和負數不是花費。那一餐跳過，其他照記。"""
    monkeypatch.setattr(meal_track, "_ai",
                        _fake_ai('{"早餐": 0, "晚餐": 200}'))
    out = meal_track.parse_meal_reply("早餐沒吃 晚餐200", ("早餐", "晚餐"))
    assert out == {"晚餐": 200}


def test_invalid_json_returns_none(monkeypatch):
    """解析失敗回 None，呼叫端據此請使用者重講。絕不猜金額 ——
    記錯的數字比沒記更糟：空白看得出來，錯的數字看不出來。"""
    monkeypatch.setattr(meal_track, "_ai", _fake_ai("我不知道你在說什麼"))
    assert meal_track.parse_meal_reply("嗯", ("早餐",)) is None


def test_json_wrapped_in_code_fence_still_parses(monkeypatch):
    """模型常把 JSON 包在 ```json 裡。這不算失敗。"""
    monkeypatch.setattr(meal_track, "_ai",
                        _fake_ai('```json\n{"晚餐": 200}\n```'))
    assert meal_track.parse_meal_reply("晚餐200", ("晚餐",)) == {"晚餐": 200}


def test_empty_result_returns_none(monkeypatch):
    """全部被過濾掉之後等於什麼都沒解析到。"""
    monkeypatch.setattr(meal_track, "_ai", _fake_ai('{"宵夜": 80}'))
    assert meal_track.parse_meal_reply("宵夜80", ("晚餐",)) is None


def test_ai_exception_returns_none(monkeypatch):
    """AI 掛掉不該讓整個對話炸掉。"""
    def _boom(prompt, max_tokens=128):
        raise RuntimeError("API down")
    monkeypatch.setattr(meal_track, "_ai", _boom)
    assert meal_track.parse_meal_reply("早餐60", ("早餐",)) is None


def test_float_amounts_are_rounded_to_int(monkeypatch):
    """台幣沒有小數。60.0 是合法的，60.4 也收下取整。"""
    monkeypatch.setattr(meal_track, "_ai", _fake_ai('{"早餐": 60.0}'))
    assert meal_track.parse_meal_reply("早餐60", ("早餐",)) == {"早餐": 60}
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_track.py -q -k "parse or ai or json or amount or meal_name"
```

預期：FAIL，`AttributeError: module 'meal_track' has no attribute '_ai'`。

- [ ] **Step 3: 加 prompt**

附加到 `prompts.py` 結尾（記得 CRLF）：

```python
MEAL_PARSE_PROMPT = """使用者在補記昨天的三餐花費。

系統問他的是這幾餐：{asked}

他說：
{text}

請抽出每一餐的金額，回一個 JSON 物件，key 是餐別、value 是新台幣整數。

規則：
- 只回 JSON，不要解釋，不要 markdown 圍籬
- 沒講到的餐就不要出現在 JSON 裡
- 他講了沒被問到的餐（例如系統只問早餐、晚餐，他卻提到午餐），照樣收下
- 說「沒吃」「沒花」「零」的餐，不要放進 JSON
- 金額看不出來的餐，不要放進 JSON。**絕對不要猜數字**

範例輸入：早餐 60 晚餐兩百
範例輸出：{{"早餐": 60, "晚餐": 200}}"""
```

- [ ] **Step 4: 實作解析與接縫**

附加到 `meal_track.py` 結尾：

```python
# ─────────────────────────────────────────────────────────
# I/O 邊界：上面全是純邏輯，以下開始碰 AI
#
# _ai 存在的唯一理由是讓測試整個換掉它 —— phrasebook.py、todo_parse.py
# 同一套手法。不然每個測試都要 mock Anthropic SDK。
# ─────────────────────────────────────────────────────────

AI_MODEL = "claude-sonnet-4-5"

_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_meal_reply(text, asked):
    """使用者那句話 → {餐別: 金額}。解析不出任何一餐回 None。

    asked 是系統問的那幾餐，只用來給 AI 當上下文 —— **不拿來過濾**：
    平日沒問午餐，但使用者說他出去吃了，他比規則清楚那天發生什麼事。

    過濾的是別的東西：不在三餐之內的餐別（「宵夜」）會被丟掉，因為
    「餐別」是 Notion 的 select 欄位，送未定義的值會讓 Notion 自己
    擴充 schema 而不是報錯。非正整數也丟掉。

    回 None 而不是空 dict：呼叫端要據此請使用者重講。
    **絕不猜金額** —— 記錯的數字比沒記更糟，空白看得出來，錯的看不出來。
    """
    import json

    from prompts import MEAL_PARSE_PROMPT

    try:
        raw = _ai(MEAL_PARSE_PROMPT.format(
            asked="、".join(asked), text=text,
        ))
    except Exception as e:
        print(f"[meal_track] AI 解析失敗：{e}")
        return None

    # 模型常把 JSON 包在 ```json 圍籬裡。抓最外層的大括號就好。
    m = _JSON_RE.search(raw or "")
    if not m:
        print(f"[meal_track] AI 回覆裡沒有 JSON：{(raw or '')[:80]}")
        return None

    try:
        data = json.loads(m.group(0))
    except ValueError:
        print(f"[meal_track] AI 回覆不是合法 JSON：{m.group(0)[:80]}")
        return None

    if not isinstance(data, dict):
        return None

    out = {}
    for meal, amount in data.items():
        if meal not in MEALS:
            continue
        try:
            value = int(round(float(amount)))
        except (TypeError, ValueError):
            continue
        if value > 0:
            out[meal] = value

    return out or None


def _ai(prompt, max_tokens=128):
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

- [ ] **Step 5: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_track.py -q
```

預期：35 passed。

- [ ] **Step 6: 驗 prompts.py 行尾**

```bash
cd C:/Users/acer/projects/ReportRobot && python -c "
d=open('prompts.py','rb').read()
print('CRLF',d.count(b'\r\n'),'bare LF',d.count(b'\n')-d.count(b'\r\n'))"
```

預期：`bare LF 0`。

- [ ] **Step 7: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add prompts.py meal_track.py tests/test_meal_track.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: AI 解析三餐補記那句話

_ai 接縫讓測試整個換掉它，不必 mock Anthropic SDK（同 phrasebook.py
與 todo_parse.py）。

asked 只當 AI 的上下文，不拿來過濾 —— 平日沒問午餐但使用者說他出去
吃了，他比規則清楚那天發生什麼事。真正過濾的是三餐以外的餐別：
「餐別」是 select 欄位，送未定義的值會讓 Notion 自己擴充 schema。

解析不出來回 None 請使用者重講，絕不猜金額 —— 記錯的數字比沒記更糟，
空白看得出來，錯的數字看不出來。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 8: `personal._PENDING_MEAL` 待命狀態

跟待辦的 `_PENDING_TODO` 同一套，但兩者不能同時待命 —— 使用者按了「補記三餐」又去按「➕ 新增待辦」，下一句話該記成哪個？後按的贏。

**Files:**
- Modify: `personal.py:38-42`（`_PENDING_TODO` 附近）、`personal.py:90-112`（三個 pending 函式附近）
- Test: `tests/test_personal_meal.py`

- [ ] **Step 1: 寫失敗的測試**

新建 `tests/test_personal_meal.py`：

```python
"""三餐補記的待命狀態。不碰 Notion（本機沒有 NOTION_TOKEN）。"""

from datetime import timedelta

import personal


def _reset():
    personal._PENDING_MEAL.clear()
    personal._PENDING_TODO.clear()


def test_start_and_read_pending_meal():
    _reset()
    personal.start_pending_meal("U1", "2026-09-08", ("早餐", "晚餐"))
    assert personal.pending_meal("U1") == {
        "date": "2026-09-08", "meals": ("早餐", "晚餐"),
    }


def test_not_pending_returns_none():
    _reset()
    assert personal.pending_meal("U1") is None


def test_clear_pending_meal():
    _reset()
    personal.start_pending_meal("U1", "2026-09-08", ("早餐",))
    personal.clear_pending_meal("U1")
    assert personal.pending_meal("U1") is None


def test_clear_when_not_pending_does_not_raise():
    _reset()
    personal.clear_pending_meal("U1")


def test_pending_meal_times_out():
    """沒有逾時的話，一個忘掉的待命會把隔天隨口講的話變成三餐紀錄。"""
    _reset()
    personal.start_pending_meal("U1", "2026-09-08", ("早餐",))
    stale = personal._PENDING_MEAL["U1"]["started"] - timedelta(
        minutes=personal.PENDING_MEAL_TIMEOUT_MINUTES + 1)
    personal._PENDING_MEAL["U1"]["started"] = stale
    assert personal.pending_meal("U1") is None


def test_timed_out_entry_is_swept():
    """逾時的順手掃掉，不然 dict 會一直長。"""
    _reset()
    personal.start_pending_meal("U1", "2026-09-08", ("早餐",))
    stale = personal._PENDING_MEAL["U1"]["started"] - timedelta(
        minutes=personal.PENDING_MEAL_TIMEOUT_MINUTES + 1)
    personal._PENDING_MEAL["U1"]["started"] = stale
    personal.pending_meal("U1")
    assert "U1" not in personal._PENDING_MEAL


def test_starting_a_meal_cancels_a_pending_todo():
    """兩個待命同時開著的話，下一句話該記成哪個？後按的贏。"""
    _reset()
    personal.start_pending_todo("U1")
    personal.start_pending_meal("U1", "2026-09-08", ("早餐",))
    assert personal.is_pending_todo("U1") is False


def test_starting_a_todo_cancels_a_pending_meal():
    _reset()
    personal.start_pending_meal("U1", "2026-09-08", ("早餐",))
    personal.start_pending_todo("U1")
    assert personal.pending_meal("U1") is None


def test_two_users_do_not_interfere():
    _reset()
    personal.start_pending_meal("U1", "2026-09-08", ("早餐",))
    personal.start_pending_meal("U2", "2026-09-08", ("晚餐",))
    personal.clear_pending_meal("U1")
    assert personal.pending_meal("U2")["meals"] == ("晚餐",)
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_personal_meal.py -q
```

預期：FAIL，`AttributeError: module 'personal' has no attribute '_PENDING_MEAL'`。

- [ ] **Step 3: 加狀態容器**

在 `personal.py` 的 `PENDING_TODO_TIMEOUT_MINUTES = 10` 那一行**之後**插入（記得 CRLF）：

```python

# ────────────────────────────────────────
# 三餐補記「待命」狀態：使用者按了「➕ 補記」，下一句話就是三餐花費
#
# user_id → {"date": "2026-09-08", "meals": ("早餐", "晚餐"), "started": ...}
# 比待辦多存了日期與缺的餐別：推播問的是**昨天**，而使用者可能過了
# 午夜才回。存下來才不會把昨天的早餐記到今天。
#
# 跟 _PENDING_TODO 一樣不進 Notion：壽命只有幾秒。
# ────────────────────────────────────────

_PENDING_MEAL = {}

PENDING_MEAL_TIMEOUT_MINUTES = 15
```

- [ ] **Step 4: 加三個存取函式**

在 `personal.py` 的 `def add_todo(` **之前**插入：

```python
def start_pending_meal(user_id, day_iso, meals):
    """進入三餐待命：下一句話當那天的三餐花費。

    會先取消待辦待命 —— 兩個同時開著的話，下一句話該記成哪個沒有
    好答案，所以規則定死：**後按的贏**。
    """
    with _LOCK:
        _PENDING_TODO.pop(user_id, None)
        _PENDING_MEAL[user_id] = {
            "date": day_iso,
            "meals": tuple(meals),
            "started": now_tpe(),
        }


def clear_pending_meal(user_id):
    """離開三餐待命。不在待命中也不會炸。"""
    with _LOCK:
        _PENDING_MEAL.pop(user_id, None)


def pending_meal(user_id):
    """待命中且未逾時回 {"date", "meals"}，否則 None。逾時的順手掃掉。

    逾時比待辦長（15 分鐘 vs 10 分鐘）：推播是晚上主動送的，使用者
    可能正在吃飯、洗澡，不像按待辦那樣人就在螢幕前。
    """
    with _LOCK:
        entry = _PENDING_MEAL.get(user_id)
        if not entry:
            return None
        if now_tpe() - entry["started"] > timedelta(
                minutes=PENDING_MEAL_TIMEOUT_MINUTES):
            _PENDING_MEAL.pop(user_id, None)
            return None
        return {"date": entry["date"], "meals": entry["meals"]}
```

- [ ] **Step 5: 讓待辦待命也取消三餐待命**

在 `personal.py` 的 `start_pending_todo` 裡，把

```python
    with _LOCK:
        _PENDING_TODO[user_id] = now_tpe()
```

改成

```python
    with _LOCK:
        # 三餐待命同時開著的話，下一句話該記成哪個沒有好答案 ——
        # 規則定死：後按的贏。
        _PENDING_MEAL.pop(user_id, None)
        _PENDING_TODO[user_id] = now_tpe()
```

- [ ] **Step 6: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_personal_meal.py -q
```

預期：10 passed。

- [ ] **Step 7: 驗行尾並跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -c "
d=open('personal.py','rb').read()
print('CRLF',d.count(b'\r\n'),'bare LF',d.count(b'\n')-d.count(b'\r\n'))" && python -m pytest -q 2>&1 | tail -3
```

預期：`bare LF 0`，測試全部 PASS。

- [ ] **Step 8: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add personal.py tests/test_personal_meal.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: personal._PENDING_MEAL 三餐補記待命狀態

比待辦待命多存日期與缺的餐別：推播問的是昨天，而使用者可能過了
午夜才回，不存下來就會把昨天的早餐記到今天。

兩個待命互斥，後按的贏 —— 同時開著的話「下一句話記成哪個」沒有
好答案，與其猜不如定死。

逾時 15 分鐘（待辦是 10）：推播是晚上主動送的，使用者可能正在
吃飯洗澡，不像按待辦那樣人就在螢幕前。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 9: `meal_reminder.record_meals` —— 把三餐寫進交易明細

**Files:**
- Create: `meal_reminder.py`
- Test: `tests/test_meal_reminder.py`

- [ ] **Step 1: 寫失敗的測試**

新建 `tests/test_meal_reminder.py`：

```python
"""三餐補記的寫入與排程。notion_db 整個換掉，不碰網路。"""

import meal_reminder


class _FakeNotion:
    def __init__(self):
        self.added = []

    def transaction_add(self, txn):
        self.added.append(txn)
        return f"page{len(self.added)}"


def test_record_meals_writes_one_row_per_meal(monkeypatch):
    fake = _FakeNotion()
    monkeypatch.setattr(meal_reminder, "_store", lambda: fake)

    n = meal_reminder.record_meals("2026-09-08", {"早餐": 60, "晚餐": 200})

    assert n == 2
    assert len(fake.added) == 2
    shops = sorted(t["shop"] for t in fake.added)
    assert shops == ["早餐", "晚餐"]


def test_recorded_rows_carry_the_right_fields(monkeypatch):
    fake = _FakeNotion()
    monkeypatch.setattr(meal_reminder, "_store", lambda: fake)

    meal_reminder.record_meals("2026-09-08", {"晚餐": 200})
    txn = fake.added[0]

    assert txn["date"] == "2026-09-08"
    assert txn["amount"] == 200
    assert txn["total"] == 200
    assert txn["meal"] == "晚餐"
    assert txn["category"] == "餐飲"
    assert txn["direction"] == "支出"
    assert txn["source"] == "手動"
    assert txn["status"] == "已結帳"
    assert txn["split_type"] == "個人"
    assert txn["fingerprint"]


def test_recorded_rows_have_no_time(monkeypatch):
    """使用者不會講「我 7:40 吃早餐」。餐別直接寫，時間留空。"""
    fake = _FakeNotion()
    monkeypatch.setattr(meal_reminder, "_store", lambda: fake)

    meal_reminder.record_meals("2026-09-08", {"早餐": 60})
    assert "time" not in fake.added[0]


def test_fingerprints_differ_between_meals(monkeypatch):
    """同一天兩餐同價的話，指紋一樣就會被去重吃掉一筆。"""
    fake = _FakeNotion()
    monkeypatch.setattr(meal_reminder, "_store", lambda: fake)

    meal_reminder.record_meals("2026-09-08", {"早餐": 100, "晚餐": 100})
    prints = {t["fingerprint"] for t in fake.added}
    assert len(prints) == 2


def test_empty_meals_writes_nothing(monkeypatch):
    fake = _FakeNotion()
    monkeypatch.setattr(meal_reminder, "_store", lambda: fake)

    assert meal_reminder.record_meals("2026-09-08", {}) == 0
    assert fake.added == []


def test_write_failure_does_not_lose_the_other_meals(monkeypatch):
    """一餐寫失敗不該讓另一餐跟著消失。"""
    class _HalfBroken:
        def __init__(self):
            self.added = []

        def transaction_add(self, txn):
            if txn["shop"] == "早餐":
                raise RuntimeError("Notion 掛了")
            self.added.append(txn)
            return "page1"

    fake = _HalfBroken()
    monkeypatch.setattr(meal_reminder, "_store", lambda: fake)

    n = meal_reminder.record_meals("2026-09-08", {"早餐": 60, "晚餐": 200})
    assert n == 1
    assert [t["shop"] for t in fake.added] == ["晚餐"]
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_reminder.py -q
```

預期：FAIL，`ModuleNotFoundError: No module named 'meal_reminder'`。

- [ ] **Step 3: 建立 `meal_reminder.py`**

```python
"""三餐補記：每晚檢查前一天缺哪幾餐並推播，以及把補記寫進交易明細。

純邏輯在 meal_track.py，這裡只做 I/O：讀 Notion、組卡片、推 LINE。

Spec: docs/superpowers/specs/2026-09-09-meal-tracking-reminder-design.md
"""

MEAL_CATEGORY = "餐飲"


def _store():
    """notion_db 的間接層。存在的唯一理由是讓測試整個換掉它 ——
    phrasebook.py 同一套手法。"""
    import notion_db
    return notion_db


def record_meals(day_iso, meals):
    """把 {餐別: 金額} 寫進交易明細。回實際寫成功的筆數。

    一餐一筆，不合併成一筆「三餐 380」—— 合併之後就再也分不出
    早餐花多少，而「哪一餐最貴」正是這整套要回答的問題。

    一筆寫失敗不影響其他筆：Notion 偶爾會超時，讓另外兩餐跟著消失
    是最沒有必要的損失。
    """
    import finance_report

    if not meals:
        return 0

    notion = _store()
    written = 0
    # 照三餐順序寫，Notion 上的建立順序才讀得懂
    import meal_track
    for meal in meal_track.MEALS:
        amount = meals.get(meal)
        if not amount:
            continue
        txn = {
            "date": day_iso,
            "amount": amount,          # 三餐預設個人，我負擔 = 全額
            "total": amount,
            "split_type": "個人",
            "shop": meal,              # 摘要就叫「早餐」——事後看得懂
            "meal": meal,
            "category": MEAL_CATEGORY,
            "direction": "支出",
            "status": "已結帳",         # 手動輸入就是最終金額，不需要對帳
            "source": "手動",
            # 指紋帶餐別：同一天兩餐同價的話，不帶就會被去重吃掉一筆。
            "fingerprint": finance_report.make_manual_fingerprint(
                day_iso, amount, meal),
        }
        try:
            if notion.transaction_add(txn):
                written += 1
        except Exception as e:
            print(f"[meal] 寫入 {day_iso} {meal} 失敗：{e}")
    return written
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_reminder.py -q
```

預期：6 passed。

指紋那題會過，是因為 `make_manual_fingerprint(day, amount, shop, split_type=None)`
的第三個參數這裡傳的就是餐別 —— 早餐 100 與晚餐 100 的雜湊來源分別是
`手動|日期|100|早餐` 與 `手動|日期|100|晚餐`，本來就不同。若這題紅了，
表示 `finance_report.py:327` 的簽章變了，回頭對一次。

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add meal_reminder.py tests/test_meal_reminder.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: meal_reminder.record_meals 把三餐寫進交易明細

一餐一筆，不合併成「三餐 380」—— 合併之後就再也分不出早餐花多少，
而「哪一餐最貴」正是這整套要回答的問題。

一筆寫失敗不影響其他筆：Notion 偶爾超時，讓另外兩餐跟著消失是最
沒有必要的損失。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 10: `flex_builder.meal_prompt_flex` —— 推播卡片

**Files:**
- Modify: `flex_builder.py`（附加在 `todo_due_prompt_flex` 之後）
- Test: `tests/test_flex_builder.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_flex_builder.py` 結尾：

```python
def test_meal_prompt_flex_lists_missing_meals():
    import flex_builder

    msg = flex_builder.meal_prompt_flex("2026-09-08", ("早餐", "晚餐"),
                                        weekday_lunch_skipped=True)
    blob = str(msg)
    assert "早餐" in blob
    assert "晚餐" in blob
    assert "9/08" in blob


def test_meal_prompt_flex_mentions_self_cooked_lunch_on_weekdays():
    import flex_builder

    msg = flex_builder.meal_prompt_flex("2026-09-08", ("早餐", "晚餐"),
                                        weekday_lunch_skipped=True)
    assert "自煮" in str(msg)


def test_meal_prompt_flex_omits_lunch_note_on_weekend():
    import flex_builder

    msg = flex_builder.meal_prompt_flex("2026-09-12",
                                        ("早餐", "午餐", "晚餐"),
                                        weekday_lunch_skipped=False)
    assert "自煮" not in str(msg)


def test_meal_prompt_flex_uses_postback_buttons():
    """message 型按鈕會在對話裡留下一句話，而那句話會再被指令解析
    甚至待命攔截處理一次。"""
    import flex_builder

    blob = str(flex_builder.meal_prompt_flex("2026-09-08", ("早餐",),
                                             weekday_lunch_skipped=True))
    assert "meal_add_start" in blob
    assert "meal_skip" in blob
    assert "\"type\": \"message\"" not in blob.replace("'", '"')


def test_meal_prompt_flex_carries_the_date_in_postback():
    """推播是昨天的事，使用者可能過午夜才按 —— 日期要跟著按鈕走。"""
    import flex_builder

    blob = str(flex_builder.meal_prompt_flex("2026-09-08", ("早餐",),
                                             weekday_lunch_skipped=True))
    assert "2026-09-08" in blob
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_flex_builder.py -q -k "meal_prompt"
```

預期：FAIL，`AttributeError: module 'flex_builder' has no attribute 'meal_prompt_flex'`。

- [ ] **Step 3: 實作**

在 `flex_builder.py` 的 `def _see_all_button():` **之前**插入：

```python
def meal_prompt_flex(day_iso, missing, weekday_lunch_skipped):
    """「昨天還缺哪幾餐」的推播卡。

    用 postback 而不是 message 型按鈕，理由同 todo_due_prompt_flex：
    message 會在對話裡留下一句話，而那句話會再被指令解析（甚至待命
    攔截）處理一次。

    日期跟著 postback 走：推播是昨天的事，使用者可能過了午夜才按，
    到時候「昨天」已經是前天了。
    """
    month, day = day_iso[5:7], day_iso[8:10]
    lines = [{
        "type": "text",
        "text": f"🍽 {month}/{day} 還缺：" + "、".join(missing),
        "size": "sm", "weight": "bold", "color": _TEXT_DARK, "wrap": True,
    }]
    if weekday_lunch_skipped:
        lines.append({
            "type": "text", "text": "午餐已預設自煮",
            "size": "xxs", "color": _TEXT_DARK, "wrap": True,
        })

    buttons = [
        {
            "type": "button", "style": "primary", "height": "sm", "margin": "md",
            "action": {
                "type": "postback",
                "label": "➕ 補記",
                # 缺的餐別跟著按鈕走。router 拿不到那天的交易明細，
                # 重算只能算出「該有哪幾餐」而不是「還缺哪幾餐」——
                # 那會讓 AI 的上下文多出使用者早就記過的餐。
                "data": _postback("meal_add_start", d=day_iso,
                                  m=",".join(missing)),
                "displayText": "➕ 補記三餐",
            },
        },
        {
            "type": "button", "style": "secondary", "height": "sm", "margin": "sm",
            "action": {
                "type": "postback",
                "label": "都沒花",
                "data": _postback("meal_skip", d=day_iso),
                "displayText": "都沒花",
            },
        },
    ]

    bubble = {
        "type": "bubble", "size": "kilo",
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "backgroundColor": _LIGHT_BG, "paddingAll": "lg",
            "contents": lines + buttons,
        },
    }
    return _wrap(bubble, alt=f"{month}/{day} 三餐補記")
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_flex_builder.py -q
```

預期：全部 PASS。

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add flex_builder.py tests/test_flex_builder.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: flex_builder.meal_prompt_flex 三餐補記推播卡

日期跟著 postback 走：推播是昨天的事，使用者可能過了午夜才按，
到時候「昨天」已經是前天了。

用 postback 而非 message 型按鈕，理由同 todo_due_prompt_flex ——
message 會在對話裡留下一句話，那句話會再被指令解析處理一次。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 11: postback `meal_add_start` / `meal_skip`

**Files:**
- Modify: `command_router.py:1223-1230`（`handle_postback` 的 action 分支）
- Test: `tests/test_command_router.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_command_router.py` 結尾：

```python
def test_meal_add_start_enters_pending():
    import command_router
    import personal

    personal._PENDING_MEAL.clear()
    reply = command_router.handle_postback(
        "action=meal_add_start&d=2026-09-08&m=%E6%97%A9%E9%A4%90", "U1")

    assert "請說" in reply
    entry = personal.pending_meal("U1")
    assert entry["date"] == "2026-09-08"
    assert entry["meals"] == ("早餐",)


def test_meal_add_start_without_date_is_rejected():
    """沒有日期就不知道要記到哪一天。寧可回一句錯誤，也不要猜今天。"""
    import command_router
    import personal

    personal._PENDING_MEAL.clear()
    reply = command_router.handle_postback("action=meal_add_start", "U1")

    assert personal.pending_meal("U1") is None
    assert "認不出" in reply


def test_meal_skip_clears_pending_and_writes_nothing():
    import command_router
    import personal

    personal.start_pending_meal("U1", "2026-09-08", ("早餐",))
    reply = command_router.handle_postback(
        "action=meal_skip&d=2026-09-08", "U1")

    assert personal.pending_meal("U1") is None
    assert "9/08" in reply
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_command_router.py -q -k "meal_add_start or meal_skip"
```

預期：FAIL —— `handle_postback` 對未知 action 回 None，斷言 `"請說" in None` 會 `TypeError`。

- [ ] **Step 3: 實作兩個分支**

在 `command_router.py` 的 `handle_postback` 裡，`if action == "todo_add_start":` 那一段**之前**插入：

```python
        if action == "meal_add_start":
            import personal
            from datetime import date as _date

            day_iso = (parsed.get("d") or [""])[0]
            try:
                _date.fromisoformat(day_iso)
            except ValueError:
                # 沒有日期就不知道要記到哪一天。寧可回一句錯誤，
                # 也不要猜「今天」—— 推播問的本來就是昨天。
                return "認不出那個日期，請重新按推播上的按鈕。"

            # 缺的餐別由推播卡帶過來。這裡拿不到那天的交易明細，
            # 自己重算只能得到「該有哪幾餐」，會把使用者早就記過的
            # 餐也塞進 AI 的上下文。
            missing = tuple(
                m for m in ((parsed.get("m") or [""])[0]).split(",") if m)
            personal.start_pending_meal(user_id, day_iso, missing)
            return ("請說。" + _NL + _NL
                    + "可以一句話講完，例如：" + _NL
                    + "　早餐 60 晚餐 200" + _NL + _NL
                    + f"（{personal.PENDING_MEAL_TIMEOUT_MINUTES} 分鐘內沒說就自動取消）")

        if action == "meal_skip":
            import personal

            day_iso = (parsed.get("d") or [""])[0]
            personal.clear_pending_meal(user_id)
            # 不寫任何紀錄：0 元會污染餐飲筆數統計（見 spec 2.3）
            label = f"{day_iso[5:7]}/{day_iso[8:10]}" if len(day_iso) == 10 else "那天"
            return f"好，{label} 不記錄。"
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_command_router.py -q
```

預期：全部 PASS。

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add command_router.py tests/test_command_router.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: 三餐補記的兩個 postback

meal_add_start 進待命，meal_skip 清掉待命且不寫任何紀錄
（0 元會污染餐飲筆數統計，見 spec 2.3）。

日期認不出來就回錯誤，不猜「今天」—— 推播問的本來就是昨天。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 12: 待命攔截 —— 使用者講的那句話

**Files:**
- Modify: `command_router.py:983-1036`（`_intercept_pending_todo` 與 `handle`）
- Test: `tests/test_command_router.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_command_router.py` 結尾：

```python
def _personal_ctx():
    return {"source_type": "user", "user_id": "U1"}


def test_spoken_meals_are_recorded(monkeypatch):
    import command_router
    import meal_reminder
    import meal_track
    import personal

    personal.start_pending_meal("U1", "2026-09-08", ("早餐", "晚餐"))
    monkeypatch.setattr(meal_track, "parse_meal_reply",
                        lambda text, asked: {"早餐": 60, "晚餐": 200})
    written = []

    def _fake_record(day, meals):
        written.append((day, meals))
        return len(meals)

    monkeypatch.setattr(meal_reminder, "record_meals", _fake_record)

    reply = command_router.handle("早餐 60 晚餐 200", _personal_ctx())

    assert written == [("2026-09-08", {"早餐": 60, "晚餐": 200})]
    assert "已記錄" in reply
    assert personal.pending_meal("U1") is None


def test_unparseable_reply_keeps_nothing_and_asks_again(monkeypatch):
    """解析不出來就請他重講，絕不猜金額。"""
    import command_router
    import meal_track
    import personal

    personal.start_pending_meal("U1", "2026-09-08", ("早餐",))
    monkeypatch.setattr(meal_track, "parse_meal_reply",
                        lambda text, asked: None)

    reply = command_router.handle("嗯嗯", _personal_ctx())

    assert "沒聽懂" in reply
    assert personal.pending_meal("U1") is None


def test_known_command_cancels_meal_pending_and_still_runs():
    """Rich Menu 的按鈕送的是純文字且不一定有斜線，所以判斷條件是
    「命中已知指令」而不是「開頭是 /」。"""
    import command_router
    import personal

    personal.start_pending_meal("U1", "2026-09-08", ("早餐",))
    result = command_router.handle("/額度", _personal_ctx())

    assert personal.pending_meal("U1") is None
    assert isinstance(result, list)
    assert result[0] == "已取消補記三餐。"


def test_empty_text_cancels_meal_pending():
    import command_router
    import personal

    personal.start_pending_meal("U1", "2026-09-08", ("早餐",))
    reply = command_router.handle("   ", _personal_ctx())

    assert "取消" in reply
    assert personal.pending_meal("U1") is None


def test_todo_pending_still_works_after_meal_intercept_added():
    """三餐攔截排在待辦攔截前面，不能把待辦那條路吃掉。"""
    import command_router
    import personal

    personal._PENDING_MEAL.clear()
    personal.start_pending_todo("U1")
    result = command_router.handle("/額度", _personal_ctx())

    assert isinstance(result, list)
    assert result[0] == "已取消新增待辦。"
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_command_router.py -q -k "spoken_meals or unparseable or meal_pending"
```

預期：FAIL —— 三餐待命中的話會被 `_intercept_pending_todo` 當成待辦記下來，或整段沒有攔截。

- [ ] **Step 3: 加三餐攔截函式**

在 `command_router.py` 的 `def _intercept_pending_todo(` **之前**插入：

```python
# 「解除三餐待命，但那個指令照常跑」的哨兵。跟 _PENDING_CANCELLED
# 分開，是因為回覆給使用者的那句話不一樣。
_MEAL_CANCELLED = object()


def _record_spoken_meals(user_id, text, entry):
    """三餐待命中收到的自由文字 → 寫進交易明細。回覆給 reply_message。"""
    import meal_reminder
    import meal_track

    meals = meal_track.parse_meal_reply(text, entry["meals"])
    if not meals:
        return ("沒聽懂金額，這次先不記。" + _NL + _NL
                + "再按一次推播上的「➕ 補記」，像這樣說：" + _NL
                + "　早餐 60 晚餐 200")

    written = meal_reminder.record_meals(entry["date"], meals)
    if not written:
        return "寫入失敗，這次沒記到。稍後再按一次「➕ 補記」。"

    day = entry["date"]
    detail = "、".join(f"{m} {meals[m]}" for m in ("早餐", "午餐", "晚餐")
                       if m in meals)
    return f"✅ 已記錄 {day[5:7]}/{day[8:10]}：{detail}"


def _intercept_pending_meal(text, ctx, parsed):
    """三餐待命中的訊息處理。回傳語意同 _intercept_pending_todo。

    排在待辦攔截**之前**：兩者互斥（start_pending_meal 會清掉待辦
    待命），所以順序其實不影響結果，但先檢查三餐讓「剛推播完」
    這個最常見的情境少走一次判斷。
    """
    import personal

    user_id = (ctx or {}).get("user_id")
    if not user_id or not _is_personal_chat(ctx):
        return None

    entry = personal.pending_meal(user_id)
    if not entry:
        return None

    personal.clear_pending_meal(user_id)

    if parsed:
        return _MEAL_CANCELLED
    if not (text or "").strip():
        return "沒聽到內容，取消補記三餐。"
    return _record_spoken_meals(user_id, text, entry)
```

- [ ] **Step 4: 在 `handle` 裡串上**

把 `command_router.py` 的 `handle` 函式主體（從 `parsed = parse(text)` 到 `return result`）換成：

```python
    parsed = parse(text)

    # 三餐待命與待辦待命互斥（start_pending_meal 會清掉待辦待命），
    # 所以這裡最多只有一個會回非 None。
    intercepted = _intercept_pending_meal(text, ctx, parsed)
    if intercepted is None:
        intercepted = _intercept_pending_todo(text, ctx, parsed)

    if (intercepted is not None
            and intercepted is not _PENDING_CANCELLED
            and intercepted is not _MEAL_CANCELLED):
        return intercepted

    result = _dispatch(text, ctx, parsed)

    if intercepted is _PENDING_CANCELLED:
        note = "已取消新增待辦。"
    elif intercepted is _MEAL_CANCELLED:
        note = "已取消補記三餐。"
    else:
        return result

    if result is None:
        return note
    return [note] + (result if isinstance(result, list) else [result])
```

- [ ] **Step 5: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_command_router.py -q
```

預期：全部 PASS。

- [ ] **Step 6: 跑全套並驗行尾**

```bash
cd C:/Users/acer/projects/ReportRobot && python -c "
d=open('command_router.py','rb').read()
print('CRLF',d.count(b'\r\n'),'bare LF',d.count(b'\n')-d.count(b'\r\n'))" && python -m pytest -q 2>&1 | tail -3
```

預期：`bare LF 0`，測試全部 PASS。

- [ ] **Step 7: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add command_router.py tests/test_command_router.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: 三餐待命攔截

哨兵分成 _PENDING_CANCELLED 與 _MEAL_CANCELLED 兩個：回覆給使用者
的那句話不一樣（「已取消新增待辦」vs「已取消補記三餐」）。

解析不出金額就請他重講，不寫任何東西 —— 記錯的數字比沒記更糟。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 13: 每晚的排程本體

**Files:**
- Modify: `meal_reminder.py`
- Test: `tests/test_meal_reminder.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_meal_reminder.py`：

```python
from datetime import date


def _yesterday_rows(rows):
    class _Fake:
        def transactions_load(self, limit=200):
            return rows

        def transaction_add(self, txn):
            return "p1"
    return _Fake()


def test_pushes_when_meals_are_missing(monkeypatch):
    """週一，那天完全沒有餐飲紀錄 → 該推。"""
    pushed = []
    monkeypatch.setattr(meal_reminder, "_store",
                        lambda: _yesterday_rows([]))
    monkeypatch.setattr(meal_reminder, "_push",
                        lambda uid, msg: pushed.append((uid, msg)))
    monkeypatch.setattr(meal_reminder, "_target_user", lambda: "U1")

    n = meal_reminder.run_nightly(today=date(2026, 9, 8))

    assert n == 1
    assert pushed and pushed[0][0] == "U1"


def test_stays_quiet_when_nothing_is_missing(monkeypatch):
    """週一該有早餐、晚餐，兩筆都在 → 不推。安靜是正確行為。"""
    rows = [
        {"date": "2026-09-07", "meal": "早餐", "category": "餐飲",
         "shop": "", "time": None},
        {"date": "2026-09-07", "meal": "晚餐", "category": "餐飲",
         "shop": "", "time": None},
    ]
    pushed = []
    monkeypatch.setattr(meal_reminder, "_store", lambda: _yesterday_rows(rows))
    monkeypatch.setattr(meal_reminder, "_push",
                        lambda uid, msg: pushed.append((uid, msg)))
    monkeypatch.setattr(meal_reminder, "_target_user", lambda: "U1")

    assert meal_reminder.run_nightly(today=date(2026, 9, 8)) == 0
    assert pushed == []


def test_only_yesterdays_rows_count(monkeypatch):
    """前天的早餐不能算進昨天。"""
    rows = [
        {"date": "2026-09-06", "meal": "早餐", "category": "餐飲",
         "shop": "", "time": None},
        {"date": "2026-09-07", "meal": "晚餐", "category": "餐飲",
         "shop": "", "time": None},
    ]
    pushed = []
    monkeypatch.setattr(meal_reminder, "_store", lambda: _yesterday_rows(rows))
    monkeypatch.setattr(meal_reminder, "_push",
                        lambda uid, msg: pushed.append(msg))
    monkeypatch.setattr(meal_reminder, "_target_user", lambda: "U1")

    meal_reminder.run_nightly(today=date(2026, 9, 8))
    assert "早餐" in str(pushed[0])


def test_convenience_store_breakfast_counts(monkeypatch):
    """早上的超商刷卡就是早餐。不認的話會被追問，然後同一筆錢記兩次。"""
    rows = [
        {"date": "2026-09-07", "meal": None, "category": "超市∕量販",
         "shop": "全家便利商店－板橋廣榮店", "time": "07:12"},
        {"date": "2026-09-07", "meal": None, "category": "餐飲",
         "shop": "自助餐", "time": "19:20"},
    ]
    pushed = []
    monkeypatch.setattr(meal_reminder, "_store", lambda: _yesterday_rows(rows))
    monkeypatch.setattr(meal_reminder, "_push",
                        lambda uid, msg: pushed.append(msg))
    monkeypatch.setattr(meal_reminder, "_target_user", lambda: "U1")

    assert meal_reminder.run_nightly(today=date(2026, 9, 8)) == 0
    assert pushed == []


def test_no_target_user_means_no_push(monkeypatch):
    """兩個 env 都沒設就安靜跳過，不要炸掉排程。"""
    pushed = []
    monkeypatch.setattr(meal_reminder, "_store", lambda: _yesterday_rows([]))
    monkeypatch.setattr(meal_reminder, "_push",
                        lambda uid, msg: pushed.append(msg))
    monkeypatch.setattr(meal_reminder, "_target_user", lambda: None)

    assert meal_reminder.run_nightly(today=date(2026, 9, 8)) == 0
    assert pushed == []


def test_notion_failure_does_not_raise(monkeypatch):
    """讀不到資料就跳過。排程炸掉會觸發 admin 通知，而「Notion 這分鐘
    不通」不值得一則 Error 推播。"""
    class _Broken:
        def transactions_load(self, limit=200):
            raise RuntimeError("Notion 掛了")

    monkeypatch.setattr(meal_reminder, "_store", lambda: _Broken())
    monkeypatch.setattr(meal_reminder, "_target_user", lambda: "U1")

    assert meal_reminder.run_nightly(today=date(2026, 9, 8)) == 0
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_reminder.py -q -k "run_nightly or pushes or quiet or yesterday or convenience or target_user or notion_failure"
```

預期：FAIL，`AttributeError: module 'meal_reminder' has no attribute 'run_nightly'`。

- [ ] **Step 3: 實作**

附加到 `meal_reminder.py` 結尾：

```python
# 一天最多幾筆交易。餐別判斷只看昨天，但 transactions_load 是照日期
# 新到舊撈全部 —— 200 筆大約涵蓋一個月，足夠找到昨天那幾筆。
LOOKBACK_ROWS = 200


def _target_user():
    """三餐提醒要推給誰。兩個 env 都沒設回 None。

    跟 daily_report._personal_user_id 同一套 fallback：這兩個變數在
    這台 bot 上永遠是同一個人。要人把同一串 U... 貼兩次，只會有一次
    忘了貼，然後整個功能靜悄悄地不動。
    """
    import os
    for name in ("PERSONAL_USER_ID", "ADMIN_LINE_USER_ID"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _push(user_id, message):
    import line_quota
    import line_sender

    line_sender.push_to_user_sync(user_id, message)
    line_quota.bump()


def run_nightly(today=None):
    """檢查前一天缺哪幾餐，缺就推播。回推播則數（0 或 1）。

    問**前一天**而不是今天：國泰「消費彙整通知」每天 14:2x–14:5x 送達、
    FINANCE_CRON 排在台北 15:30 同步。今天晚上問今天的話，今天的刷卡
    還沒進 Notion，系統會把「還沒同步」誤判成「沒有紀錄」，然後追問
    一堆使用者其實刷過的餐。

    任何一步失敗都回 0 而不是 raise：APScheduler 的 error listener 會
    把例外推成 admin 的 Error 通知，而「Notion 這分鐘不通」不值得一則。
    """
    from datetime import timedelta

    import flex_builder
    import meal_track
    from tz_utils import today_tpe

    today = today or today_tpe()
    day = today - timedelta(days=1)
    day_iso = day.isoformat()

    user_id = _target_user()
    if not user_id:
        print("[meal] PERSONAL_USER_ID / ADMIN_LINE_USER_ID 都沒設，跳過")
        return 0

    try:
        rows = _store().transactions_load(limit=LOOKBACK_ROWS)
    except Exception as e:
        print(f"[meal] 讀交易明細失敗，本次跳過：{e}")
        return 0

    yesterday = [r for r in rows if (r.get("date") or "") == day_iso]
    missing = meal_track.missing_meals(day, yesterday)
    if not missing:
        print(f"[meal] {day_iso} 三餐都有紀錄，不推播")
        return 0

    try:
        _push(user_id, flex_builder.meal_prompt_flex(
            day_iso, missing,
            weekday_lunch_skipped=day.weekday() < 5,
        ))
    except Exception as e:
        print(f"[meal] 推播失敗：{e}")
        return 0

    print(f"[meal] {day_iso} 缺 {'、'.join(missing)}，已推播")
    return 1
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_reminder.py -q
```

預期：12 passed。

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add meal_reminder.py tests/test_meal_reminder.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: meal_reminder.run_nightly 每晚檢查前一天缺哪幾餐

問前一天而不是今天：國泰彙整通知 14:2x–14:5x 送達、FINANCE_CRON
排在 15:30。今晚問今天的話，今天的刷卡還沒進 Notion，系統會把
「還沒同步」誤判成「沒有紀錄」，追問一堆使用者其實刷過的餐。

任何一步失敗都回 0 而不是 raise：APScheduler 的 error listener 會把
例外推成 admin 的 Error 通知，而「Notion 這分鐘不通」不值得一則。

_target_user 沿用 daily_report._personal_user_id 的 fallback ——
這兩個 env 在這台 bot 上永遠是同一個人。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 14: 掛上排程

**Files:**
- Modify: `server.py:53-54`（cron 常數附近）、`server.py:147-192`（`lifespan` 的 `add_job` 區）
- Test: `tests/test_server_cron.py`

**注意：`server.py` 是純 LF，不是 CRLF。** 改完驗證時 `CRLF` 應該是 0。

- [ ] **Step 1: 寫失敗的測試**

新建 `tests/test_server_cron.py`：

```python
"""MEAL_CRON 的格式與預設值。不啟動 FastAPI。"""

import importlib


def test_meal_cron_defaults_to_taipei_9pm(monkeypatch):
    """UTC 13:00 = 台北 21:00。國泰 15:30 同步完，資料已經齊了。"""
    monkeypatch.delenv("MEAL_CRON", raising=False)
    import server
    importlib.reload(server)
    assert server.MEAL_CRON == "0 13 * * *"


def test_bad_meal_cron_falls_back_to_default(monkeypatch):
    """env 打錯不該讓 startup crash —— 整個 bot 會起不來。"""
    monkeypatch.setenv("MEAL_CRON", "not a cron")
    import server
    importlib.reload(server)
    assert server.MEAL_CRON == "0 13 * * *"


def test_valid_meal_cron_is_honoured(monkeypatch):
    monkeypatch.setenv("MEAL_CRON", "30 14 * * *")
    import server
    importlib.reload(server)
    assert server.MEAL_CRON == "30 14 * * *"
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_server_cron.py -q
```

預期：FAIL，`AttributeError: module 'server' has no attribute 'MEAL_CRON'`。

- [ ] **Step 3: 加常數**

在 `server.py` 的 `FINANCE_CRON = _cron_or_default("FINANCE_CRON", "30 7 * * *")` 那一行**之後**插入（**LF，不是 CRLF**）：

```python
# UTC 13:00 = 台北 21:00。三餐提醒問的是**前一天** —— 國泰彙整通知
# 14:2x–14:5x 送達、FINANCE_CRON 15:30 同步，所以晚上問昨天的話
# 資料早就齊了。問今天則會把「還沒同步」誤判成「沒有紀錄」。
MEAL_CRON = _cron_or_default("MEAL_CRON", "0 13 * * *")
```

- [ ] **Step 4: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_server_cron.py -q
```

預期：3 passed。

- [ ] **Step 5: 註冊 job**

在 `server.py` 的 `lifespan` 裡，`# 即時警示（颱風 / 重要 Gmail）` 那一段**之前**插入：

```python
    # 三餐補記提醒：台灣 21:00。約 28 則 push/月，加上既有推播
    # 合計約 81/200，不會觸發 LINE 免費方案的 80% 警示。
    m_minute, m_hour, m_day, m_month, m_dow = MEAL_CRON.split()
    scheduler.add_job(
        _run_meal_reminder,
        CronTrigger(minute=m_minute, hour=m_hour, day=m_day,
                    month=m_month, day_of_week=m_dow),
        id="meal_reminder",
        max_instances=1,
        coalesce=True,
        # 漏跑一小時內補跑。超過就放棄 —— 半夜兩點才問「昨天吃什麼」
        # 比不問更糟。
        misfire_grace_time=3600,
        replace_existing=True,
    )
```

- [ ] **Step 6: 加 job 的包裝函式**

在 `server.py` 的 `@asynccontextmanager` 那一行**之前**插入：

```python
def _run_meal_reminder():
    """排程呼叫的入口。run_nightly 自己吞掉所有例外並回 0，
    所以這裡不需要 try —— 但保留這層是為了讓 job id 對應到一個
    看得懂名字的函式，而不是一個模組屬性。"""
    import meal_reminder
    return meal_reminder.run_nightly()
```

- [ ] **Step 7: 加 startup 訊息**

在 `server.py` 的 `print("LINE push 月配額警示：每天 09:00 TPE")` 那一行**之後**插入：

```python
    print(f"三餐補記提醒排程：{MEAL_CRON} (UTC)")
```

- [ ] **Step 8: 確認 server.py 仍是純 LF**

```bash
cd C:/Users/acer/projects/ReportRobot && python -c "
d=open('server.py','rb').read()
print('CRLF',d.count(b'\r\n'),'bare LF',d.count(b'\n')-d.count(b'\r\n'))"
```

預期：`CRLF 0`，`bare LF` 等於總行數。

- [ ] **Step 9: 確認 server 匯入得起來**

```bash
cd C:/Users/acer/projects/ReportRobot && python -c "import server; print('ok', server.MEAL_CRON)"
```

預期：`ok 0 13 * * *`

- [ ] **Step 10: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q 2>&1 | tail -3
```

預期：全部 PASS。

- [ ] **Step 11: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add server.py tests/test_server_cron.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: MEAL_CRON 排程（台北 21:00）

misfire_grace_time 設 3600：漏跑一小時內補跑，超過就放棄 ——
半夜兩點才問「昨天吃什麼」比不問更糟。

約 28 則 push/月，加上既有推播合計約 81/200，不會觸發 LINE 免費
方案的 80% 警示。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Task 15: 三餐也能分帳（「晚餐 400 共同」）

Spec 6.4 要求三餐沿用既有的分帳語法。Task 9 的 `record_meals` 目前寫死 `split_type="個人"`，這個 task 把它補上。

**分攤類型套用整句話，不是逐餐。** 「早餐 60 晚餐 400 共同」會把兩餐都記成共同。這是刻意的取捨：逐餐分攤要嘛讓使用者講「早餐60個人晚餐400共同」（沒人會這樣講），要嘛讓 AI 去猜哪一餐是共同（猜錯不會報錯，只會讓金額默默少一半）。一句一個分攤類型，講錯了自己看得出來。

**Files:**
- Modify: `meal_reminder.py`（`record_meals` 加參數）、`command_router.py`（`_record_spoken_meals` 先剝離分攤字）
- Test: `tests/test_meal_reminder.py`、`tests/test_command_router.py`

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_meal_reminder.py`：

```python
def test_shared_meal_records_only_my_half(monkeypatch):
    """「金額」欄的語意是「我實際負擔」，共同消費存分攤後的那半。
    改成存總額會讓六處既有報表全部高估，而且不會報錯。"""
    fake = _FakeNotion()
    monkeypatch.setattr(meal_reminder, "_store", lambda: fake)

    meal_reminder.record_meals("2026-09-08", {"晚餐": 400},
                               split_type="共同")
    txn = fake.added[0]

    assert txn["amount"] == 200      # 我負擔
    assert txn["total"] == 400       # 整桌
    assert txn["split_type"] == "共同"


def test_split_type_defaults_to_personal(monkeypatch):
    fake = _FakeNotion()
    monkeypatch.setattr(meal_reminder, "_store", lambda: fake)

    meal_reminder.record_meals("2026-09-08", {"晚餐": 400})
    txn = fake.added[0]

    assert txn["amount"] == 400
    assert txn["total"] == 400
    assert txn["split_type"] == "個人"


def test_shared_and_personal_same_amount_have_different_fingerprints(monkeypatch):
    """個人 200 與共同 400（分攤 200）的「金額」欄都是 200。
    指紋不加區別的話，第二筆會被去重擋掉。"""
    fake = _FakeNotion()
    monkeypatch.setattr(meal_reminder, "_store", lambda: fake)

    meal_reminder.record_meals("2026-09-08", {"晚餐": 200})
    meal_reminder.record_meals("2026-09-08", {"晚餐": 400},
                               split_type="共同")

    prints = {t["fingerprint"] for t in fake.added}
    assert len(prints) == 2


def test_odd_shared_amount_rounds_half_up(monkeypatch):
    """my_share_of 用四捨五入而非 banker's rounding —— 共同消費除以 2
    在金額為奇數時大量產生 .5，忽上忽下對帳時查不出規律。"""
    fake = _FakeNotion()
    monkeypatch.setattr(meal_reminder, "_store", lambda: fake)

    meal_reminder.record_meals("2026-09-08", {"晚餐": 605},
                               split_type="共同")
    assert fake.added[0]["amount"] == 303
```

附加到 `tests/test_command_router.py`：

```python
def test_shared_keyword_is_stripped_before_ai_sees_it(monkeypatch):
    """「共同」要在送進 AI 之前剝掉 —— 留著的話 AI 會把它當成餐點名稱
    或金額的一部分。"""
    import command_router
    import meal_reminder
    import meal_track
    import personal

    personal.start_pending_meal("U1", "2026-09-08", ("晚餐",))
    seen = []
    monkeypatch.setattr(meal_track, "parse_meal_reply",
                        lambda text, asked: seen.append(text) or {"晚餐": 400})
    calls = []

    def _fake_record(day, meals, split_type="個人"):
        calls.append((day, meals, split_type))
        return len(meals)

    monkeypatch.setattr(meal_reminder, "record_meals", _fake_record)

    reply = command_router.handle("晚餐 400 共同", _personal_ctx())

    assert seen == ["晚餐 400"]
    assert calls == [("2026-09-08", {"晚餐": 400}, "共同")]
    assert "共同" in reply


def test_no_split_keyword_means_personal(monkeypatch):
    import command_router
    import meal_reminder
    import meal_track
    import personal

    personal.start_pending_meal("U1", "2026-09-08", ("晚餐",))
    monkeypatch.setattr(meal_track, "parse_meal_reply",
                        lambda text, asked: {"晚餐": 400})
    calls = []

    def _fake_record(day, meals, split_type="個人"):
        calls.append(split_type)
        return len(meals)

    monkeypatch.setattr(meal_reminder, "record_meals", _fake_record)
    command_router.handle("晚餐 400", _personal_ctx())

    assert calls == ["個人"]
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_reminder.py tests/test_command_router.py -q -k "shared or split_type or odd_shared"
```

預期：FAIL，`TypeError: record_meals() got an unexpected keyword argument 'split_type'`。

- [ ] **Step 3: `record_meals` 加分攤參數**

把 `meal_reminder.py` 的 `record_meals` 換成：

```python
def record_meals(day_iso, meals, split_type="個人"):
    """把 {餐別: 金額} 寫進交易明細。回實際寫成功的筆數。

    meals 裡的金額是**掏出去的全額**。共同消費時「金額」欄存分攤後
    我負擔的那半，「原始總額」存全額 —— 這是既有約定，六處報表都讀
    「金額」，改成存全額會讓它們一起高估，而且不會報錯，只是數字變大。

    split_type 套用整句話而不是逐餐：逐餐要嘛讓使用者講
    「早餐60個人晚餐400共同」（沒人會這樣講），要嘛讓 AI 猜哪一餐是
    共同 —— 猜錯不會報錯，只會讓金額默默少一半。

    一餐一筆，不合併成一筆「三餐 380」—— 合併之後就再也分不出
    早餐花多少，而「哪一餐最貴」正是這整套要回答的問題。

    一筆寫失敗不影響其他筆：Notion 偶爾會超時，讓另外兩餐跟著消失
    是最沒有必要的損失。
    """
    import finance_report
    import meal_track

    if not meals:
        return 0

    notion = _store()
    written = 0
    # 照三餐順序寫，Notion 上的建立順序才讀得懂
    for meal in meal_track.MEALS:
        total = meals.get(meal)
        if not total:
            continue
        amount = (finance_report.my_share_of(total)
                  if split_type == "共同" else total)
        txn = {
            "date": day_iso,
            "amount": amount,          # 我實際負擔 —— 六處報表都讀這個
            "total": total,            # 掏出去的全額
            "split_type": split_type,
            "shop": meal,              # 摘要就叫「早餐」——事後看得懂
            "meal": meal,
            "category": MEAL_CATEGORY,
            "direction": "支出",
            "status": "已結帳",         # 手動輸入就是最終金額，不需要對帳
            "source": "手動",
            # 指紋帶餐別與分攤類型：個人 200 與共同 400（分攤 200）的
            # 「金額」欄都是 200，不加區別第二筆會被去重擋掉。
            "fingerprint": finance_report.make_manual_fingerprint(
                day_iso, amount, meal, split_type),
        }
        try:
            if notion.transaction_add(txn):
                written += 1
        except Exception as e:
            print(f"[meal] 寫入 {day_iso} {meal} 失敗：{e}")
    return written
```

- [ ] **Step 4: `_record_spoken_meals` 先剝離分攤字**

把 `command_router.py` 的 `_record_spoken_meals` 換成：

```python
def _record_spoken_meals(user_id, text, entry):
    """三餐待命中收到的自由文字 → 寫進交易明細。回覆給 reply_message。

    「共同」要在送進 AI 之前剝掉：留著的話模型會把它當成餐點名稱或
    金額的一部分。剝離複用 finance_report._strip_split_type —— 那支
    只認尾端，「共同基金 3000」的共同在開頭，是商店名不是分攤類型。
    """
    import finance_report
    import meal_reminder
    import meal_track

    cleaned, split_type = finance_report._strip_split_type(text)
    split_type = split_type or "個人"

    meals = meal_track.parse_meal_reply(cleaned, entry["meals"])
    if not meals:
        return ("沒聽懂金額，這次先不記。" + _NL + _NL
                + "再按一次推播上的「➕ 補記」，像這樣說：" + _NL
                + "　早餐 60 晚餐 200")

    written = meal_reminder.record_meals(entry["date"], meals,
                                         split_type=split_type)
    if not written:
        return "寫入失敗，這次沒記到。稍後再按一次「➕ 補記」。"

    day = entry["date"]
    detail = "、".join(f"{m} {meals[m]}" for m in ("早餐", "午餐", "晚餐")
                       if m in meals)
    head = f"✅ 已記錄 {day[5:7]}/{day[8:10]}：{detail}"
    if split_type == "共同":
        mine = sum(finance_report.my_share_of(v) for v in meals.values())
        return head + _NL + f"　共同消費，你分攤 NT${mine:,}"
    return head
```

- [ ] **Step 5: 跑測試確認通過**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest tests/test_meal_reminder.py tests/test_command_router.py -q
```

預期：全部 PASS。

- [ ] **Step 6: 跑全套**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q 2>&1 | tail -3
```

預期：全部 PASS。

- [ ] **Step 7: Commit**

```bash
git -C C:/Users/acer/projects/ReportRobot add meal_reminder.py command_router.py tests/test_meal_reminder.py tests/test_command_router.py
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
feat: 三餐補記支援共同分帳

沿用既有語法「晚餐 400 共同」。分攤類型套用整句話而不是逐餐 ——
逐餐要嘛讓使用者講「早餐60個人晚餐400共同」（沒人這樣講），要嘛
讓 AI 猜哪一餐是共同，而猜錯不會報錯，只會讓金額默默少一半。

「共同」在送進 AI 之前就剝掉，否則模型會把它當成餐點名稱。
指紋帶上分攤類型：個人 200 與共同 400（分攤 200）的「金額」欄都是
200，不加區別第二筆會被去重擋掉。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## 收尾

- [ ] **跑全套並確認數字**

```bash
cd C:/Users/acer/projects/ReportRobot && python -m pytest -q 2>&1 | tail -3
```

預期：`1270 passed` 之上再加約 80 個新測試。

- [ ] **確認所有改動檔案的行尾**

```bash
cd C:/Users/acer/projects/ReportRobot && python -c "
crlf = ['notion_db.py','personal.py','command_router.py','flex_builder.py',
        'prompts.py','finance_sync.py']
lf = ['server.py']
for p in crlf:
    d=open(p,'rb').read()
    bad = d.count(b'\n')-d.count(b'\r\n')
    print(('OK  ' if bad==0 else 'BAD '), p, 'bare LF', bad)
for p in lf:
    d=open(p,'rb').read()
    print(('OK  ' if d.count(b'\r\n')==0 else 'BAD '), p, 'CRLF', d.count(b'\r\n'))"
```

預期：每一行都是 `OK`。

- [ ] **更新 HANDOFF**

在 `docs/HANDOFF.md` 的 env 變數表格加一列（CRLF）：

```markdown
| `MEAL_CRON` | 三餐補記提醒的排程（UTC crontab，5 欄位）。預設 `0 13 * * *` = 台北 21:00。格式錯誤會退回預設值並印警告，不會讓 startup crash。 |
```

- [ ] **推上去**

```bash
git -C C:/Users/acer/projects/ReportRobot add docs/HANDOFF.md
git -C C:/Users/acer/projects/ReportRobot commit -F - <<'MSG'
docs: HANDOFF 補上 MEAL_CRON

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
git -C C:/Users/acer/projects/ReportRobot push -u origin feat/meal-tracking-reminder
```

---

## 上線之後使用者要做的事

Railway 部署完（約 60–90 秒）之後，**隔天晚上 21:00** 才會有第一次推播。要立刻驗的話，照這個順序：

1. **等隔天** —— 今晚 21:00 會檢查昨天。昨天的資料早就同步好了，所以第一晚就會動。
2. 推播出現 → 應該看到 `🍽 MM/DD 還缺：早餐、晚餐`（平日）或三餐（假日），加上「午餐已預設自煮」。
3. 按 **➕ 補記** → 應該回「請說。」
4. 說 **`早餐 60 晚餐 200`** → 應該回 `✅ 已記錄 MM/DD：早餐 60、晚餐 200`
5. 按 **➕ 補記** 之後改按 Rich Menu 的 **快過期** → 應該回「已取消補記三餐。」再接上快過期的結果
6. 去 Notion 交易明細看那兩筆：類別「餐飲」、餐別「早餐」/「晚餐」、來源「手動」

**兩件事要先知道：**

- **既有的 72 筆沒有時間也沒有餐別。** Fingerprint 刻意不含時間（`parsers/cathay_daily.py:39`），重跑同步會被去重擋掉，所以補不回來。這兩欄只對加上去之後的資料生效 —— 也就是說第一個月的統計會偏低。
- **第一晚很可能會問「早餐、晚餐」都缺**，因為昨天的刷卡雖然有時間了，但那是在這次部署**之前**寫進 Notion 的，時間欄是空的。從部署後第二天起才會正常。
