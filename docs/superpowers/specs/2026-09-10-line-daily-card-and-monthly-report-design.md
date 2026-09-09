# 每日 LINE 精簡卡 + 支出月報 — 設計

**日期:** 2026-09-10
**狀態:** 設計已確認，尚未實作
**前置:**
- `docs/superpowers/specs/2026-08-13-daily-spending-bubble-design.md`（每日消費泡泡）
- `docs/superpowers/specs/2026-09-01-phrasebook-and-spending-chart-design.md`（圓餅圖 + mailer 內嵌圖）
- `docs/superpowers/specs/2026-09-09-meal-tracking-reminder-design.md`（三餐補記，共用交易明細）

---

## 1. 問題

**每日信寄得很完整，但使用者不一定會開信。** 每天早上真正想知道的只有四件事：
今天要做什麼、昨天花了多少、股票漲跌、要不要帶傘。這四件事全都已經在信裡，
但要開信才看得到。

**支出只有「當下」沒有「趨勢」。** `/本月支出` 回答「這個月到目前為止」，
每日信的圓餅圖畫的也是當月。沒有任何地方回答「這個月跟上個月比，我是變省還是變兇」——
而那才是會改變行為的那個數字。

---

## 2. 五個決定與理由

### 2.1 資料讀一次，email 與 LINE 共用

`_email_personal_report` 目前把每個區塊各自包成一個 `_safe(...)` 閉包，
每個閉包自己去 Notion 撈資料。LINE 卡片如果照抄這個模式，會變成：

- 同一天對 Notion 查兩輪（交易明細、待辦、持倉各兩次）
- 兩邊的快照時間差幾秒到幾十秒
- **兩邊數字不一樣時，沒有任何地方看得出來是哪一邊錯**

所以改成先把原始資料撈齊、再分別餵給兩個 formatter。這也順便解掉一個既有問題：
`_spending_recent()` 內部撈了 txns 但只回傳格式化過的字串，LINE 卡片要的
「昨天花多少」拿不到原始數字。

```
_load_personal_data(today) → dict（原始資料，不含任何排版）
        ├── _build_personal_sections(...)  → email HTML
        └── flex_builder.daily_card_flex(...) → LINE 一張卡
```

### 2.2 LINE 卡片不是信的縮圖

已排除的替選方案是「把 email 的 sections 前 N 個字截斷後塞進 flex」。
那會得到一張讀不懂的卡：email 的區塊是**段落**（多行、有標題、有明細），
flex 卡的資訊密度完全不同 —— 一行只放得下一個事實。

四個區塊在卡片上各佔**一行**：

| 區塊 | 卡片上長什麼樣 | 資料來源 |
|---|---|---|
| 今日待辦 | `📋 交社宅資料、繳健保費`（只 P0，最多 2 筆 + 「還有 N 件」） | `personal.todos_due_today` |
| 昨天花了多少 | `💳 昨天 779　本月 24,135` | 交易明細 |
| 持倉漲跌 | `📈 +1.2%（2 漲 2 跌）` | `stock_moves.daily_moves` |
| 天氣 | `🌤 板橋 26–31°C　降雨 20%` | `weather` |

**空的區塊整行不放**，沿用 email 既有規則（`_build_personal_sections` 的
「空的區塊直接不放：留一張空卡片比沒有還糟」）。四個都空就不推播。

### 2.3 一則推播是硬上限，塞不下就砍內容

LINE 免費方案每月 200 則。目前用量 16/200、月底推估 53 則；加上三餐補記
約 28 則、每日卡 30 則，合計約 **111 / 200（55%）**，離 80% 警示還有距離。

但這個數字的前提是**每天只推一則**。卡片如果撐爆一張 bubble 而要拆成兩則，
月耗直接變 60，總數跳到 141（70%）—— 開始逼近警示線。

所以「精簡」在這裡不是美學偏好，是硬性約束：

- 待辦最多列 2 筆，其餘收成「還有 N 件」
- 每一行不換行（`wrap: false`），過長的字串在 formatter 端就截斷
- 不放圓餅圖（flex 的 hero image 要 URL，而圖是本機檔案，得先上傳到某處）

### 2.4 月報寄「上個月」，不是「最近 30 天」

月份是使用者思考支出的單位（帳單、薪水、房租都以月為界）。「最近 30 天」
橫跨兩個月，跟任何一張帳單都對不起來。

每月 **1 號** 早上寄上個月的完整報告。1 號寄而不是月底最後一天：月底當天
還會有消費，那份報告從寄出的那一刻就是錯的。

這正是 `transactions_load_month` 的 `until` 邊界存在的理由 —— 資料新到舊
排序，查上個月時只給起點的話 `limit` 會先被這個月的新資料填滿，上個月
於是看起來沒花多少錢（見 `notion_db.transactions_load_month` 的 docstring）。

### 2.5 月報的精簡原則：一個數字只出現一次

使用者對第一版報告的回饋是「不錯，但想要精簡一點，重複、不重要的資訊刪掉」。

刪掉的東西與理由：

| 拿掉 | 為什麼 |
|---|---|
| 「整桌總額」 | 跟「我實際負擔」講的是同一筆錢的兩種切法。共同消費金額大時才有意義，平常兩個數字並排只是噪音 —— 改成只在共同消費 > 總支出 10% 時才多一行 |
| 佔比 < 3% 的類別 | 上個月尾巴四類合計 2.7%。列出來只是把眼睛從前五名帶走 |
| 「存錢情境」三檔 | 那是一次性的分析，不是每月都會變的東西。月報講「發生了什麼」，不講「你該怎麼辦」 |
| 每日長條圖 | 好看，但「哪一天花最多」已經由「最大三筆」回答了 |

留下來的五塊：

1. **三個數字**：總支出、日均、與上個月比較（`+12%` / `−8%`）
2. **類別 TOP 5**（其餘合併成「其他 N 類」）
3. **圓餅圖**（沿用 `spending_chart.build_pie` + mailer 內嵌）
4. **最大三筆**
5. **一句話結論**：變化最大的那個類別

第 5 點是唯一的「分析」，而且是算出來的不是寫死的：跟上個月比，金額變化
絕對值最大的類別，例如「餐飲比上個月多了 1,340（+36%）」。

---

## 3. 架構

### 3.1 資料層

不動 schema。兩個功能都只讀，不寫。

### 3.2 新模組

| 檔案 | 責任 |
|---|---|
| `daily_card.py` | 把原始資料組成 LINE 一行一個事實的四個字串。**純邏輯**，不碰 Notion、不碰 LINE |
| `monthly_report.py` | 月報：兩個月的資料 → 五塊內容。**純邏輯**，圖與寄信由呼叫端做 |

兩支都是純邏輯，測試不需要 mock 任何東西 —— 跟 `phrasebook.py`、`meal_track.py`
同一套。

### 3.3 改動的既有檔案

| 檔案 | 改什麼 |
|---|---|
| `daily_report.py` | 抽出 `_load_personal_data(today)`；`_email_personal_report` 改吃它；新增 `_push_personal_card` |
| `flex_builder.py` | 新增 `daily_card_flex(lines)` |
| `server.py` | 新增 `MONTHLY_CRON` 排程 |
| `finance_report.py` | 新增 `category_totals(txns, month)`（月報與圓餅圖共用，避免兩邊各算一次） |

---

## 4. 每日 LINE 卡

### 4.1 `daily_card.py` 的四個函式

全部回字串或 None（None = 這一行不放）。

```python
def todo_line(todos, limit=2):
    """📋 交社宅資料、繳健保費　+3

    只列 P0。超過 limit 筆時收成「+N」而不是換行 —— 卡片上多一行的
    成本是「可能塞不下而拆成兩則推播」，那會讓月配額從 30 變 60。
    """


def spending_line(txns, today):
    """💳 昨天 779　本月 24,135

    昨天而不是今天：早上 8 點推播時，今天還沒開始花錢，而昨天的
    刷卡在 15:30 就同步完了。
    """


def stock_line(moves):
    """📈 +1.2%　2 漲 2 跌"""


def weather_line(report):
    """🌤 板橋 26–31°C　降雨 20%"""
```

### 4.2 `flex_builder.daily_card_flex(lines)`

一張 bubble，每個字串一行，`wrap: false`。底部一顆按鈕「看完整報告」——
`message` 型，送 `/本月支出`，因為那是最常接著想看的東西。

### 4.3 推播時機

跟每日信同一個排程（`DAILY_CRON`，台北 08:00），在 `_email_personal_report`
之後。**信先寄、卡後推**：信是完整版，推播失敗不該讓信也跟著沒出去。

推播走 `line_sender.push_to_user_sync` + `line_quota.bump()`，收件人用
`_personal_user_id()`（`PERSONAL_USER_ID` 沒設就退回 `ADMIN_LINE_USER_ID`）。

---

## 5. 支出月報

### 5.1 `monthly_report.py`

```python
def build(this_month_txns, last_month_txns, month):
    """回 dict，欄位對應 5.2 的五塊。純計算，不排版、不畫圖。

    上個月沒有資料時（第一次跑、或那個月真的沒消費），比較欄位回 None，
    呼叫端據此整行不放 —— 拿 0 當基準會算出「+∞%」。
    """
```

回傳的 dict：

```python
{
    "total": 24135,
    "daily_avg": 779,
    "vs_last": {"delta": 2140, "pct": 9.7},      # 沒有上個月就是 None
    "shared_note": None,                          # 共同消費佔比 > 10% 時才有
    "top_categories": [("餐飲", 5032, 20.8), ...], # 最多 5 個
    "other_categories": {"count": 4, "amount": 651},
    "biggest": [("2026-09-04", "ＣＯＵＰＡＮＧ", 2837), ...],  # 3 筆
    "headline": "餐飲比上個月多了 1,340（+36%）",   # 變化最大的類別
}
```

### 5.2 信件內容

沿用 `digest.build_digest_html` 的區塊格式，五個區塊。圓餅圖用既有的
`spending_chart.build_pie` + `mailer.send_email(images={...})` 內嵌。

主旨：`📊 2026 年 8 月支出　NT$24,135（+9.7%）` —— 主旨就把最重要的兩個
數字講完，不開信也知道發生什麼事。

### 5.3 排程

```python
# UTC 00:30 = 台北 08:30。排在每日信（08:00）之後半小時，
# 錯開兩者對 Notion 與 Gmail 的呼叫。
MONTHLY_CRON = _cron_or_default("MONTHLY_CRON", "30 0 1 * *")
```

`misfire_grace_time` 設 86400（一天）：月報一年只有 12 次機會，漏一次要等
一個月，比每日信值得多補跑。

---

## 6. 測試

全部純邏輯，不碰網路。

| 測試檔 | 涵蓋 |
|---|---|
| `tests/test_daily_card.py` | 四個 line 函式：正常、空資料回 None、待辦超過 2 筆收成 +N、金額千分位 |
| `tests/test_monthly_report.py` | `build`：類別排序與 TOP 5 截斷、上個月沒資料時 `vs_last` 是 None、headline 挑變化最大的類別、共同消費 > 10% 才出 shared_note |
| `tests/test_daily_report.py`（既有） | `_load_personal_data` 只查一次 Notion；推播失敗不影響寄信 |
| `tests/test_flex_builder.py`（既有） | `daily_card_flex`：空行不放、四行都在、按鈕是 message 型 |

---

## 7. 不做的事

- **LINE 卡不放圓餅圖。** flex 的 hero image 要公開 URL，本機檔案得先上傳，
  而為了一張每天都在變的圖去架圖床，成本遠大於價值。要看圖就開信。
- **月報不推 LINE。** 圖是月報的主體，LINE 放不下；而且月報一個月只有一次，
  開信的動機本來就比每日信高。
- **不做「上週支出」。** 週不是使用者思考支出的單位，加了只是多一封信。
- **不改每日信的內容。** 使用者沒有抱怨過信太長，抱怨的是「要開信才看得到」。
  LINE 卡解決的是後者。
