# 手動記帳兩段式 Quick Reply 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓「記一筆」像「買了」一樣點兩下就記完帳,不必打字。

**Architecture:** 兩段式 Quick Reply(品項 → 金額)。按鈕送出的文字本身攜帶進度
(`記一筆` → `記一筆 午餐` → `記一筆 午餐 120`),三種狀態靠 `arg` 內容判斷,
不需要對話狀態機。新邏輯全部進 `finance_report.py` 的純函式,不碰 Notion 也不碰 LINE。

**Tech Stack:** Python 3、pytest、LINE Messaging API(quickReply)、Notion API

**規格:** `docs/superpowers/specs/2026-08-19-manual-entry-quick-reply-design.md`

**分支:** `feat/manual-entry-quick-reply`(已建立,勿推 main —— Railway 接 main 自動部署)

---

## 檔案結構

| 檔案 | 責任 | 動作 |
|---|---|---|
| `notion_db.py` | Notion 讀寫 | 修改:`transactions_load` 補讀「來源」欄 |
| `finance_report.py` | 財務純邏輯 | 修改:+3 個函式,`parse_manual` 改一行 |
| `command_router.py` | 指令解析與分派 | 修改:`fin_manual` 三態分流 + 2 個 helper |
| `setup_richmenu.py` | Rich Menu 定義 | 修改:1 行 `prompt` → `message` |
| `tests/test_manual_entry_quickreply.py` | 本功能測試 | 新建 |
| `tests/test_transactions_load.py` | 既有 | 修改:+1 個測試 |

**為什麼不抽共用的排序函式:** `finance_report.frequent_expense_items` 與
`kitchen.frequent_items` 的排序邏輯確實相似,但過濾條件不同(一個看 `source`、
一個看庫存狀態),而且分屬兩個領域模組。抽共用會讓 `finance_report` 依賴
`kitchen` 或逼出一個新的 util 模組 —— 為了省 15 行製造跨領域耦合不划算。
保持各自實作,註解寫明對應關係。

---

## Task 1: `transactions_load` 補讀「來源」欄

沒有這一步,後面「只學手動記的帳」拿不到資料 —— 而且不會報錯,
`source` 全部是 `None`,學習邏輯會安靜地把所有交易都濾掉,按鈕永遠是預設六樣。

**Files:**
- Modify: `notion_db.py`(`transactions_load` 的 `out.append`)
- Test: `tests/test_transactions_load.py`

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_transactions_load.py` 的 `PagedDatabases._row` 加入「來源」欄位:

```python
    @staticmethod
    def _row(i):
        return {
            "properties": {
                "日期": {"date": {"start": f"2026-08-{(i % 28) + 1:02d}"}},
                "金額": {"number": i + 1},
                "商店": {"rich_text": [{"plain_text": f"店{i}"}]},
                "類別": {"select": {"name": "餐飲"}},
                "方向": {"select": {"name": "支出"}},
                "狀態": {"select": {"name": "授權中"}},
                "來源": {"select": {"name": "手動" if i % 2 else "國泰消費彙整"}},
            }
        }
```

在檔案末尾加測試:

```python
def test_load_reads_source(monkeypatch):
    """來源寫得進去就要讀得回來，否則分不出手動記帳與自動同步。"""
    _install(monkeypatch, 4)

    rows = notion_db.transactions_load(limit=4)

    assert [r["source"] for r in rows] == [
        "國泰消費彙整", "手動", "國泰消費彙整", "手動"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_transactions_load.py::test_load_reads_source -v`
Expected: FAIL — `KeyError: 'source'`

- [ ] **Step 3: 實作**

在 `notion_db.py` 的 `transactions_load` 裡,`out.append({...})` 的
`"status": _read_select(props, "狀態"),` 後面加一行:

```python
                    "source": _read_select(props, "來源"),
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_transactions_load.py -v`
Expected: 全部 PASS(既有分頁測試不受影響)

- [ ] **Step 5: Commit**

```bash
git add notion_db.py tests/test_transactions_load.py
git commit -m "fix(notion): transactions_load 補讀「來源」欄

來源寫得進去(transaction_add 有送)卻讀不出來,任何想區分
自動同步與手動記帳的功能都拿到 None,而且不會報錯。"
```

---

## Task 2: `guess_category` — 吃的自動歸「餐飲」

**Files:**
- Modify: `finance_report.py`
- Test: `tests/test_manual_entry_quickreply.py`(新建)

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_manual_entry_quickreply.py`:

```python
"""「記一筆」不帶參數時的兩段式 Quick Reply：點品項 → 點金額 → 記完。

原本要打「記一筆 午餐 120」，手機上打中文是最大的摩擦。
改成按「記一筆」→ 跳常記品項 → 點「午餐」→ 跳常用金額 → 點「120」。

按鈕送出的文字本身攜帶進度（記一筆 / 記一筆 午餐 / 記一筆 午餐 120），
所以三種狀態靠 arg 內容判斷，不需要對話狀態機。
"""

import pytest

import command_router as cr
import finance_report as fr


def _txn(shop, amount, source="手動"):
    return {"date": "2026-08-19", "amount": amount, "shop": shop,
            "category": "餐飲", "direction": "支出", "currency": "TWD",
            "status": "已結帳", "source": source}


# ── 分類判斷 ─────────────────────────────────────────────

@pytest.mark.parametrize("shop", ["早餐", "午餐", "晚餐", "咖啡", "飲料", "點心"])
def test_default_items_are_food(shop):
    assert fr.guess_category(shop) == "餐飲"


def test_food_keyword_inside_a_longer_name():
    """「跟同事吃午餐」也該是餐飲 —— 手打時不會剛好只打兩個字。"""
    assert fr.guess_category("跟同事吃午餐") == "餐飲"


def test_unknown_item_is_other():
    """國泰分類裡沒有「交通」，不自創類別（notion_db.py:90）。"""
    assert fr.guess_category("搭車") == "其他"


def test_blank_is_other():
    assert fr.guess_category("") == "其他"
    assert fr.guess_category(None) == "其他"


def test_parse_manual_uses_guessed_category():
    assert fr.parse_manual("午餐 120")["category"] == "餐飲"
    assert fr.parse_manual("搭車 30")["category"] == "其他"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: FAIL — `AttributeError: module 'finance_report' has no attribute 'guess_category'`

- [ ] **Step 3: 實作**

在 `finance_report.py` 的 `_INCOME_HINTS` 下面加:

```python
# 認得的餐飲品項。用子字串比對而非完全相等：手打時常是「跟同事吃午餐」
# 這種句子，只認兩個字的話大多數手打紀錄都會落到「其他」。
# 已知取捨：「咖啡機」會被判成餐飲。發生率遠低於前者，接受。
_FOOD_HINTS = ("早餐", "午餐", "晚餐", "咖啡", "飲料", "點心", "宵夜", "下午茶")
```

在 `parse_manual` 上方(第 173 行 `_AMOUNT_RE` 附近)加函式:

```python
def guess_category(shop):
    """品項 → 消費類別。認不出來回「其他」，不自創類別。

    類別沿用國泰帳單自帶分類（notion_db._SPEND_CATEGORIES）。國泰沒有
    「交通」，所以「搭車」記成「其他」—— 增生分類會讓 Notion 長出
    兩套命名系統，之後兩邊都對不起來。
    """
    name = (shop or "").strip()
    return "餐飲" if any(k in name for k in _FOOD_HINTS) else "其他"
```

改 `parse_manual` 回傳 dict 裡的 category 那一行:

```python
        "category": guess_category(shop),
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_manual_entry_quickreply.py tests/test_finance_report.py -v`
Expected: 全部 PASS

`tests/test_finance_report.py` 已查證過**沒有**斷言 `parse_manual` 輸出類別的測試
(只有第 12 行的 `_txn` fixture 帶 category 參數,那是餵給格式化函式的假資料),
所以這一步不會撞到既有測試。既有的 `test_parse_manual_entry` 參數化案例
`("咖啡 85元", "咖啡", 85)` 與 `("計程車 350", "計程車", 350)` 只斷言 shop 與 amount,
不受影響。

- [ ] **Step 5: Commit**

```bash
git add finance_report.py tests/test_manual_entry_quickreply.py
git commit -m "feat(finance): 手動記帳的餐飲品項自動歸類

原本寫死「其他」，手動記的帳越多，本月支出的分類百分比越失真。"
```

---

## Task 3: `frequent_expense_items` — 常記品項

**Files:**
- Modify: `finance_report.py`
- Test: `tests/test_manual_entry_quickreply.py`

- [ ] **Step 1: 寫失敗測試**

追加到 `tests/test_manual_entry_quickreply.py`:

```python
# ── 常記品項 ─────────────────────────────────────────────

def test_frequent_items_ranks_by_count():
    txns = [_txn("午餐", 120), _txn("午餐", 100), _txn("午餐", 150),
            _txn("咖啡", 55), _txn("咖啡", 65),
            _txn("搭車", 30)]

    assert fr.frequent_expense_items(txns, limit=3, pad=False) == [
        "午餐", "咖啡", "搭車"]


def test_frequent_items_ignores_auto_synced():
    """信用卡同步的店名放按鈕上沒意義，還會被 LINE 截成半截。"""
    txns = [_txn("全聯福利中心－板橋板新", 361, source="國泰消費彙整"),
            _txn("全聯福利中心－板橋板新", 210, source="國泰消費彙整"),
            _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False) == ["午餐"]


def test_frequent_items_ties_keep_first_seen_order():
    """同次數時位置要穩定：按鈕每次都在跳比排序不準更難用。"""
    txns = [_txn("咖啡", 55), _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False) == ["咖啡", "午餐"]


def test_frequent_items_ignores_blank_names():
    txns = [_txn("", 100), _txn("   ", 100), _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False) == ["午餐"]


def test_frequent_items_pads_with_defaults():
    """第一天沒歷史，給空按鈕列等於這個功能不存在。"""
    assert fr.frequent_expense_items([], limit=6) == [
        "午餐", "晚餐", "早餐", "咖啡", "飲料", "點心"]


def test_padding_never_duplicates_history():
    txns = [_txn("咖啡", 55)]

    out = fr.frequent_expense_items(txns, limit=6)

    assert out[0] == "咖啡"
    assert out.count("咖啡") == 1
    assert len(out) == 6


def test_history_always_outranks_padding():
    txns = [_txn("搭車", 30)]

    assert fr.frequent_expense_items(txns, limit=6)[0] == "搭車"


def test_frequent_items_respects_limit():
    txns = [_txn(f"品項{i}", 100) for i in range(20)]

    assert len(fr.frequent_expense_items(txns, limit=6)) == 6
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -k frequent_items -v`
Expected: FAIL — `AttributeError: module 'finance_report' has no attribute 'frequent_expense_items'`

- [ ] **Step 3: 實作**

在 `finance_report.py` 的 `guess_category` 下面加:

```python
# 沒有記帳歷史時的預設按鈕。用一陣子後會被真實習慣取代。
_DEFAULT_ITEMS = ["午餐", "晚餐", "早餐", "咖啡", "飲料", "點心"]


def frequent_expense_items(txns, limit=6, pad=True):
    """常記品項：手動記過越多次的排前面。純邏輯，不碰 Notion。

    只看 source == "手動"。交易明細裡混著信用卡自動同步的資料，商店名
    長這樣「全聯福利中心－板橋板新」—— 放到按鈕上沒有意義，而且 LINE 的
    label 上限 20 字會把它截成半截店名。

    同次數保持第一次出現的順序：每次跳出來的按鈕位置都在動，比排序不準
    更難用。（與 kitchen.frequent_items 同一套理由，過濾條件不同故各自實作。）

    pad=True 時用 _DEFAULT_ITEMS 補到 limit：沒歷史就給空按鈕列，
    等於這個功能第一天不存在。
    """
    counts = {}
    order = []
    for t in txns or []:
        if (t.get("source") or "") != "手動":
            continue
        name = (t.get("shop") or "").strip()
        if not name:
            continue
        if name not in counts:
            order.append(name)
        counts[name] = counts.get(name, 0) + 1

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

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add finance_report.py tests/test_manual_entry_quickreply.py
git commit -m "feat(finance): 常記品項清單（只學手動記的帳）"
```

---

## Task 4: `frequent_amounts` — 該品項的常用金額

**Files:**
- Modify: `finance_report.py`
- Test: `tests/test_manual_entry_quickreply.py`

- [ ] **Step 1: 寫失敗測試**

追加到 `tests/test_manual_entry_quickreply.py`:

```python
# ── 常用金額 ─────────────────────────────────────────────

def test_amounts_rank_by_count():
    txns = [_txn("午餐", 120), _txn("午餐", 120), _txn("午餐", 100)]

    assert fr.frequent_amounts(txns, "午餐", limit=5, pad=False) == [120, 100]


def test_amounts_are_per_item():
    """共用一份全域金額清單會讓咖啡的按鈕上出現 200 元。"""
    txns = [_txn("午餐", 120), _txn("咖啡", 55)]

    assert fr.frequent_amounts(txns, "咖啡", limit=5, pad=False) == [55]


def test_amounts_ignore_auto_synced():
    txns = [_txn("午餐", 999, source="國泰消費彙整"), _txn("午餐", 120)]

    assert fr.frequent_amounts(txns, "午餐", limit=5, pad=False) == [120]


def test_amounts_pad_with_seeds():
    """第一天沒歷史，金額按鈕不能是空的。"""
    assert fr.frequent_amounts([], "午餐") == [100, 120, 150]


def test_seeds_never_duplicate_history():
    txns = [_txn("午餐", 120)]

    out = fr.frequent_amounts(txns, "午餐")

    assert out[0] == 120
    assert out.count(120) == 1


def test_unknown_item_has_no_seed_amounts():
    """使用者自己打的品項沒有種子金額 —— 呼叫端要據此不放 quickReply，
    空的 quickReply 物件會被 LINE 當格式錯誤整則退回。"""
    assert fr.frequent_amounts([], "搭車") == []


def test_unknown_item_still_learns_from_history():
    txns = [_txn("搭車", 30), _txn("搭車", 30), _txn("搭車", 45)]

    assert fr.frequent_amounts(txns, "搭車") == [30, 45]


def test_blank_item_returns_empty():
    assert fr.frequent_amounts([_txn("午餐", 120)], "") == []


def test_amounts_are_ints_when_whole():
    """按鈕 label 不要出現 120.0。"""
    txns = [_txn("午餐", 120.0)]

    assert fr.frequent_amounts(txns, "午餐", pad=False) == [120]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -k amounts -v`
Expected: FAIL — `AttributeError: module 'finance_report' has no attribute 'frequent_amounts'`

- [ ] **Step 3: 實作**

在 `finance_report.py` 的 `frequent_expense_items` 下面加:

```python
# 種子金額：該品項還沒有歷史時的按鈕。記過幾次之後就由真實資料接手。
# 沒列在這裡的品項（使用者自己打的「搭車」）回空 list，呼叫端只給文字提示。
_SEED_AMOUNTS = {
    "早餐": [50, 60, 80],
    "午餐": [100, 120, 150],
    "晚餐": [120, 150, 200],
    "咖啡": [55, 65, 85],
    "飲料": [35, 50, 60],
    "點心": [40, 50, 80],
}


def frequent_amounts(txns, item, limit=5, pad=True):
    """某個品項的常用金額，記過越多次的排前面。純邏輯，不碰 Notion。

    依品項分別統計：共用一份全域金額清單會讓咖啡的按鈕上出現 200 元。
    與 frequent_expense_items 一樣只看 source == "手動"。

    整數金額回 int，否則按鈕上會出現「120.0」。
    """
    key = (item or "").strip()
    if not key:
        return []

    counts = {}
    order = []
    for t in txns or []:
        if (t.get("source") or "") != "手動":
            continue
        if (t.get("shop") or "").strip() != key:
            continue
        amount = t.get("amount")
        if amount is None:
            continue
        amount = int(amount) if float(amount) == int(amount) else amount
        if amount not in counts:
            order.append(amount)
        counts[amount] = counts.get(amount, 0) + 1

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

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add finance_report.py tests/test_manual_entry_quickreply.py
git commit -m "feat(finance): 每個品項各自學自己的常用金額"
```

---

## Task 5: `command_router` 三態分流

**Files:**
- Modify: `command_router.py`(`_handle_finance` 的 `fin_manual` 分支 + 2 個 helper)
- Test: `tests/test_manual_entry_quickreply.py`

- [ ] **Step 1: 寫失敗測試**

追加到 `tests/test_manual_entry_quickreply.py`:

```python
# ── 三態分流 ─────────────────────────────────────────────

class FakeNotion:
    def __init__(self, txns=None, configured=True, write_ok=True):
        self._txns = txns or []
        self.added = []
        self._configured = configured
        self._write_ok = write_ok

    def is_configured(self):
        return self._configured

    def transactions_load(self, limit=200):
        return list(self._txns)

    def transaction_add(self, txn):
        if not self._write_ok:
            return None
        self.added.append(txn)
        return "page-fake"


@pytest.fixture
def fake_notion(monkeypatch):
    def _install(**kwargs):
        import sys
        fake = FakeNotion(**kwargs)
        monkeypatch.setitem(sys.modules, "notion_db", fake)
        return fake
    return _install


def _ctx():
    return {"source_type": "user", "user_id": "U1"}


def test_bare_command_returns_item_buttons(fake_notion):
    fake_notion(txns=[_txn("午餐", 120), _txn("午餐", 100), _txn("咖啡", 55)])

    reply = cr.handle("記一筆", _ctx())

    labels = [i["action"]["label"] for i in reply["quickReply"]["items"]]
    assert labels[0] == "午餐"


def test_item_buttons_send_parseable_commands(fake_notion):
    """按鈕送出的字串必須自己 parse 得回來，不然點了沒反應。"""
    fake_notion(txns=[_txn("午餐", 120)])

    reply = cr.handle("記一筆", _ctx())

    for item in reply["quickReply"]["items"]:
        sent = item["action"]["text"]
        assert cr.parse(sent) == ("fin_manual", sent.split(" ", 1)[1])


def test_item_only_returns_amount_buttons(fake_notion):
    fake_notion(txns=[_txn("午餐", 120), _txn("午餐", 120), _txn("午餐", 100)])

    reply = cr.handle("記一筆 午餐", _ctx())

    labels = [i["action"]["label"] for i in reply["quickReply"]["items"]]
    assert labels[:2] == ["120", "100"]


def test_amount_buttons_send_complete_commands(fake_notion):
    fake_notion(txns=[_txn("午餐", 120)])

    reply = cr.handle("記一筆 午餐", _ctx())

    for item in reply["quickReply"]["items"]:
        sent = item["action"]["text"]
        assert sent.startswith("記一筆 午餐 ")
        assert cr.parse(sent)[0] == "fin_manual"


def test_full_flow_actually_writes(fake_notion):
    """整條線走完：點品項 → 點金額 → 真的進 Notion。"""
    fake = fake_notion(txns=[_txn("午餐", 120)])

    step1 = cr.handle("記一筆", _ctx())
    item_cmd = step1["quickReply"]["items"][0]["action"]["text"]
    step2 = cr.handle(item_cmd, _ctx())
    amount_cmd = step2["quickReply"]["items"][0]["action"]["text"]

    cr.handle(amount_cmd, _ctx())

    assert len(fake.added) == 1
    assert fake.added[0]["shop"] == "午餐"
    assert fake.added[0]["amount"] == 120
    assert fake.added[0]["category"] == "餐飲"


def test_unknown_item_falls_back_to_text(fake_notion):
    """沒種子金額的品項只給文字提示 —— 空 quickReply 會被 LINE 整則退回。"""
    fake_notion(txns=[])

    reply = cr.handle("記一筆 搭車", _ctx())

    assert isinstance(reply, str)
    assert "記一筆 搭車" in reply


def test_complete_command_is_unchanged(fake_notion):
    """有帶完整參數時行為照舊，不要因為加按鈕改掉主要路徑。"""
    fake = fake_notion()

    reply = cr.handle("記一筆 午餐 120", _ctx())

    assert isinstance(reply, str)
    assert "已記錄" in reply
    assert len(fake.added) == 1


def test_reply_shows_category(fake_notion):
    fake_notion()

    reply = cr.handle("記一筆 午餐 120", _ctx())

    assert "餐飲" in reply


def test_income_still_detected(fake_notion):
    fake = fake_notion()

    cr.handle("記一筆 薪水 50000", _ctx())

    assert fake.added[0]["direction"] == "收入"


def test_falls_back_to_text_when_notion_down(fake_notion):
    fake_notion(configured=False)

    reply = cr.handle("記一筆", _ctx())

    assert isinstance(reply, str) and "記一筆" in reply


def test_write_failure_is_readable(fake_notion):
    fake_notion(write_ok=False)

    reply = cr.handle("記一筆 午餐 120", _ctx())

    assert isinstance(reply, str) and "失敗" in reply


def test_bare_command_still_explains_typed_form(fake_notion):
    """按鈕只能點常見的，特殊金額還是要打字 —— 用法不能消失。"""
    fake_notion(txns=[_txn("午餐", 120)])

    reply = cr.handle("記一筆", _ctx())

    assert "記一筆" in reply["text"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: FAIL — `test_bare_command_returns_item_buttons` 得到字串而非 dict
(`TypeError: string indices must be integers`)

- [ ] **Step 3: 實作**

在 `command_router.py` 的 `_MANUAL_USAGE` 常數(約第 730 行)下面加兩個 helper
與一個提示常數:

```python
_MANUAL_QUICK_HINT = (
    "要記什麼?點下面常記的,或直接打:\n"
    "記一筆 午餐 120\n\n"
    "含「薪水、獎金、退款」等字會記成收入。"
)


def _manual_item_quick_reply():
    """「記一筆」不帶參數 → 常記品項按鈕。Notion 掛掉就退回用法說明。"""
    import finance_report
    import notion_db
    from flex_builder import quick_reply_text

    if not notion_db.is_configured():
        return _MANUAL_USAGE

    try:
        txns = notion_db.transactions_load()
    except Exception as e:
        print(f"常記品項載入失敗:{e}")
        return _MANUAL_USAGE

    names = finance_report.frequent_expense_items(txns)
    if not names:
        return _MANUAL_USAGE
    return quick_reply_text(_MANUAL_QUICK_HINT,
                            [(n, f"記一筆 {n}") for n in names])


def _manual_amount_quick_reply(item):
    """「記一筆 午餐」→ 常用金額按鈕。

    沒有可用金額(使用者自己打的品項且無歷史)就只回文字提示 ——
    空的 quickReply 物件會被 LINE 當格式錯誤,整則訊息退回。
    """
    import finance_report
    import notion_db
    from flex_builder import quick_reply_text

    hint = f"{item} 多少錢?點下面的,或直接打:\n記一筆 {item} 95"

    if not notion_db.is_configured():
        return hint

    try:
        txns = notion_db.transactions_load()
    except Exception as e:
        print(f"常用金額載入失敗:{e}")
        return hint

    amounts = finance_report.frequent_amounts(txns, item)
    if not amounts:
        return hint
    return quick_reply_text(hint,
                            [(str(a), f"記一筆 {item} {a}") for a in amounts])
```

把 `_handle_finance` 的 `fin_manual` 分支(約第 744-753 行)整段換成:

```python
    if kind == "fin_manual":
        if not arg:
            return _manual_item_quick_reply()
        txn = finance_report.parse_manual(arg)
        if not txn:
            # 有品項沒金額 —— 這是兩段式的第二段，不是錯誤
            return _manual_amount_quick_reply(arg.strip())
        if not notion_db.transaction_add(txn):
            return "寫入 Notion 失敗,請稍後再試。"
        sign = "+" if txn["direction"] == "收入" else "-"
        return (f"✅ 已記錄:{txn['shop']}　{sign}NT${txn['amount']:,}"
                f"（{txn['category']}）")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add command_router.py tests/test_manual_entry_quickreply.py
git commit -m "feat(finance): 「記一筆」兩段式 Quick Reply

空 arg → 品項按鈕；只有品項 → 金額按鈕；兩者都有 → 照舊寫入。
按鈕送出的文字攜帶進度，不需要對話狀態機。"
```

---

## Task 6: Rich Menu 的「記一筆」改送裸指令

`prompt` 只會打開鍵盤預填「記一筆 」,**永遠送不出裸指令**,
所以 Task 5 做的按鈕從選單點下去根本不會出現。
`docs/HANDOFF.md` 第 231 行記過「買了」踩的同一個坑。

**Files:**
- Modify: `setup_richmenu.py`(第 74 行)
- Test: `tests/test_manual_entry_quickreply.py`

- [ ] **Step 1: 寫失敗測試**

追加到 `tests/test_manual_entry_quickreply.py`:

```python
# ── Rich Menu ────────────────────────────────────────────

def test_record_cell_sends_bare_command():
    """選單的「記一筆」要送裸指令才會跳品項按鈕；prompt 只會開鍵盤。"""
    import setup_richmenu as rm

    cells = rm.MENUS["finance"]["cells"]
    kind, param = next(c[3] for c in cells if c[0] == "記一筆")

    assert (kind, param) == ("message", "記一筆")
    assert cr.parse(param) == ("fin_manual", None)


def test_record_cell_does_not_fall_through_to_paid_ai():
    """裸指令沒被 command_router 認得會掉進 free_query —— 按一次付一次
    Anthropic 的錢，而且不會壞、不會有紅字（HANDOFF 4.4）。"""
    import setup_richmenu as rm

    cells = rm.MENUS["finance"]["cells"]
    _, param = next(c[3] for c in cells if c[0] == "記一筆")

    assert cr.parse(param)[0] != "free_query"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_manual_entry_quickreply.py -k record_cell -v`
Expected: `test_record_cell_sends_bare_command` FAIL —
`assert ('prompt', '記一筆 ') == ('message', '記一筆')`

- [ ] **Step 3: 實作**

`setup_richmenu.py` 第 74 行:

```python
            ("記一筆",   "ADD",      "#9A7B63", ("prompt",  "記一筆 ")),
```

換成(含註解,理由要留給下一個人):

```python
            # 送裸指令：「記一筆」不帶東西會回一排常記品項，再點金額，
            # 兩下記完。prompt 只會開鍵盤，送不出裸指令（HANDOFF 231）。
            ("記一筆",   "ADD",      "#9A7B63", ("message", "記一筆")),
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_manual_entry_quickreply.py tests/test_richmenu.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add setup_richmenu.py tests/test_manual_entry_quickreply.py
git commit -m "fix(menu): 「記一筆」改送裸指令，不然按鈕列跳不出來

prompt 只會開鍵盤預填文字。與 HANDOFF 231 行「買了」同一個坑。
部署後要重跑 POST /admin/setup-richmenu 才生效。"
```

---

## Task 7: 全套測試 + 文件同步

**Files:**
- Modify: `command_router.py`(`HELP_TEXT`)
- Modify: `docs/HANDOFF.md`

- [ ] **Step 1: 跑全部測試**

Run: `python -m pytest -q`
Expected: 全數 PASS。既有測試數 222 起跳,本次新增約 40 個。

若 `tests/test_help_text.py` 因下一步的說明文字變動而紅,是預期的,
在 Step 2 一起修。

- [ ] **Step 2: 更新 HELP_TEXT**

`command_router.py` 第 121 行附近,把:

```python
    "  • 記一筆 午餐 120     ← 手動記帳\n"
    "  • 記一筆 薪水 50000   ← 含薪水/獎金/退款會記成收入\n"
```

換成:

```python
    "  • 記一筆        ← 只打三個字會跳常記品項，再點金額，兩下記完\n"
    "  • 記一筆 午餐 120     ← 直接打也可以\n"
    "  • 記一筆 薪水 50000   ← 含薪水/獎金/退款會記成收入\n"
```

Run: `python -m pytest tests/test_help_text.py -v`
Expected: PASS

- [ ] **Step 3: 更新 HANDOFF.md**

在第 3 節「真實環境驗證狀態」表格追加一列:

```markdown
| 「記一筆」兩段式 Quick Reply | ❌ 沒在 LINE 上按過（約 40 個單元測試）;**要重跑 `/admin/setup-richmenu`** 才會生效 |
```

在第 10 節「使用者待辦」追加:

```markdown
- [ ] 部署後重跑 `POST /admin/setup-richmenu`（「記一筆」從 prompt 改成 message）
- [ ] 在 LINE 私訊實測「記一筆」→ 點品項 → 點金額
```

- [ ] **Step 4: 跑全部測試確認乾淨**

Run: `python -m pytest -q`
Expected: 全數 PASS,沒有 warning 以外的輸出

- [ ] **Step 5: Commit**

```bash
git add command_router.py docs/HANDOFF.md
git commit -m "docs: HELP_TEXT 與 HANDOFF 同步兩段式記帳"
```

---

## 完成後的人工步驟

實作完成不等於能用。以下三件事程式端做不到:

1. **合併到 main** —— Railway 接 main 自動部署,推 main = 上線
2. **重跑 `POST /admin/setup-richmenu`** —— 否則線上選單的「記一筆」還是舊的 prompt 行為
3. **在 LINE 私訊實測** —— HANDOFF 第 3 節記錄財務按鈕從未被真實使用過,
   這次的兩段式流程同樣沒有真實環境驗證

```powershell
$t = [Environment]::GetEnvironmentVariable('ADMIN_TOKEN','User')
Invoke-RestMethod -Method Post `
  -Uri "https://chengreportbot-production.up.railway.app/admin/setup-richmenu" `
  -Headers @{ 'X-Admin-Token' = $t }
```
