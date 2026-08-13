# 每日推播加「最近一天消費」bubble

**日期:** 2026-08-13
**狀態:** 設計定案,待實作
**相關:** `docs/HANDOFF.md` 第 5 節第 1 項、`docs/superpowers/specs/2026-08-10-notion-finance-kitchen-design.md`

---

## 1. 目標

每日 07:00 推播的 carousel 加一個 bubble,顯示最近一天的信用卡消費明細與本月累計。

動機:研究結論是記帳系統被放棄的主因是「每天要手動輸入」,所以優化方向是**減少操作次數**。
資料都已經在 Notion 了,把它推到眼前,多數日子連選單都不用點。

---

## 2. 為什麼不是「昨天花了多少」

HANDOFF 原本寫的是「昨日消費」。實際查過 `parsers/cathay_daily.py` 後發現做不到。

國泰世華「消費彙整通知」**每天一封,彙整前一日的刷卡授權**,而交易的 `date` 欄位取的是
信件裡的「授權日期」(真實刷卡日)。時間線:

```
8/12 刷卡
8/13 14:2x   國泰寄出彙整信(內容 = 8/12 的消費)
8/13 15:30   FINANCE_CRON 同步寫進 Notion,交易日期 = 2026-08-12
8/14 07:00   推播若要找「昨天」= 8/13 → Notion 裡還沒有
```

8/13 的消費要等 8/14 14:2x 那封信才會進來。**這是資料源的延遲,不是排程沒調好**——
把 `FINANCE_CRON` 提前到 07:00 前也一樣拿不到,因為信本來就晚一天寄。

所以標題不寫「昨日」,改成寫出**實際日期**。符合設計原則第 2 條(不猜著填)與第 3 條
(說明為什麼沒有)。

---

## 3. 輸出樣式

正常:

```
💳 最近一天消費
8/12（三）  NT$1,290  3 筆

・全聯          NT$839
・統一超商      NT$351
・便利商店      NT$100

本月累計 NT$24,300
```

資料過舊(超過 3 天):

```
💳 最近一天消費
8/08（五）  NT$1,290  3 筆

・全聯          NT$839
・統一超商      NT$351

⚠️ 已 5 天沒新消費資料
　可能是沒刷卡,也可能是同步中斷

本月累計 NT$24,300
```

完全沒有支出資料:**回 `None`,整個 bubble 不出現**。

---

## 4. 架構

採「純邏輯 + 呼叫端取數」,比照既有 `_kitchen_reminder()`。

```
07:00 排程 run_daily_report()
 └─ daily_report._spending_recent()                    ← I/O
     ├─ notion_db.is_configured() 為否 → None
     ├─ notion_db.transactions_load()
     └─ finance_report.format_latest_day_spending(txns, today)   ← 純邏輯
         └─ str | None
 └─ flex_builder.daily_report_carousel(..., spending_text=...)
```

用既有 `_safe("消費摘要", ...)` 包住:炸了只是少一個 bubble,不影響天氣與盤前。

**評估過但不採用的替代方案:**

- **同步時預算好摘要存進 Notion** — 推播只讀一個欄位。省一次查詢,但多一張表要維護,
  且同步失敗時摘要會停在舊值卻看不出來。
- **直接在 `flex_builder` 組** — 最少檔案,但把 Notion 查詢塞進畫圖層,違反既有分層,
  也沒辦法單測。

---

## 5. 純邏輯規格

### `finance_report.format_latest_day_spending(txns, today, stale_days=3, max_rows=5)`

回 `str` 或 `None`。不碰 Notion 也不碰 LINE。

1. 只取支出——沿用既有 `_is_spending(txn)`(`direction` 缺值時視為支出)
2. 忽略日期晚於 `today` 的資料列(資料髒掉時不該讓未來日期主導)
3. 忽略沒有合法 `date` 的資料列
4. 在剩下的資料中找**最大的日期**,取該日全部支出 → 總額、筆數
5. 明細依金額由大到小,最多 `max_rows` 筆;超過時最後補一行「…另 N 筆」
6. 附一行 `today` 所在月份的支出累計
7. `(today - 最新日).days > stale_days` → 加警告區塊,寫出實際天數與兩種可能原因
8. 過濾後沒有任何支出 → 回 `None`

**輸出區塊順序**(上列規則是判斷邏輯,不是排版順序):

```
標題
日期 / 總額 / 筆數
（空行）
明細列
（空行,僅在有警告時）
過舊警告
（空行）
本月累計
```

金額格式沿用既有 `_money()`。金額為 `None` 的資料列**計為 0 但仍算一筆**
(它確實是一筆消費,只是金額沒解析出來),該列金額顯示 `_money(None)` 的 `-`,
排序時視為 0 排在最後。

「本月累計」以 `today` 的月份為準,不是最新交易日的月份。月初幾天數字很小甚至是 0
是預期行為——它回答的是「這個月到目前為止花了多少」。

日期顯示 `M/DD（週幾）`,週幾用中文單字(一二三四五六日)。

### 為什麼沒資料時回 `None` 而不是說明文案

`finance_report` 既有的 `_EMPTY_MONTH` / `_EMPTY_RECENT` 那套文案要留著——那是使用者
**主動按按鈕查詢**時的回應,一定要回點東西,而且要講清楚為什麼空的。

推播不同:它每天自動來。剛啟用時 Notion 是空的,天天跳一則「還沒有紀錄」會讓人開始
略過整則推播。這跟 `_kitchen_reminder()` 在沒有快過期食材時回 `None` 是同一個理由。

---

## 6. 呼叫端與畫面

### `daily_report._spending_recent()`

比照 `_kitchen_reminder()`:在函式內 import `notion_db` 與 `finance_report`,
先查 `notion_db.is_configured()`,未設定就回 `None`。`today` 用 `tz_utils.today_tpe()`。

在 `run_daily_report()` 裡用 `_safe("消費摘要", _spending_recent)` 取值。

### `flex_builder.daily_report_carousel(..., spending_text=None)`

新增具名參數,預設 `None` 以維持既有呼叫相容。

bubble 順序:**今日一則 → 食材提醒 → 天氣 → 盤前 → 消費**。

排最後的理由:食材提醒是今天要動手的事,消費是回顧性資訊,優先度最低。

header 色 `#A66F6F`,與食材 `#6F9A62`、盤前 `#5B8DA6`、天氣 `_BROWN` 區隔。

---

## 7. 測試

新增 `tests/test_daily_spending.py`:

| 案例 | 期望 |
|---|---|
| 多筆同日支出 | 總額、筆數、明細依金額遞減 |
| 跨日資料 | 只取最新那天,舊的不混進來 |
| 只有收入 | 回 `None` |
| 空 list | 回 `None` |
| 金額為 `None` | 不炸,計 0 但算一筆 |
| 日期跨月 | 本月累計不含上月 |
| 日期晚於 `today` | 被忽略 |
| 最新日 = today - 3 天 | **不**出現警告 |
| 最新日 = today - 4 天 | 出現警告,天數寫 4 |
| 明細 7 筆、`max_rows=5` | 顯示 5 筆 + 「…另 2 筆」 |

`tests/test_flex_carousel.py` 補:給 `spending_text` 與不給時的 bubble 數差 1,
且消費 bubble 在最後。

---

## 8. 已知限制(不在本次範圍)

`notion_db.transactions_load()` 是 `page_size=min(limit, 100)` 且**沒有分頁**,
所以當月交易超過 100 筆時「本月累計」會少算。

既有的 `format_monthly_spending()`(財務分頁「本月支出」按鈕)早就吃同一份資料,
有完全相同的問題。這次不一起修,以免範圍擴散;要修的話應該在 `transactions_load()`
加 `start_cursor` 迴圈,一次解決兩處。

---

## 9. 不做什麼

- 不改 `FINANCE_CRON` 排程時間——改了也拿不到當日資料(見第 2 節)
- 不動 `finance_report` 既有的五個 formatter 與空資料文案
- 不加 quickReply 或 postback 按鈕(那是 HANDOFF 第 5 節第 2、4 項,各自獨立)
- 不碰 `transactions_load()` 的分頁問題(見第 8 節)
