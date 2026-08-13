# 交接文件 — Notion 財務中心 + 煮飯模板 + 全能大管家

**最後更新:** 2026-08-13
**狀態:** 已上線運作,部分功能未經真實使用驗證
**規格:** `docs/superpowers/specs/2026-08-10-notion-finance-kitchen-design.md`

> 給下一個 session 的人(或未來的我):先讀「⚠️ 未解問題」和「下一步」兩節,
> 其他部分當參考手冊查即可。

---

## 1. TL;DR

把 ReportRobot 從「股市大管家」擴充成「全能大管家」:

- **LINE 選單**改成分頁式,5 張選單 24 個入口,幾乎全部用按的
- **煮飯模板**:LINE 記錄食材 → 自動算分類/到期日/營養 → 每天早上提醒快過期的並建議煮什麼
- **財務中心**:每天 15:30 自動把國泰信用卡消費同步進 Notion,可查本月支出/最近交易/淨值
- 全部寫進 Notion,資料庫由程式自動建立

**15 個 commit,222 個測試,已部署於 Railway。**

---

## 2. 已完成(依 commit 順序)

| Commit | 內容 |
|---|---|
| `d2946d7` | `notion_db` 兩階段 relation 建立 + schema 遷移 + 9 個新 DB |
| `31d5643` | `kitchen.py` 純邏輯(採購解析/分類/保存期限/推薦) |
| `8717dfd` | 區塊子頁(💰 財務中心 / 🍳 煮飯模板) |
| `667c9d8` | 內建營養粗估表(**不接任何外部 API**) |
| `851abcb` | 分頁式 Rich Menu |
| `d645d69` | 煮飯指令接上 Notion |
| `f3abe3f` | `parsers/cathay_daily.py` 國泰消費彙整解析器 |
| `ec5346a` | `finance_sync.py` 端到端管線 + 15:30 排程 |
| `a04ac16` | merge 到 main(首次部署) |
| `ea7aab2` | `/admin/finance-sync` 手動觸發端點 |
| `dd469e7` | 修 Rich Menu「只有色塊沒有字」+ 缺字型時大聲失敗 |
| `f444081` | 每日推播加食材提醒 |
| `d8a4898` | 財務分頁五個按鈕接上實作 |
| `fe9208c` | 持倉/淨值快照寫入 + 採購清單 |
| `ddda56f` | 台股代號回填修正 |

---

## 3. 真實環境驗證狀態

**這一欄很重要 —— 寫完不等於能用。**

| 功能 | 驗證程度 |
|---|---|
| 消費彙整 parser | ✅ 用真實信件實跑,欄位逐項比對過 |
| 財務同步 + 去重 | ✅ 生產環境跑過 2 次,第二次 `written=0 skipped=7` |
| Notion 區塊子頁 + relation | ✅ 真實 API 建立成功,relation 已確認 |
| Rich Menu 分頁 + 字型 | ✅ 5 張選單建立成功,使用者確認有字 |
| 持倉 / 淨值快照 | 🟡 寫入成功,但**資料正確性有疑慮**(見未解問題) |
| 煮飯全部功能 | ❌ **完全沒有真實使用過** |
| 財務查詢 5 個按鈕 | ❌ **只用假資料跑過,沒在 LINE 上按過** |
| 採購清單 | ❌ 沒用過 |
| 每日推播的食材提醒 | ❌ 沒觸發過(要有快過期食材才會出現) |

---

## 4. ⚠️ 未解問題

### 4.1 持倉資料的完整性(最重要)

`get_portfolio_from_gmail()` 是**從抓取範圍內的信件重建持倉**,不是讀取庫存快照。

實測:目前只重建出 `AAPL 6股` 與 `SPCX 44股`。鴻海(2317)與 00632R 在範圍內被賣掉所以歸零 —— 這是對的;**但買很久、之後沒再交易的部位會整個消失**。

後果:
- 淨值快照會少算
- Notion 持倉表可能留下孤兒資料列

**Notion 持倉裡有一筆 `代號=台積電` 的孤兒資料,需要人工刪除**(程式端無法判斷它是孤兒還是「超出範圍的真實持倉」)。

**❌ 不要寫「自動清掉不在 portfolio 裡的資料列」** —— 因為範圍限制,那會刪掉真實持倉。

**建議解法:** 從最近一期月對帳單的**庫存欄位**做一次性初始化,而不是從成交紀錄累加。(規格第 10 節本來就列了這條。)

### 4.2 代號回填(已修,但不是主因)

`ddda56f` 修好了「對照表查不到就把中文名當 ticker」的問題(會導致誤判成美股、抓不到現價)。修正是對的,但**當初的台積電問題主因是 4.1 的範圍限制,不是這個**。診斷時我一開始判斷錯,記錄下來避免重蹈。

### 4.3 消費類別會自然增生

`類別` 是 Notion select,寫入未知選項時會自動新增。實測已多出「線上付款」「教育∕學費」——那是國泰原始值,程式沒有改寫。schema 遷移只新增不覆寫,所以使用者在 Notion 手動調整不會被蓋掉。

---

## 5. 下一步(使用者已決定的優先序:1 → 2 → 4)

目標是**簡潔好用**。研究結論:記帳系統被放棄的主因是「每天要手動輸入」,
所以優化方向是**減少操作次數**,不是增加功能。

### 1️⃣ 每日推播加「昨天花了多少」〔下一個要做〕

早上 7 點推播加一段:

```
💳 昨日消費 NT$1,290（3 筆）
　最大一筆:全聯 NT$839
```

多數日子連選單都不用點。資料都在 Notion 了,工作量小。

實作位置:`daily_report.py` 加 `_spending_yesterday()`(比照既有 `_kitchen_reminder()`),
`finance_report.py` 加 formatter,`flex_builder.daily_report_carousel()` 加一個 bubble 參數。

### 2️⃣ 「買了」改成常買清單 Quick Reply

現在要打「買了 高麗菜1顆」。改成按「買了」後回一組 Quick Reply 按鈕(最常買的 10 樣),點兩下完成。

⚠️ 已查證:`line_sender.py` 目前**沒有 quickReply 支援**,`_to_messages()` 要先擴充。

### 4️⃣ 推播直接附操作按鈕

食材提醒那則直接放「已用掉」按鈕。
`flex_builder.todo_list_flex()` 已有 postback 按鈕的現成寫法可抄,
`command_router.handle_postback()` 也現成。

### 其他(未排序)

- 另外 3 個 parser:國泰月帳單 / 繳款入帳 / 富邦轉帳(**含即時餘額,可維護現金餘額**)
- PDF 加密對帳單(台新 / 台北富邦 / New New Bank / 富邦證券月對帳單)
- 授權 → 入帳對帳(月帳單來時補正金額,規格 4.3)
- 選單精簡(把「更多」頁收掉)—— 這是取捨題,使用者尚未決定

---

## 6. 操作手冊

### 部署

Railway 接 GitHub **main 分支自動部署**。推 main = 上線。建置約 60–90 秒。

查狀態:
```bash
gh api repos/kengkeng44/ReportRobot/deployments --jq '.[0] | {sha:.sha[0:7], env:.environment}'
```

### Admin 端點

`ADMIN_TOKEN` 存在 Windows 使用者環境變數(也在 Infisical)。

```powershell
$t = [Environment]::GetEnvironmentVariable('ADMIN_TOKEN','User')
$h = @{ 'X-Admin-Token' = $t }
$base = "https://chengreportbot-production.up.railway.app"

# 手動跑財務同步(交易 + 持倉 + 淨值),重跑安全
Invoke-RestMethod -Method Post -Uri "$base/admin/finance-sync?days=7" -Headers $h

# 重建 Rich Menu(改了 MENUS 之後一定要跑)
Invoke-RestMethod -Method Post -Uri "$base/admin/setup-richmenu" -Headers $h

# 看持倉解析來源(debug 用)
Invoke-RestMethod -Uri "$base/admin/portfolio-debug" -Headers $h
```

### 排程

| 工作 | 台灣時間 | env |
|---|---|---|
| 每日推播 | 07:00 | `DAILY_CRON` |
| 財務同步 | 15:30 | `FINANCE_CRON`(預設 `30 7 * * *` UTC) |

15:30 的依據:實測國泰「消費彙整通知」每天 14:2x–14:5x 送達,富邦成交回報盤後約 14:25,
且台股 13:30 已收盤。**一天一次即足夠**,這些信一天只來一封。

---

## 7. 踩過的坑(別重蹈)

### 寫 parser 前一定要看真實信件

國泰消費彙整的結構是「**每筆交易 4 個 `tr`**(兩組 header/data 交錯)」,卡號在**另一個 table**。
照 snippet 猜必錯。抓信件的方法:Gmail MCP `get_thread` 會因為太大而存成檔案,
再寫小腳本用 BeautifulSoup 抽結構(輸出寫成 UTF-8 檔再讀,不要直接 print)。

### Windows console 是 cp950

印中文或 emoji 會 `UnicodeEncodeError`。驗證輸出要寫檔再用 Read 工具讀,
或設 `PYTHONIOENCODING=utf-8`。

### Rich Menu 的 PNG 不能放 emoji

用 CJK 字型畫,沒有彩色 emoji 字型,放了就是豆腐字。已加測試擋住。

### 缺字型要大聲失敗

原本 `_find_font` 找不到就 fallback `ImageFont.load_default()` —— 那是固定小點陣字型,
會忽略指定字級,在 2500px 圖上約 10px,等於看不見。結果**默默上傳了一張沒有字的選單**。
現已改成丟 `NoChineseFontError`。**默默產出壞掉的東西比直接失敗糟得多。**

Nixpacks 預設映像沒有 CJK 字型,靠 `nixpacks.toml` 裝 `fonts-wqy-zenhei`(13MB,
比 fonts-noto-cjk 的 200MB+ 省很多建置時間)。

### Notion select 不接受 `name=None`

估不出克數、猜不出分類時,該欄位要**整個不送**,否則整筆寫入失敗。
`pantry_add` / `transaction_add` 都有濾 None 的邏輯。

### Notion relation 要兩階段建立

`databases.create` 時 relation 的目標 `database_id` 必須已存在。
所以 `_SCHEMAS`(一般欄位)與 `_RELATIONS`(第二輪 `databases.update`)分開。

### 既有 DB 不會自動有新欄位

`get_or_create_db` 重用既有 DB 時不會補欄位,所以有 `_ensure_properties` 做遷移。
**只新增缺少的,不覆寫既有定義** —— 使用者可能已在 Notion 手動調過選項。

### 指令 regex 的順序會互相吃掉

`買好了 醬油` 會被 `^(?:買了|買|採買)` 的 `買` 吃掉。
shopping 的 regex 必須排在 `_PANTRY_ADD_RE` **前面**。已加驗證測試。

### 阻塞呼叫不能直接放進 async endpoint

`finance_sync.sync()` 是同步 HTTP(Gmail + Notion),可能跑數十秒。
直接在 async endpoint 裡 await 會卡住 event loop,**LINE webhook 會全部停擺**。
要用 `asyncio.to_thread`。

### PowerShell 貼密鑰

不要叫使用者貼進 `$env:X = "..."` 的雙引號 —— PSReadLine 高亮會把含 `$`/`"`/反引號的
token 弄壞。用 `Read-Host` 並存成 User 層級環境變數。

---

## 8. 架構地圖

```
server.py              FastAPI + APScheduler + LINE webhook + admin 端點
├─ daily_report.py     每日推播(天氣/盤前/今日一則/食材提醒)
├─ command_router.py   文字指令解析與分派(parse / handle / handle_postback)
│   ├─ _handle_kitchen()   煮飯 8 個指令
│   └─ _handle_finance()   財務 5 個指令
├─ finance_sync.py     Gmail → parser → 去重 → Notion;持倉/淨值同步
│   └─ parsers/
│       └─ cathay_daily.py   國泰消費彙整(唯一已完成的 parser)
├─ kitchen.py          煮飯純邏輯(解析/分類/期限/營養/推薦/格式化)
├─ finance_report.py   財務純邏輯(月結/最近/卡費/淨值/手動記帳)
├─ notion_db.py        所有 Notion 讀寫 + schema 定義 + 區塊子頁
├─ setup_richmenu.py   分頁式 Rich Menu(MENUS 常數 + 生圖 + 上傳)
├─ gmail_reader.py     Gmail OAuth + PDF 解密 + 持倉解析(既有)
└─ portfolio.py        現價/損益計算(既有)
```

### 設計原則(整份程式碼一致遵守)

1. **Notion 失敗一律 fallback,不 raise** —— 記帳寫入失敗不能拖垮每日推播
2. **解析不出來就跳過那一筆,不猜著填** —— 錯的資料比缺的資料更難發現也更難修
3. **沒資料時說明「為什麼沒有」** —— 只回「無資料」讓人分不出是壞了還是本來就空的
4. **純邏輯與 I/O 分離** —— `kitchen.py` / `finance_report.py` 不碰 Notion 也不碰 LINE,才好測

### Notion 結構

```
ReportRobot（根頁,NOTION_PARENT_PAGE_ID）
├─ Todos / Reminders / LineQuota      ← 核心 DB,已在線上跑,不要搬家
├─ 💰 財務中心
│   └─ 帳戶 / 交易明細 / 信用卡帳單 / 持倉 / 淨值快照
└─ 🍳 煮飯模板
    └─ 食材庫存 / 食譜 / 本週菜單 / 採購清單
```

`信用卡帳單` 等 DB 是 lazy 建立,第一次被存取時才出現。

---

## 9. 環境變數

既有的見 `.env.example`。本次新增:

| 變數 | 用途 | 預設 |
|---|---|---|
| `FINANCE_CRON` | 財務同步排程(UTC crontab) | `30 7 * * *` |

`NOTION_TOKEN` / `NOTION_PARENT_PAGE_ID` / `TOKEN_PICKLE_B64` / `PDF_PASSWORD_PREFIX` /
`ADMIN_TOKEN` 皆已存在 Infisical,自動 sync 到 Railway。

> 註:`TOKEN_PICKLE_B64` 優先於本機 `token.pickle`,所以本機 token 失效
> **不影響 Railway**。曾誤判過這點。

---

## 10. 使用者待辦(需要人工)

- [ ] 刪除 Notion 持倉裡 `代號=台積電` 的孤兒資料列(程式無權限刪除)
- [ ] LINE Official Account Manager 改帳號名稱為「全能大管家」(**無 API 可改**)
- [ ] 在 LINE 私訊實測煮飯與財務按鈕(這兩塊完全沒被真實使用過)
- [ ] 考慮把台北富邦設為薪轉戶(目前是郵局,郵局不寄任何電子通知,薪水只能手動記)
