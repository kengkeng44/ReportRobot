#  LINE 情報機器人 (ReportRobot)

每天台灣早上 8 點自動推播每日情報到 LINE 群組，**並且支援即時對話查詢個股、ETF、持倉、待辦提醒**。

含 HMAC webhook 驗章、log 脫敏、外部 API exponential backoff retry、graceful degradation、管理員錯誤通知、排程冪等性等生產等級的資安／可靠性／可觀測性補強。

## 推播內容

| 段落 | 內容 |
|---|---|
| 🌤️ **淡水區天氣** | 整體總結、6 時段逐時概況、今日重點提醒、📅 近期活動（AI web_search） |
| 📊 **盤前報告**（週末略過） | 🌍 國際指數（道瓊 / S&P / Nasdaq / 費半 / TSMC ADR / NVDA）<br>💱 匯率原物料（USD/TWD / DXY / USD/JPY / 油 / 金）<br>🏛️ 三大法人買賣超（外資 / 投信 / 自營）<br>🧠 AI 盤前重點 8-10 條（Fed / 總經 / 地緣 / 類股 / 法說會） |

## 互動指令

直接在 LINE 群組打就會回應：

| 類別 | 範例 | 說明 |
|---|---|---|
| 個股查詢 | `2330` / `/2330` / `查2330` | 完整報告（簡介、股價、新聞、論壇、AI 解讀）|
| 美股 | `AAPL` / `/aapl` | 純大寫直接打、小寫加 `/` |
| ETF | `0050` / `SPY` / `00631L` | 含前 5 大持股（中文名）|
| 中文公司名 | `/鼎天` / `查台積` | 反查 twstock 拿代號 |
| 比較 | `/比較 0050 0056 1y` | 兩檔績效對比 |
| 自由問答 | `/Fed 最新動向` | Sonnet + web_search 回答 |
| 持倉 | `仁和持股` / `持股` | 全持倉現價損益 |
| 用量 | `/cost` / `/用量` | AI 累積成本 |
| 說明 | `/help` / `說明` / `?` | 完整指令清單 |

**1 對 1 chat 額外指令**（在群組打會被擋下）：

| 範例 | 說明 |
|---|---|
| `/待辦 加 X` / `/待辦 加X` | 新增待辦 |
| `/待辦` | 看清單 |
| `/待辦 完成 1` | 完成（自動移除）|
| `/提醒 30 分鐘後 X` / `/提醒 一分鐘後 X` | 一次性提醒（中文數字 OK）|
| `/提醒 明天 9:30 X` | 絕對時間提醒 |
| `/提醒` | 看所有進行中提醒 |

**保守觸發策略**：`hi` / `ok`、純中文聊天、「我買台積」這種句子都不會觸發。短英文與無前綴中文不視為指令，避免家人聊天被打擾。

## 個股報告完整內容

`/2330` 會收到：

```
📌 2330 台積電
📖 簡介（AI 1-2 句）
💰 股價（現價 + 日 / 5日 / 月漲跌）
📦 ETF 前五大持股（僅 ETF 才有）
📊 基本面分析（僅台股個股，AI web_search）
   📈 月營收 / 💰 季 EPS / 🔄 業務動態 / 📌 觀察重點
📰 最新新聞（Yahoo + 鉅亨網，英文標題附中文翻譯）
🗣️ PTT Stock 熱門
🌐 英文論壇（Reddit + StockTwits 綜合熱度）
🎴 Dcard 股票版
🤖 新聞 + 論壇 AI 解讀
```

## 設計亮點

> 個人 side project · 約 4,000 行 Python · 19 個模組 · LINE webhook + 排程整合 · 部署於 Railway

**解決的痛點**：早上要分別查股價、看新聞、查天氣太瑣碎；券商對帳單是加密 PDF 沒辦法直接看持倉；想查個股還要打開 App、找代號、看新聞、滑論壇、抓 ETF 成分。一個 LINE bot 全部解決，且家人也能直接用。

### 技術亮點

1. **三角部署：Web service + 內建排程 + LINE webhook**
   - 改寫成 FastAPI long-running web service（取代 cron-and-exit），用 `apscheduler` 內建排程跑每日推播，同時 server 接 LINE Messaging API webhook 處理互動指令
   - HMAC-SHA256 簽章驗證 LINE webhook，避免任何人都能戳 endpoint
   - `/admin/run-daily` 預留手動觸發 endpoint（X-Admin-Token header 保護），含 `?force=1` bypass 週末檢查方便測試

2. **Gmail → 持倉狀態管線（雙市場支援）**
   - **複委託（美股/港股）**：OAuth2 抓信 → `pikepdf` 解密 → `pdfplumber` 抽純文字 → 雙重防呆解析（避免把 `USD`/`PDF`/`SEC` 誤判為股票）
   - **台股日成交回報**：純 email 內文，regex 兼容 plain text 與 HTML 兩種寄送格式
   - **台股月對帳單**：自動辨識零股 vs 整股欄位差，並用 `成交股數 × 成交價格 ≈ 成交金額` 反推驗證解析結果
   - **雙 pass 對照**：先解析日報建立「證券名稱↔代號」對照表，再回頭把月對帳單只有名稱的紀錄回填正確 4-6 位代號（含槓桿 ETF 如 `00631L`）

3. **多源資料融合 + 智慧 fallback**
   - 股價：`yfinance` 一次抓 3 月 chart 算出當日/5 日/30 日漲跌
   - 上市/上櫃自動判斷：`twstock.codes[ticker].market` 決定 `.TW` 或 `.TWO` 後綴（鼎天 3306 是上櫃，要 `.TWO` 才抓得到）
   - 中文公司名反查：`twstock` 內建 46k 對照表，`/鼎天` 自動 → 3306
   - ETF 前五大持股：`yfinance.funds_data.top_holdings`（Yahoo `quoteSummary` 直接打需 crumb，yfinance 內建處理）
   - 天氣：CWA F-D0047-071 鄉鎮預報 + F-C0032-001 36 小時備用 fallback
   - 新聞：Yahoo Finance RSS + 鉅亨網 API
   - 論壇：PTT Stock + Reddit (r/stocks + r/wallstreetbets) + StockTwits + Dcard

4. **AI 多模型分工 + Cache 控制成本**
   - **Sonnet 4.5 + web_search**：盤前重點、近期活動、台股基本面分析（搜近 7 天事件）
   - **Sonnet 4.5（無工具）**：天氣報告整理、新聞 + 論壇解讀
   - **Haiku 4.5（無工具）**：個股簡介（1-2 句，便宜快速）
   - **基本面 6 小時 cache**：月營收/季 EPS 不會頻繁變動，同檔股票一天內多次查走 cache，month-cost 從 ~$30 降到 ~$3

5. **OAuth Token 雲端化**
   Railway 唯讀檔案系統無法寫回 `token.pickle`，本機 OAuth 完成後將 token base64 編碼存進環境變數 `TOKEN_PICKLE_B64`。`_load_creds()` 優先讀環境變數、退回本機檔案，**同份程式碼支援本機開發與雲端部署**。

6. **Secrets 管理走 Infisical**
   所有敏感變數（LINE token、Anthropic key、Gmail token...）統一在 Infisical 維護，自動 sync 到 Railway。

7. **指令解析的隱私守則**
   `command_router.py` 採取保守觸發策略：
   - 純大寫英文 ≥ 2 字才視為美股代號（避免家人講 `hi`、`ok` 觸發）
   - 中文反查必須有 `/` 或 `查` 前綴（避免「我買台積電」觸發）
   - 不認得的指令一律靜默不回應（不要動不動回「無此指令」吵到家人聊天）

8. **資安、可靠性、可觀測性**
   - **HMAC-SHA256 webhook 簽章驗證**：拒絕任何未經 LINE 平台簽章的偽造請求（用 `hmac.compare_digest` 防 timing attack）
   - **OAuth 2.0**：Gmail 走標準 OAuth flow，refresh token 自動換新 access token
   - **Secrets 管理**：Infisical 集中維護 → Railway 注入 → 不入版控；`config.py` 與 `token.pickle` 都列入 `.gitignore`
   - **日誌脫敏**（`security_utils.mask`）：LINE userId / groupId、token 等以 `頭4***尾4` 格式輸出，避免敏感字串外洩到 Railway log
   - **外部 API exponential backoff retry**（`http_utils`）：對 timeout / 429 / 5xx 自動重試 3 次（2→4→8 秒），4xx 不重試；用 `tenacity`
   - **Graceful degradation**：每段內含降級（CWA 主備、quote N/A、AI 失敗回降級文案）；段落層級失敗時仍推「⚠️ 該段資料暫時無法取得」讓使用者知道
   - **管理員錯誤主動通知**（`admin_notify`）：retry 用盡、排程錯誤、command 例外都會 push 到 `ADMIN_LINE_USER_ID`；同錯誤 5 分鐘 throttle 避免通知洪水；自身崩潰只往 stderr 不影響主程式
   - **排程冪等性**：`apscheduler` 加 `coalesce=True` + `misfire_grace_time=300`，搭配每日 flag 檔，避免服務重啟踩到排程點導致重複推播

### 架構

```
LINE Group ←─┐
              │
              ▼
   ┌──────────────────────┐
   │  Railway 24/7 web    │
   │  uvicorn server:app  │
   │                      │
   │  ┌────────────────┐  │
   │  │ FastAPI        │  │
   │  │ /line/webhook  │ ◄─── LINE 訊息事件 (HMAC 驗章)
   │  │ /admin/...     │  │
   │  └────────────────┘  │
   │  ┌────────────────┐  │
   │  │ apscheduler    │ ───► 每日 08:00 推送
   │  └────────────────┘  │
   └──────────┬───────────┘
              │
              ├──► LINE Messaging API (push / reply)
              ├──► Gmail API (持倉)
              ├──► Yahoo / yfinance (股價 / ETF 持股)
              ├──► 中央氣象署 / OpenWeatherMap (天氣)
              ├──► 證交所 OpenAPI (三大法人)
              ├──► PTT / Reddit / Dcard / StockTwits (論壇)
              └──► Anthropic Claude (簡介 / 解讀 / 盤前 / web_search)
```

## 模組

| 檔案 | 說明 |
|---|---|
| `server.py` | FastAPI app + apscheduler 排程 + LINE webhook handler + admin endpoint |
| `daily_report.py` | 組裝每日推播：天氣 → 盤前報告，每段獨立 try/except 保護 |
| `command_router.py` | LINE 訊息解析：`/2330`、`查台積`、`仁和持股`、`/help` 多種觸發 |
| `weather.py` | CWA 鄉鎮預報 + OpenWeatherMap 輔助 + AI 整理 + 近期活動 web_search |
| `markets.py` | Yahoo Finance v8 chart API 通用報價 wrapper |
| `premarket.py` | 盤前報告組裝（國際指數 + ADR + 匯率原物料 + 三大法人 + AI 重點），週末 skip |
| `chips.py` | 證交所 OpenAPI 抓三大法人買賣超，5 日 fallback 走過假日 |
| `stock_news.py` | 個股報告：股價、簡介、ETF 持股、基本面、新聞、論壇、AI 解讀 |
| `gmail_reader.py` | 抓 Gmail 對帳單，雙市場 + 雙 pass 解析持倉 |
| `portfolio.py` | 持倉現價/損益計算 |
| `line_sender.py` | LINE Messaging API push / reply 包裝（HTML strip + 切片） |
| `personal.py` | 個人待辦 / 提醒邏輯（in-memory + apscheduler 排 one-shot） |
| `app_state.py` | 跨模組共享 scheduler ref（避免循環 import） |
| `compare.py` | 兩檔績效比較（IX 指數對照、yfinance 算漲跌） |
| `free_query.py` | 自由格式中文 query → Sonnet + web_search |
| `usage_tracker.py` | 累積 AI / web_search 用量與估算成本 |
| `prompts.py` | 集中管理所有 AI prompt |
| `http_utils.py` | requests 包 tenacity：timeout / 429 / 5xx 自動 exponential backoff retry |
| `security_utils.py` | log 脫敏 utility（`mask` / `mask_source`） |
| `admin_notify.py` | 管理員錯誤通知（含 5 分鐘 throttle，retry 用盡 / 排程失敗時主動 push）|
| `main.py` | 本機手動測試入口（部署用 server.py） |

## 技術棧

- **語言**：Python 3.11+
- **Web**：FastAPI + uvicorn
- **排程**：APScheduler（內建在 server.py，不依賴 Railway cron）
- **AI**：Anthropic Claude (sonnet-4-5 + haiku-4-5)，含 `web_search_20250305` 工具
- **股價 / 財報**：yfinance（含 ETF funds_data.top_holdings）
- **台股對照**：twstock（內建 46k 上市櫃對照表，含 ETF 中文名）
- **API**：LINE Messaging API、Gmail API (OAuth2)、CWA Open Data、OpenWeatherMap、TWSE OpenAPI
- **PDF**：pikepdf（解密）、pdfplumber（抽文字）
- **可靠性**：tenacity（exponential backoff retry）、apscheduler（冪等排程）
- **資安**：hmac（webhook 驗章）、log mask、Infisical secrets
- **部署**：Railway 24/7 web service
- **Secrets**：Infisical → Railway sync

## 環境變數

LINE
| 變數 | 用途 |
|---|---|
| `LINE_CHANNEL_TOKEN` | LINE Messaging API push / reply |
| `LINE_CHANNEL_SECRET` | LINE webhook HMAC-SHA256 簽章驗證 |
| `LINE_GROUP_ID` | 每日推播目標群組 ID（C 開頭 33 字元）|

Gmail / 對帳單
| 變數 | 用途 |
|---|---|
| `GMAIL_USER` | Gmail 帳號 email |
| `TOKEN_PICKLE_B64` | Gmail OAuth token base64（雲端必填）|
| `PDF_PASSWORD_PREFIX` | 富邦對帳單 PDF 解密密碼前 4 碼 |

外部 API
| 變數 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API |
| `CWA_API_KEY` | 中央氣象署 |
| `OWM_API_KEY` | OpenWeatherMap |

設定
| 變數 | 用途 |
|---|---|
| `MANUAL_STOCKS` | 預留：手動追蹤股票清單（逗號分隔）|
| `WEATHER_LOCATIONS` | 天氣地點，逗號分隔（例：`淡水區,金山區`）|
| `ADMIN_TOKEN` | `/admin/run-daily` endpoint 保護用 |
| `ADMIN_LINE_USER_ID` | 管理員錯誤通知目標（自己的 LINE userId）；未設定則 notify_admin 變 no-op |
| `DAILY_CRON` | 排程 cron 表達式（預設 `"0 0 * * *"` = UTC 00:00 = 台北 08:00）|
| `PYTHONUNBUFFERED` | 設 `1`，讓 print 即時顯示在 Railway log |

## 部署

1. **連 GitHub repo**：Railway → New Project → Deploy from GitHub → 選此 repo
2. **建 Public Domain**：Railway → Settings → Networking → Generate Domain
3. **設環境變數**：（建議用 Infisical sync）對應上方清單
4. **拿 LINE_GROUP_ID**：把 bot 加進目標群組 → 群組打字 → Railway log 找 `[webhook] message ... source={'type': 'group', 'groupId': 'C...'}` → 複製到 Infisical
5. **設 LINE Webhook URL**：LINE Developers → Messaging API → Webhook URL → `https://<railway-domain>/line/webhook` → Use webhook ON → Verify

部署成功後：
- 每天 08:00 自動推送
- LINE 群組打 `/2330` 等指令立刻回應
- `https://<railway-domain>/admin/env-check` 可檢查環境變數是否正確 sync 到 Railway

## 監控 AI 用量與費用

### 從 LINE 群組查（最方便）

在群組打 `/cost` 或 `/用量`，bot 會回應：
- 各模型呼叫次數、input/output tokens
- web_search 呼叫次數
- 估算累計費用（USD）

### 從 PowerShell 查（不想暴露給家人時）

```powershell
Invoke-RestMethod -Method Get `
  -Uri "https://chengreportbot-production.up.railway.app/admin/cost-stats" `
  -Headers @{ "X-Admin-Token" = "你的 ADMIN_TOKEN" }
```

回 JSON 含相同資料 + `note` 欄位說明。

### 計算方式

`usage_tracker.py` 在每次 Anthropic SDK call 後抽 `message.usage` 累加：
- Token 計費：`PRICING` 表內建 Sonnet 4.5 `$3/$15`、Haiku 4.5 `$0.80/$4`（per 1M）
- Web search：每次 `$0.01`

⚠️ in-memory counter，**Railway redeploy 會歸零**。看當期趨勢用，月底總帳對 [Anthropic Console](https://console.anthropic.com/) 為準。

## 排程時間

`server.py` 用 apscheduler，預設讀 `DAILY_CRON` 環境變數：

```
DAILY_CRON = "0 0 * * *"   # UTC 00:00 = 台北 08:00
```

要改時間直接改 Infisical 的 `DAILY_CRON` 變數。

## Roadmap / 待辦清單

### v1 已完成

- [x] 每日 LINE 群組推播（天氣 + 盤前報告，每天台北 08:00）
- [x] LINE 互動指令：個股、ETF、持倉、比較、自由問答、用量、help
- [x] 中文公司名反查、上市/上櫃自動判斷（`.TW` / `.TWO`）
- [x] ETF 前五大持股（台股 / 美股都顯示中文名）
- [x] 台股基本面分析（月營收 / 季 EPS / 業務動態，24h cache）
- [x] AI 用量追蹤 `/cost`（含成本估算）
- [x] 1 對 1 待辦清單（完成自動移除，每天 06/09/12/18 點未完成自動提醒）
- [x] 1 對 1 自訂提醒（`/提醒 30 分鐘後 X` / `/提醒 明天 9:30 X`，支援中文數字）
- [x] 1 對 1 vs 群組權限分流（個人指令不在群組執行，避免家人看到）
- [x] 多行訊息一次處理多個指令
- [x] 資安補強：HMAC 驗章、log 脫敏（mask）
- [x] 可靠性補強：外部 API tenacity exponential backoff retry、graceful degradation 段落降級文案、排程冪等性（coalesce + misfire_grace + daily flag）
- [x] 可觀測性補強：管理員錯誤主動通知（含 5 分鐘 throttle 防通知洪水）

### v2 規劃（個人深度功能）

- [ ] **持久化儲存**（待辦/提醒搬到 Notion DB 或 Postgres，避免 redeploy 丟失）
- [ ] **信用卡帳單管家**：Gmail 抓信用卡帳單 → 月支出統計、各類別比例
- [ ] **多輪對話 AI 助理**：1 對 1 chat 不需要 / 前綴，bot 記住對話脈絡
- [ ] **健康/習慣追蹤**：`/喝水` `/運動` `/睡眠` + 月底自動報告
- [ ] **Google Calendar 整合**：「明天行程」、「/排 週四 9 點開會」
- [ ] **Notion 雙向同步**：LINE 待辦 ↔ Notion DB

### v3 規劃（即時警示與整合）

- [ ] **持股漲跌即時警示**（突破 ±5% 自動 push，不用等隔天）
- [ ] **重要 Gmail 即時轉發**（特定寄件人）
- [ ] **CWA 颱風 / 地震警示**
- [ ] **Notion 知識庫整合**：問題自動查 Notion 筆記
- [ ] **語音輸入**：LINE 傳語音 → bot 用 STT 解析成指令
- [ ] **多人 1 對 1 私人功能**（家人各自有自己的待辦/提醒）

## 授權

MIT License — 詳見 [LICENSE](LICENSE)。
