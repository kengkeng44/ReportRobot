# ReportRobot v4:每日播報幽默化 + 月消費統整 — 設計規格

- **日期**:2026-08-03
- **狀態**:設計已批准,待寫實作計畫
- **repo**:kengkeng44/ReportRobot(股市大管家 / LINE 每日播報 bot)

## 1. 目標

在不動現有天氣、盤前、持股核心邏輯的前提下,新增兩塊功能:

- **功能 A — 每日播報 v4**:每日 08:00 的 Flex Carousel 由 2 張卡擴成 3 張,新增「今日一則」卡(小知識/笑話 + 節日祝福 + 有趣/天氣/颱風新聞),並在既有盤前卡加上股市新聞。
- **功能 B — 月消費統整**:新增 `/本月消費` 指令,讀信用卡消費通知信、加總本月花費,回一張 Flex 卡片。

兩功能彼此獨立,可分開上線。全部複用既有程式碼與已付費的 AI,**不申請任何新 API key**。新增第三方相依只有 `holidays`(判斷台灣節日)。

## 2. 功能 A — 每日播報 v4

### 2.1 卡片結構(3 張)

| 卡片 | 內容 | 變動 |
|---|---|---|
| ① 今日一則 | 小知識/笑話(每日輪流)＋ 節日祝福(遇節日同卡)＋ 有趣/天氣/颱風新聞一則 | 全新 |
| ② 天氣 | 現有天氣預報 | 不變 |
| ③ 股市盤前 | 現有盤前分析 ＋ 新增市場新聞摘要 | 加料 |

### 2.2 新模組 `humor.py`

對外單一入口:

```
get_daily_extra() -> str | None
```

組出「今日一則」卡片的文字內容,任一子區塊失敗只略過該區塊、不整卡失敗;全部失敗回 `None`(由 daily_report 決定是否略過該卡)。

內部三個子區塊:

1. **小知識/笑話(輪流)**
   - 依日期決定型別:`today.timetuple().tm_yday % 2 == 0` → 小知識,否則笑話。以日期決定(非亂數)確保可測試、同一天重跑結果一致。
   - 用既有 `anthropic` client(比照 `stock_news.py` 的呼叫方式)產生一則短中文內容。prompt 放進 `prompts.py`(新增 `DAILY_TRIVIA_PROMPT` / `DAILY_JOKE_PROMPT`)。

2. **節日祝福**
   - 用 `holidays` 套件(`holidays.Taiwan(...)`)判斷 `today` 是否為台灣節日;是則取節日中文名,叫 AI 生一句對應祝福(新增 `FESTIVAL_GREETING_PROMPT`)。非節日則此區塊留空。

3. **有趣/天氣/颱風新聞**
   - 用 §2.4 的通用新聞 helper 搜「颱風」「天氣」近期新聞,取數則標題餵 AI,請它挑最有趣/最該知道的一則,用一句白話講重點。
   - 無相關新聞時,fallback 搜一個較廣的有趣主題墊檔;仍無則此區塊留空。

### 2.3 盤前卡加市場新聞

- 卡片 ③ 在盤前分析下方附「今日市場新聞」數則標題(1–3 則)。
- 來源:既有 `stock_news.get_cnyes_news`(鉅亨網 `tw_stock` 分類為市場總覽新聞,非個股)。
- 由 `daily_report.py` 額外 gather 這段,經 `_safe` 包住,傳給 carousel builder;抓不到就不顯示這段,不影響盤前本體。

### 2.4 通用新聞 helper(對 `stock_news.py` 的targeted 改善)

現況:`get_google_news(stock_id, stock_name, limit)` 綁個股,內部自組 Google News RSS 查詢。

改法:把「給定查詢字串 → feedparser 抓 RSS → 回標題清單」的核心抽成模組級函式:

```
_google_news_rss(query: str, limit: int = 10) -> list[dict]
```

`get_google_news` 改為呼叫它;`humor.py` 也呼叫它(query 傳「颱風 天氣」等關鍵字)。純內部重構,對外行為不變。

### 2.5 `daily_report.py` 變動

- 新增 gather:`extra_text = _safe("今日一則", humor.get_daily_extra)`。
- 新增 gather:`market_news = _safe("市場新聞", _fetch_market_news)`。
- 呼叫改版後的 carousel builder(見 §2.6)。

### 2.6 `flex_builder.py` 變動

- `daily_report_carousel` 簽名擴充:由 `(weather_text, premarket_text, today)` → `(extra_text, weather_text, premarket_text, market_news, today)`。
- 新增「今日一則」bubble builder。
- 盤前 bubble 增加市場新聞區塊(有才顯示)。
- 降級規則沿用現有:任一段 `None` → 該 bubble 顯示降級文案;全部 `None` → fallback 純文字。

## 3. 功能 B — 月消費統整

### 3.1 新模組 `expenses.py`

```
get_monthly_spending() -> dict
# 回 {"total": int, "count": int, "items": [{"date": str, "merchant": str|None, "amount": int}, ...]}
```

- **Gmail 存取**:複用 `gmail_reader.get_gmail_service()` 與 `gmail_reader._get_email_body(payload)`,不重寫登入。
- **撈信**:Gmail query 篩「發卡行消費通知寄件人」+ `after:<本月1號>`(用 `tz_utils` 算台北時區月初)。
- **抽金額**:正規表示式從信件內文抽每筆消費金額,加總。
- **月界線**:以台北時區當月 1 號 00:00 為起點。

### 3.2 待實作時鎖定的未知項

發卡行寄件人位址與通知信金額格式**因銀行而異**,無法先寫死。實作第一步:

1. 用 bot 的 Gmail(`get_gmail_service`)撈一封近期消費通知信,或由使用者貼一封範例。
2. 依真實格式寫 regex,存一份去識別化(金額可保留、個資遮除)的樣本當測試 fixture。
3. 對 fixture 寫單元測試。

此為唯一的實作期未知項,已有明確解法,不阻擋設計定案。

### 3.3 指令與卡片

- `command_router.py` 新增 `/本月消費`(可加別名如 `/花費`)→ 呼叫 `expenses.get_monthly_spending()` → `flex_builder` 組卡。
- 卡片顯示:本月累計總額 + 筆數;行有餘力列「前幾大筆」。
- **第一版只加總,不做分類**(餐飲/購物等分類複雜,列為後續)。

### 3.4 錯誤處理

- 查無通知信 → 卡片顯示「本月尚無消費通知」。
- 解析失敗 → log + `notify_admin`,不讓整個指令炸掉。

## 4. 測試

- `humor.py`:固定日期測節日偵測、測小知識/笑話輪流的日期判斷、測新聞 fallback;AI 呼叫可 mock。
- `stock_news._google_news_rss`:重構後回歸測 `get_google_news` 行為不變。
- `expenses.py`:對 fixture 樣本信測 regex 加總正確;測月界線篩選(上月的信不算)。
- 手動驗收:`force` 觸發每日報看 3 卡片;打 `/本月消費` 看卡片。

## 5. 上線與風險

- 三塊(今日一則卡、盤前市場新聞、月消費)彼此獨立,可分批上線。
- 不動天氣、盤前、持股核心邏輯,風險低。
- 新相依 `holidays` 加進 `requirements.txt`。
- Railway 唯讀檔案系統:`expenses.py` 不寫檔(純即時查詢),無持久化需求。

## 6. 待決/後續(非本次範圍)

- 消費「分類」(餐飲/購物/交通…)。
- 消費資料持久化到 Notion(比照現有 todos/reminders 持久化模式)做趨勢圖。
- 卡片 1 有趣新聞的「廣度主題」清單可日後擴充。
