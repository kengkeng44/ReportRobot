# 交接文件 — Notion 財務中心 + 煮飯模板 + 全能大管家

**最後更新:** 2026-08-25
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
| 持倉 / 淨值快照 | 🟡 2026-08-26 已接上庫存快照(見 4.1),**Railway 端還沒實跑驗證**。台銀金那筆待補 |
| 煮飯全部功能 | ❌ **完全沒有真實使用過** |
| 財務查詢 5 個按鈕 | ❌ **只用假資料跑過,沒在 LINE 上按過** |
| 採購清單 | ❌ 沒用過 |
| ~~每日推播的食材提醒~~ | 🗑️ 2026-08-16 從推播移除,改成指令「快過期」(含按鈕) |
| ~~每日推播的消費摘要~~ | 🗑️ 2026-08-16 從推播移除,改成指令「最新消費」 |
| 食材提醒的「已用掉」按鈕 | ❌ 沒在 LINE 上按過(23 個單元測試) |
| 「買了」常買清單 Quick Reply | ❌ 沒在 LINE 上按過(20 個單元測試);選單已於 2026-08-19 重建,按鈕本身生效了 |
| 「記一筆」兩段式 Quick Reply | ❌ 沒在 LINE 上按過(42 個單元測試);選單已於 2026-08-19 重建,按鈕本身生效了 |
| 個人版每日報改寄 email | ✅ **2026-08-26 使用者實測收到**。走 Gmail API(443),SMTP 埠被 Railway 擋死的問題見 4.6 |
| `/admin/env-check` token 保護 | ✅ 生產環境實測:無 token 403、帶 token 200 |
| 每日信卡片版型 + 本月消費明細 | 🟡 759 個測試綠、本機產過預覽,**Railway 端還沒實跑**。持股 / LINE 餘額 / 載具品項刻意沒接(見下) |
| 黃金改抓現貨(修少報 8%) | 🟡 本機實測 Gold-API 通、量級正確(4,611 vs 舊邏輯 ~4,254);**Railway 對外能不能連 gold-api.com 還沒驗** —— 連不到會 fallback `GLD × 10.84`,不開天窗但會回到有偏差的數字。看 2026-08-26 盤前報的黃金即可確認 |
| 共同消費分攤 + 第四態按鈕 | ❌ **沒在 LINE 上按過**;Notion 新欄位也還沒實跑建立 |
| 語句庫 / 金句庫(Notion 建表) | ❌ **尚未在真實 Notion 建過**,只有單元測試。表由 `ensure_all_dbs()` 開機時自動建 |
| 每日信「今日三句」 | ❌ 沒有真的收過信。語句庫是空的時候會走 AI 補位,那條路也沒實跑過 |
| 消費圓餅圖(內嵌圖片) | ❌ 沒有真的收過信。**Gmail 對 `cid:` 的呈現未驗證**,手機 App 尤其 |
| 金句搬遷(370 句) | ❌ `import_quotes.py` 只跑過單元測試,沒對真實舊 DB 跑過 dry-run |
| ~~每日信的本月逐筆明細~~ | 🗑️ 2026-09-04 由圓餅圖 + 近三天取代。`format_monthly_detail` 函式與測試刻意留著(無呼叫端) |
| ~~每日信的冰箱快過期~~ | 🗑️ 2026-09-04 從個人信移除(太吵)。記錄路徑沒動,查詢改打 LINE「快過期」 |
| 寄信失敗重試 | ❌ 沒遇過真的失敗。三次都失敗會丟例外進 `notify_admin`,那條路沒實跑過 |

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

#### 進度〔🟡 純邏輯做完,還沒接上真實資料〕

`holdings.py` 已完成並有 22 個測試:

- `build_portfolio(trades, snapshots)` —— 每個市場用自己那期的月底當 cutoff
  (實測複委託到 7 月、有價證券只到 6 月,共用一個 cutoff 會算錯)
- 快照日以前的成交跳過(已含在庫存裡);無日期的也跳過**不猜**,但記進
  `sources` 讓數字對不上時看得出原因
- 庫存解析為空不當成「真的沒持倉」,退回成交累加 —— 否則淨值直接歸零
- `describe_sources()` 在沒快照時講明為什麼可能少算
- **沒有任何刪除邏輯**(見上面那條紅線)

#### 2026-08-26:已接上真實資料並接進生產路徑

用 `/admin/statement-dump` 撈到真實格式後才動手(第 7 節)。撈出來發現
**兩個市場的情況完全不同**,原本規格寫的「從月對帳單庫存欄位初始化」
只有一半成立:

| 市場 | 庫存表 | 做法 |
|---|---|---|
| 美股(複委託) | ✅ 有「股票庫存明細表」 | `holdings.parse_us_inventory()` 直接 parse |
| 台股(有價證券) | ❌ **整份沒有** | 手動填 `TW_HOLDINGS` 環境變數 |

台股月對帳單只有交易明細、應收付、集保「異動」,富邦自己在信裡寫
「個人即時庫存餘額明細⋯⋯請登入網路交易系統或富邦e01查詢」。
**不要再花時間找台股的庫存表,它不在那份信裡。**

**美股版面的坑:** pdfplumber 攤平表格後欄位位置會飄,同一份對帳單裡就有
三種變形(名稱留在行內 / 被推到上一行 / 拆成上下兩行)。所以不能靠欄位序號,
`parse_us_inventory` 拿幣別欄 `USD TWD USD` 當錨點回推股數。

**庫存表沒有成本均價**,只有參考收盤價與參考市值。所以 `avg_cost` 一律 None
並一路傳到顯示端標「未知」——拿收盤價充數會讓未實現損益永遠是 0,
拿 0 充數會讓損益率變成爆賺。兩種都是「看起來正常的假數字」。

因此 `_compute_portfolio_data` 與 `build_portfolio_summary` 都把
**淨值**與**損益小計**拆成兩組累加器:成本未知的部位計入淨值(股票是真的持有,
漏掉就是本節要修的少算)但不計入損益(有市值沒成本會把整筆市值當成獲利)。

**接線(這才是原本真正缺的一塊):** `holdings.build_portfolio` 在此之前是
**孤兒程式碼** —— 22 個測試全綠,但沒有任何生產路徑會執行到它,
三個消費端全部還在走 `_aggregate_portfolio`。現在
`gmail_reader.get_portfolio_from_gmail` 改用 `_build_snapshots()` + `build_portfolio()`。

順帶修掉一個獨立的坑:原本 `if not items: return {}` —— Gmail 一有閃失
持倉就顯示成全部歸零,而不是退回已知庫存。

**真實資料實測**(2026-08-26,不進 repo):美股快照抽出 3 檔,其中一檔
是「買很久之後就沒再交易」那種,舊路徑完全抓不到 —— 正是本節描述的失效。

**還缺的:** Railway 端還沒實跑;台股 `TW_HOLDINGS` 只填了一筆,另一筆待確認。

```powershell
$t = [Environment]::GetEnvironmentVariable('ADMIN_TOKEN','User')
Invoke-RestMethod -Uri "https://chengreportbot-production.up.railway.app/admin/statement-dump" `
  -Headers @{ 'X-Admin-Token' = $t } | ConvertTo-Json -Depth 5
```

⚠️ 回傳含帳號與持倉,不要貼到公開的地方。

### 4.2 代號回填(已修,但不是主因)

`ddda56f` 修好了「對照表查不到就把中文名當 ticker」的問題(會導致誤判成美股、抓不到現價)。修正是對的,但**當初的台積電問題主因是 4.1 的範圍限制,不是這個**。診斷時我一開始判斷錯,記錄下來避免重蹈。

### 4.4 選單按鈕漏進付費 AI〔已修,但這個坑要記住〕

投資分頁的「比較」「盤前」「大盤」三格送的是裸指令(`/比較`、`/盤前`、
`/大盤`),`command_router` 都不認得,於是掉進 `free_query` —— 也就是
「不認得的中文指令丟給 AI 上網查」。按一次付一次 Anthropic 的錢,
買到一段跟股票無關的通用解釋。

`premarket` 與 `market` 這兩個 kind **從頭到尾就沒實作過**,
但 `markets.build_market_summary()` 與 `premarket.build_premarket_report()`
其實早就寫好了,只是沒接上指令。

**為什麼一直沒被發現:** free_query 這條 fallback 讓任何裸指令都「有反應」。
不會壞、不會有紅字、不會進 log 的錯誤區,只會安靜地花錢。

**現在有防護:** `test_invest_menu.py::test_no_menu_cell_falls_through_to_paid_ai`
掃過所有 5 張選單每一格 `message` 型的 cell,parse 出來是 `free_query`
就紅。之後再加按鈕不會重蹈。

順帶:`/比較` 只補一檔就送出也會漏到 free_query,已攔下來回用法。
`premarket._build_ai_summary()` 加了當日快取 —— 早上推播跑過一次之後,
按鈕不再重複付費。

### 4.5 信用卡通知看不到品項〔已有解法,等 AppID〕

**問題:**「今天買了什麼菜」在信用卡通知裡永遠查不到。
國泰彙整信只有「全聯福利中心－板橋板新　NT$361　超市∕量販」。

**這不是國泰偷懶:** 刷卡授權電文本身只傳「商店代號 + 總金額」給發卡行,
品項從來沒有進入過信用卡系統。換任何一張卡、任何一家銀行都一樣,
這條路是死的,不要再往這個方向試。

**實測信箱裡真正有品項的來源**(2026-08-16 掃過近半年):

| 來源 | 品項在哪 |
|---|---|
| 酷澎 Coupang | ✅ 直接寫在信件內文的表格 |
| 好市多線上購物 | 🟡 內文沒有,在 PDF 附件 |
| 麥當勞 App / ezPay / 綠界 | ❌ 只有總額或發票號碼 |
| 國泰消費彙整 | ❌ 只有商店名 + 金額 |

**實體店(全聯、超商)的品項在財政部平台上。** 使用者有手機條碼載具
`/EK***6VW` 且已啟用,結帳有出示的發票品項都在那裡。財政部只寄中獎通知,
不寄明細,要自己用 API 撈。

**已完成:** `einvoice.py` + 27 個測試 + `/買了什麼` 指令(15 個測試)。
用 `EINVOICE_APP_ID` / `EINVOICE_CARD_NO` / `EINVOICE_CARD_ENCRYPT`
三個環境變數 gate 住,沒設定就回「怎麼申請」的說明。

**還缺:** 使用者要自己申請 AppID(需本人身分驗證,程式端做不到)
→ https://einvoice.nat.gov.tw/APCONSUMER/BTC605W/

**踩過的坑:**
- 網路上流傳的 `openapi.yaml` 寫 `application/json` 是**錯的**。
  官方規格 v1.9 第一章四節明訂 `application/x-www-form-urlencoded`,
  而且 `appID` 是必填但 yaml 裡整個沒有。**照第三方 spec 寫會串不起來。**
- 表頭查詢 version 是 **0.6**(113/1/1 起),明細查詢仍是 0.5,兩支不同。
- `code 996` 是「還有下一頁」不是錯誤。不跟分頁會默默少算後面所有發票。
- `code 950` 是「超過最大查詢次數」—— 所以 `fetch_month` 有 `max_detail`
  上限,不能一次把額度燒光。
- 查資料時 `blog.user.today` 那篇分析文內嵌了 prompt injection,
  抓取工具正確拒絕了。**第三方部落格當規格來源要小心。**

**刻意沒做:** 酷澎 email parser。使用者買菜主要在全聯與超商,網購那條
涵蓋不到主要場景;而且那封信的 HTML 表格結構沒實際看過,
照 markdown 轉換版猜格式正是第 7 節警告的事。

### 4.6 Railway 擋 SMTP 對外埠〔已量測完,已改走 Gmail API〕

個人版每日報 2026-08-25 06:01 首跑就炸 `OSError [Errno 101] Network is unreachable`,
同一秒 `admin_notify` 的 LINE push(HTTPS 443)是通的。

`/admin/net-check`(commit `a7a9fc8`)實測結果:

| 目標 | IPv4 | IPv6 |
|---|---|---|
| `smtp.gmail.com:465` | ❌ **TimeoutError** | ❌ Errno 101 |
| `smtp.gmail.com:587` | ❌ **TimeoutError** | ❌ Errno 101 |
| `api.line.me:443`(控制組) | ✅ ok | — |

**結論:平台擋 SMTP 埠,不是 IPv6 沒路由。**

判準在「IPv4 是 timeout 而不是 connection refused」—— 被拒絕代表對方明確回應,
timeout 代表封包被靜默丟棄,這是防火牆的特徵(雲平台普遍擋 SMTP 防垃圾信)。
控制組 443 通過,證明容器有外網也有 IPv4 路由,問題精準隔離在這兩個埠。

**因此「強制走 IPv4」這條修法出局** —— 465 / 587 的 IPv4 都不通。
**任何用 `smtplib` 的寫法都不可能通**,得換成走 443 的方式。

**已定案並上線:走 Gmail API(443)**,commit `d15bffb`。
2026-08-26 使用者實測收到每日信 —— 這條管線通了,4.6 到此結案。

關鍵是**另開一顆只有 `gmail.send` 的 token**(`SEND_TOKEN_PICKLE_B64`,
由 `setup_send_token.py` 產),不碰既有那顆 readonly 的。在既有 token 上加
scope 得重跑授權換掉線上那顆,而財務同步 / 發票 / Gmail 警示全靠它 ——
爆炸半徑從三個功能縮到一個。

當時評估過但沒選的兩條,留著備查:
- HTTPS 寄信服務(Resend / SendGrid 等)—— `mailer.send_email` 換實作、介面不變
- 改回 LINE 推播 —— `flex_builder.personal_report_carousel` 還留著,接回去即可

`mailer.py` 本身沒有錯,是選錯傳輸方式。`is_configured()` 那套 gate 與
「沒設定就跳過不丟例外」的設計可以原樣沿用。

### 4.3 消費類別會自然增生

`類別` 是 Notion select,寫入未知選項時會自動新增。實測已多出「線上付款」「教育∕學費」——那是國泰原始值,程式沒有改寫。schema 遷移只新增不覆寫,所以使用者在 Notion 手動調整不會被蓋掉。

---

## 5. 下一步(使用者已決定的優先序:1 → 2 → 4)

目標是**簡潔好用**。研究結論:記帳系統被放棄的主因是「每天要手動輸入」,
所以優化方向是**減少操作次數**,不是增加功能。

### 1️⃣ 每日推播加「最近一天消費」〔✅ 做完，2026-08-16 又拿掉了〕

> **⚠️ 這張卡已從每日推播移除。** 使用者的理由:每天固定跳一段回顧性資訊
> 會稀釋掉推播真正要提醒的事,要看時自己問就好。
>
> 排版邏輯 `finance_report.format_latest_day_spending()` **沒有刪**,
> 改接到 LINE 指令「最新消費」(`fin_latest_day`)。22 個排版測試留在
> `test_daily_spending.py`,指令與「推播不該再有這張卡」的測試在
> `test_latest_day_command.py`。
>
> 下面是當初的設計脈絡,保留是因為「為什麼不是昨天」那段之後還會用到。



早上 7 點推播多一個 bubble:最近一天的消費明細 + 本月累計,超過 3 天沒新資料會講明原因。

⚠️ **不是「昨天」** —— 國泰消費彙整信每天彙整**前一日**,交易日期取的是授權日,
所以早上 7 點推播時昨天的資料還沒進 Notion(要等當天 14:2x 那封信)。
原本規劃的「昨日消費」寫死了會**每天都是空的**;把 `FINANCE_CRON` 提前也沒用,
信本來就晚一天寄。改成顯示資料裡最新的那一天並寫出實際日期。

規格:`docs/superpowers/specs/2026-08-13-daily-spending-bubble-design.md`
計畫:`docs/superpowers/plans/2026-08-13-daily-spending-bubble.md`

實作位置:`finance_report.format_latest_day_spending()`(純邏輯)、
`daily_report._spending_recent()`(取數)、`flex_builder.daily_report_carousel()`
的 `spending_text` 參數(bubble 排最後)。

### 2️⃣ 「買了」改成常買清單 Quick Reply〔✅ 已完成〕

只打「買了」會回一排常買清單按鈕,點一下送出「買了 高麗菜」,兩下完成。

- `kitchen.frequent_items()` —— 在庫 + 用完一起算次數。只看在庫的話,
  常買但剛好吃完的會從清單消失,而那正是最該出現在按鈕上的東西
- 沒歷史時用預設清單墊到 10 樣(第一天給空按鈕列等於功能不存在)
- `flex_builder.quick_reply_text()` —— 截到 13 顆、label 截到 20 字。
  LINE 是**整則退回**,寧可少一顆也不要整句送不出去
- `line_sender._to_messages()` 只留**最後一則**的 `quickReply`,
  LINE 也只認這一則,掛在前面的會靜默消失
- Rich Menu 的「買了」從 `prompt` 改成 `message`,不然永遠送不出裸指令
  → **改完要重跑 `/admin/setup-richmenu`**

補記:HANDOFF 原本寫「`_to_messages()` 沒有 quickReply 支援」,實際上
dict 是原樣通過的,真正缺的是「字串回覆沒辦法帶按鈕」與上面那條
只留最後一則的規則。

### 4️⃣ 推播直接附操作按鈕〔✅ 做完,2026-08-16 搬家了〕

> **⚠️ 這張卡已從每日推播移除**,按鈕搬到 LINE 指令「快過期」
> (`pantry_expiring`)。使用者的方向很明確:**推播只留三張**
> (今日一則 / 天氣 / 盤前),其餘要看時自己問。
>
> 每天固定跳的東西越多,整則推播越容易被整個略過 —— 跟當初
> 「沒事就不要佔一個 bubble」是同一個道理,只是標準又拉高了。
>
> `kitchen.expiring_actions()` 與 `flex_builder.kitchen_reminder_bubble()`
> 都沒刪,按鈕本身的測試留在 `test_push_action_buttons.py`,
> 指令端的測試在 `test_daily_kitchen.py`。

食材提醒每樣後面一顆「已用掉」,按下去等同打「用掉 X」。

- `kitchen.expiring_actions()` 濾掉沒 `page_id` 的 —— 按鈕定位不到
  Notion 那一列,放了也是按了沒事,比沒有按鈕更糟
- 最多 5 顆,被截掉的在卡片上寫明還有幾樣
- 撈不到 items 時退回原本的純文字 bubble,提醒不會消失
- **postback 只認 `ADMIN_LINE_USER_ID`**:每日情報推到家人群組,
  這顆按鈕誰都按得到,但庫存在 `_PERSONAL_KINDS` 裡是個人資料
- 隔天再按到會友善提示,不會重複寫 Notion 也不重複加採購清單

### 5️⃣ 「記一筆」兩段式 Quick Reply〔✅ 已完成 2026-08-19〕

規格:`docs/superpowers/specs/2026-08-19-manual-entry-quick-reply-design.md`
計畫:`docs/superpowers/plans/2026-08-19-manual-entry-quick-reply.md`

手動記帳本身早就能用,缺的只是入口。打「記一筆」→ 常記品項按鈕 →
點「午餐」→ 常用金額按鈕 → 點「120」→ 記完。與第 2 項同一個方向:
**減少操作次數**。

三件值得記住的事:

- **無狀態**:按鈕送出的文字本身攜帶進度(`記一筆` / `記一筆 午餐` /
  `記一筆 午餐 120`),三態靠 `arg` 內容判斷。不需要 session,
  使用者自己打完整指令也走同一條路,沒有兩套邏輯。
- **`transactions_load` 原本沒讀「來源」欄**。`transaction_add` 一直有寫,
  讀不回來只會安靜地得到 `None` —— 「只學手動記的帳」會把每一筆都濾掉,
  按鈕永遠停在預設六樣,看起來像學習沒生效而不是欄位沒讀。已修。
- **`parse_manual` 原本把類別寫死「其他」**。刷卡的餐飲進「餐飲」、
  手打的午餐進「其他」,同一件事被拆兩邊,本月支出的百分比隨使用量失真。
  現在吃的自動歸「餐飲」,但**不自創國泰沒有的類別**,所以「搭車」仍是「其他」。

### 其他(未排序)

- 另外 3 個 parser:國泰月帳單 / 繳款入帳 / 富邦轉帳(**含即時餘額,可維護現金餘額**)
- PDF 加密對帳單(台新 / 台北富邦 / New New Bank / 富邦證券月對帳單)
- 授權 → 入帳對帳(月帳單來時補正金額,規格 4.3)
- 選單精簡(把「更多」頁收掉)—— 這是取捨題,使用者尚未決定

### 台股量能/籌碼指標〔擱置,程式還在分支上〕

**分支 `claude/verify-todays-data-47ntak` 不要刪** —— PR #2 已於 2026-08-25 關閉,
但那個分支保留著 `4270fee`,裡面是**已經試出來的 TWSE / TAIFEX API 端點**
(`market_stats.py` 244 行 + `taifex.py` 102 行 + 盤前報區塊 + `/admin/probe-indicators`)。
內容:成交金額、漲跌家數、融資餘額、台指夜盤、外資期貨未平倉。

**為什麼沒合(三個理由,都要解掉才值得撿回來):**

1. **479 行零測試** —— 這個 repo 連 86 行的 `mailer.py` 都配 8 個測試
2. **欄位沒校準過** —— parser 是照文件猜的,作者自己在 `/admin/probe-indicators`
   的 docstring 寫明「TAIFEX 欄位需靠此端點回傳的真實資料校對」。
   **這正是第 7 節「寫 parser 前一定要看真實信件」的同一個坑**
3. **方向可能已經變了** —— PR 開於 2026-07-08,那時是加功能;
   2026-08-16 之後的方向是減資訊(推播只留三張)。往盤前報再塞五項數字要重新確認

**失敗模式要特別小心:** `market_stats.py` 全部寫成「抓不到回 `None` → 顯示 N/A」。
那擋得住服務掛掉,**擋不住欄位抓錯** —— 抓錯欄位時一樣是個數字、一樣印出來、
一樣不進 log 錯誤區。跟黃金 `× 10` 能撐 22 年沒被發現是同一種安靜。

**要撿回來的順序:** 先部署 `/admin/probe-indicators` 看真實 payload → 校準欄位
→ 補測試 → 才接進盤前報。

**PR #2 的另外四個 commit 已經合了**(2026-08-25,cherry-pick 成零衝突):
`806d31d` 黃金倍率 / `b693a47` 財務時區 / `2a25898` 提醒+配額時區 / `dae9142` 黃金現貨 API。

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
| 每日推播(群組,LINE) | 07:00 | `DAILY_CRON` |
| 每日個人報(email) | 07:00 | 同上,跟在群組版後面跑 |
| 財務同步 | 15:30 | `FINANCE_CRON`(預設 `30 7 * * *` UTC) |

個人版 2026-08-20 起改寄 Gmail(`mailer.py`),不再吃 LINE push 配額 ——
push 每月 200 則,群組版每天已佔一則,個人版再佔一則等於一半花在自己身上。
群組版維持 LINE 不動(家人不會去收信)。

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
├─ daily_report.py     每日報:群組版走 LINE push、個人版走 email
│   ├─ humor.py            今日一則;主題輪替(humor_topics.py)+ 歷史去重
│   └─ mailer.py           個人版寄信(Gmail SMTP + 應用程式密碼)
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
├─ 今日一則                            ← 講過的小知識/笑話/新鮮事,給去重用
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
| `SEND_TOKEN_PICKLE_B64` | 個人報寄信用的 OAuth token base64,**只有 `gmail.send`** | 無 —— **沒設就整個不寄** |
| `REPORT_EMAIL_TO` | 個人報收件者 | 沒設就寄給 `GMAIL_USER` 自己 |
| `TW_HOLDINGS` | 台股起始庫存**備援** `代號:股數@均價`,逗號分隔 | 無 —— 正常走 Notion「起始庫存」表,這個只在 Notion 讀不到時生效 |
| `TW_HOLDINGS_ASOF` | 上面那份備援庫存的基準日 `YYYY-MM-DD` | 無 —— **沒設則整份 TW_HOLDINGS 失效** |
| `PERSONAL_USER_ID` | 個人版每日信要放誰的待辦 / 提醒(LINE user id) | 無 —— 沒設就跳過這兩個區塊,其他照寄 |

> ⚠️ `TW_HOLDINGS_ASOF` 是必填不是選填。`build_portfolio` 會跳過基準日以前的成交
> (已含在庫存裡),基準日錯了就是少算或雙重計算,而且錯得無聲無息。
> 所以缺基準日時 `manual_snapshot()` 直接回 None,寧可沒有快照也不要算錯。
> 台股需要手動填的原因見 4.1:**富邦台股月對帳單整份沒有庫存表**。

`NOTION_TOKEN` / `NOTION_PARENT_PAGE_ID` / `TOKEN_PICKLE_B64` / `PDF_PASSWORD_PREFIX` /
`ADMIN_TOKEN` 皆已存在 Infisical,自動 sync 到 Railway。

> ⚠️ `SEND_TOKEN_PICKLE_B64` 是**第二顆獨立 token**,不是 `TOKEN_PICKLE_B64`。刻意分開:
> `token.pickle` 只有 `gmail.readonly`(`gmail_reader.SCOPES`),要寄信得加 scope、
> 重跑授權、換掉線上那顆 token —— 財務同步 / 發票 / Gmail 警示全靠它,
> 換壞了是連鎖故障。兩顆各管各的,爆炸半徑從三個功能縮到一個。
>
> 產生方式:真終端機跑 `python setup_send_token.py`(會開瀏覽器),
> 產出 `token_send_b64.txt` 貼進 Infisical。**這支不會動到 `token.pickle`。**
>
> 原本的 `GMAIL_APP_PASSWORD` 已無用(SMTP 埠被擋),可從 Infisical 刪除。

> 註:`TOKEN_PICKLE_B64` 優先於本機 `token.pickle`,所以本機 token 失效
> **不影響 Railway**。曾誤判過這點。

---

## 10. 使用者待辦(需要人工)

- [x] ~~部署後重跑 `POST /admin/setup-richmenu`~~ —— 2026-08-19 已跑,5 張選單全部重建
- [x] ~~申請 Google 應用程式密碼並存進 Infisical `GMAIL_APP_PASSWORD`~~ —— 2026-08-24 已設,
  `/admin/env-check` 確認 `len: 16`。**要重拿的話別再照舊路徑找**:
  App passwords 的入口**已經不在 2-Step Verification 頁面裡**(滑到底也沒有),
  帳號設定的搜尋框打 `app password` 也只會跑出 Password Manager 跟說明文章。
  唯一入口是直接貼網址 `https://myaccount.google.com/apppasswords` ——
  第一次會被踢回首頁要求重新驗證身分,**驗證完再貼一次**就進得去。
- [ ] 在 LINE 私訊實測「記一筆」→ 點品項 → 點金額
- [ ] **語句庫要人工填內容**(2026-09-04)。部署後 `ensure_all_dbs()` 會建出
  「📚 語言學習」區塊與「語句庫」「金句庫」兩張空表,建完要去 Notion 對那一頁
  按 **Share → Connect** 接上 integration,不然機器人讀不到自己建的表。
  接著把 Preply 老師整理的句子貼進「語句庫」——貼進去當天就會出現在信裡
  (`下次出現` 空的視為到期)。庫是空的時候會走 AI 補位,不會開天窗。
- [ ] **金句搬遷**(2026-09-04)。舊的「每日一句」370 句要匯進金句庫:
  ```
  infisical run --env=dev -- python import_quotes.py <舊DB的ID> --dry-run
  ```
  先看報告(它會把「沒拆出出處」和「句子裡還留著網址」的挑出來單獨列),
  確認沒問題再把 `--dry-run` 拿掉重跑。重跑安全 —— 去重比對的是拆完的句子。
  > Notion 的 integration token **綁 workspace**。跨帳號分享頁面只解決
  > 「人看得到」,解決不了「機器人讀得到」—— 所以資料得先搬進本帳號的 workspace。
- [ ] 申請財政部電子發票 AppID(見 4.5,需本人身分驗證,程式端做不到)
- [ ] 刪除 Notion 持倉裡 `代號=台積電` 的孤兒資料列(程式無權限刪除)
- [ ] LINE Official Account Manager 改帳號名稱為「全能大管家」(**無 API 可改**)
- [ ] 在 LINE 私訊實測煮飯與財務按鈕(這兩塊完全沒被真實使用過)
- [ ] 考慮把台北富邦設為薪轉戶(目前是郵局,郵局不寄任何電子通知,薪水只能手動記)
- [ ] **在 Notion 手動刪掉「交易明細」的「帳戶」欄位**(2026-08-25):
  程式端已移除 relation 定義,但 `_ensure_properties()` 只補不刪,
  線上那個永遠空的欄位不會自動消失。
  路徑:交易明細 → 欄位標頭「帳戶」→ 下拉 → Delete property
- [ ] **在 Notion「💰 財務中心 → 起始庫存」表填持倉**(2026-08-26):
  部署後 `ensure_all_dbs()` 會自動建這張表。欄位:代號 / 市場 / 股數 /
  平均成本 / 基準日 / 備註。**基準日必填**,沒填的列會被跳過。
  已知:`2330` 10 股 @2249.6、`AU9901`(臺銀金,1 台錢) 1 股 @17410。
  這張表**只讀不寫**,跟每天被覆寫的「持倉」輸出表刻意分開 ——
  混在一起會讓算錯的結果被寫回去、下次當成起點讀回來,錯誤固化成事實。
- [ ] ~~在 Infisical 設 `TW_HOLDINGS` / `TW_HOLDINGS_ASOF`~~ —— 改走 Notion,
  環境變數只留作備援。原本的說明:
  台股月對帳單沒有庫存表(見 4.1),起始庫存只能手動給。已知一筆
  `2330:10@2249.6`;另一筆使用者口述「台銀金 17410 一股」——
  **台股沒有股價 17,410 的個股**(最高的大立光在 2,000 上下),
  所以 17410 比較可能是總金額而不是每股價,**代號與股數待使用者確認後才填**。
  填錯會讓淨值差一個量級,寧可先只填台積電那筆。
- [ ] 部署後確認 Railway log 出現 `[holdings]` 那段來源說明,
  確認美股走「月對帳單庫存」、台股走「手動設定的持倉」
- [ ] 部署後確認 Railway log 出現 `[notion] ensure_all_dbs 完成:13/13`,
  並到 Notion 看「🍳 煮飯模板」底下是否長出食譜 / 本週菜單 / 採購清單
- [ ] 在 LINE 私訊實測「記一筆」→ 品項 → 金額 → 個人/共同
- [ ] 確認 Notion「交易明細」真的長出「分攤類型」「原始總額」兩欄
      (`_ensure_properties` 在第一次 `get_or_create_db("交易明細")` 時補)
- [ ] 記一筆共同消費後,到 Notion 上核對金額欄是分攤額、原始總額是整桌

---

## 11. 2026-08-25 Notion 架構健檢

首次比對「程式碼宣稱的架構」與「線上實際存在的架構」。方法是直接用
Notion API 查每個 DB 的真實筆數,不看程式碼推論。

### 線上實況

| DB | 區塊 | 筆數 | 備註 |
|---|---|---|---|
| Todos / Reminders | 根頁 | 0 / 0 | 功能正常,只是沒在用 |
| LineQuota | 根頁 | 4 | 正常 |
| 今日一則 | 根頁 | 18 | 正常 |
| 交易明細 | 財務中心 | 32 | 唯一持續在長的(08-07 ~ 08-22) |
| 帳戶 | 財務中心 | 0 | 從未寫入 |
| 持倉 | 財務中心 | 4 | 正常 |
| 淨值快照 | 財務中心 | 13 | 正常 |
| 信用卡帳單 | 財務中心 | 0 | 尚未啟用 |
| 食材庫存 | 煮飯模板 | 0 | 從未使用 |
| 食譜 / 本週菜單 / 採購清單 | 煮飯模板 | — | **線上根本不存在** |

### 修了什麼

**1. 消費類別 schema drift(最嚴重)**
`_SPEND_CATEGORIES` 定義 10 個類別,線上實際長出 14 個 ——
多了「線上付款 / 教育∕學費 / 一般購物 / 家具家飾裝潢」。
成因:parser 直接採用國泰原字串,而 **Notion 對未定義的 select 值不會報錯,
會自動擴充選項**。所以沒有任何錯誤訊號,只有報表在安靜漏桶。

修法:白名單補齊成 14 個(顏色對齊線上現況),新增
`normalize_spend_category()`,**擋在 `transaction_add()` 寫入端**而非
parser —— 寫入端是唯一 choke point,手動記帳與日後新 parser 一併受保護。
未知類別歸入「其他」並 print,不再自動長選項。

**2. 帳戶 relation 是空殼**
`交易明細.帳戶` relation 建了但 `transaction_add()` 從未寫入,32 筆全空;
`帳戶` DB 本身 0 筆。已從 `_RELATIONS` 移除。
卡片辨識用既有的「卡末四碼」文字欄即可。

**3. 煮飯模板死鏈(因果鏈值得記住)**
`食材庫存` 為空 → `daily_report.py` 的 `expiring_soon()` 回空即 `return None`
→ 永遠走不到 `recipes_load()` → **lazy create 從未被觸發** →
三個 DB 至今不存在,而且 log 全綠。

注意短路邏輯本身是對的(無庫存本就不該發提醒),病灶在 lazy create。
修法:新增 `ensure_all_dbs()`,`server.py` 的 lifespan 用背景 thread 跑一次
(不阻塞啟動,Notion 限流 3 req/s)。

**4. 次要**
`networth_load()` 未指定 sorts(順序未定義,畫趨勢圖會亂序)已補排序;
新增 `holdings_load()`(先前只有 `holdings_sync` 能寫,沒有函式能讀)。

### 教訓

- **lazy create + 上游提早 return = 隱形失效**。DB 永遠不會誕生,而且沒有訊號。
- **Notion 的 select 是開放集合**。寫入未定義值會擴充 schema 而非失敗,
  所以程式碼裡的常數會慢慢變成謊言。要擋就擋在寫入端。
- `_ensure_properties()` 只補不刪的設計是對的(保護手動加的欄位,
  例如「交易明細.月份」formula),但代價是 drift 只會單向累積。

---

## 12. 財務儀表板 `build_dashboard.py`

```bash
# 產生(需要 Notion 金鑰,金鑰在 Infisical)
infisical run -- python build_dashboard.py

# 順便留一份原始資料
infisical run -- python build_dashboard.py --dump-json dashboard_data.json

# 離線重畫,不碰 Notion
python build_dashboard.py --from-json dashboard_data.json
```

產出 `dashboard.html` 單檔:資料內嵌成 JSON、圖表手寫 SVG、**無任何外部
資源請求**,可離線開啟、可直接傳手機。

**`dashboard.html` 與 `dashboard_data.json` 已列入 `.gitignore`** ——
它們含真實消費紀錄與持倉,而這個 repo 是公開的。

拆成 `collect` / `compute` / `render` 三段:只有 `collect` 碰 Notion,
後兩段是純函式,所以在沒有金鑰的本機也測得到(`tests/test_build_dashboard.py`)。

頁面明確標註「淨值 = 股票市值」與「狀態全部停在授權中」,
避免對著不完整的數字做判斷。

不做成 Railway 路由的理由:省掉一整套存取控制,且不替財務資料新增對外入口。
