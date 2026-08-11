# ReportRobot 擴充:Notion 財務中心 + 煮飯模板

**日期:** 2026-08-10
**狀態:** 待審核
**Repo:** `github.com/kengkeng44/ReportRobot`(本機 `C:\Users\acer\Desktop\cheng.robot`)

---

## 1. 背景與目標

在既有的 ReportRobot 上擴充兩個 Notion 模板,不另建新系統:

1. **財務中心** — 從 Gmail 自動抓信用卡消費、帳單、證券成交、對帳單,寫進 Notion;追蹤股票資產浮動與淨值。
2. **煮飯模板** — 記錄買了哪些食材、何時過期、營養成分,並推薦「今天煮什麼」。

兩者共用同一個 repo、同一個 `notion_db.py`、同一組排程與 secrets。

### 為什麼擴充而非新建

盤點後,ReportRobot 已具備本專案所需的絕大多數基礎設施:

| 需求 | 既有資產 |
|---|---|
| Gmail OAuth2 拉信 | `gmail_reader.py::get_gmail_service()` / `_download_email_items()` |
| 加密 PDF 解析 | `extract_trades_from_pdf()`(pikepdf 解密 → pdfplumber 抽表),`PDF_PASSWORD_PREFIX` 已配置 |
| 台股/美股雙市場解析 | `_parse_tw_monthly_record()` / `_parse_us_record()`,含 pdfplumber 黏字修補 |
| 持倉彙總 | `portfolio.py`,`_aggregate_portfolio()` |
| 行情現價 | `yfinance` / `twstock` |
| Notion 讀寫 | `notion_db.py`,schema-driven + lazy 建 DB + graceful fallback |
| LINE 互動輸入 | `command_router.py::parse()/handle()`,已有 todo/reminder/portfolio 等 kind |
| 排程 | `server.py` AsyncIOScheduler + CronTrigger,`DAILY_CRON` env |
| Secrets | Infisical → Railway 自動 sync |

新寫的只有:**信用卡類 parser、營養資料源、Notion schema 擴充、雙 Gmail 帳號支援、去重層**。

---

## 2. 範圍

### 做

- Notion 9 個新資料庫(財務 5 + 煮飯 4)
- 4 個新 parser(國泰消費彙整 / 國泰電子帳單 / 國泰繳款入帳 / 富邦轉帳通知)
- 雙 Gmail 帳號支援
- 交易去重(指紋)
- 營養成分自動帶入
- 到期提醒與「今天煮什麼」推薦,併入既有每日推播

### 不做(明確排除)

- **薪水自動抓取** — Gmail 內無金額(康彼斯只寄登入連結),郵局不寄任何信。薪水**每月手動填一筆**。
- **電子發票串接** — 品項名稱過髒(超市常寫「蔬菜」而非「高麗菜」),列為後續擴充點。
- **爬公司薪資系統 / 網銀** — 帳密風險與改版脆弱性不成比例。
- **Notion 當計算引擎** — 所有彙總在 Python 端算好再寫入,Notion 只存結果與提供檢視。

---

## 3. 共用技術決策

### 3.1 Notion relation 需要兩階段建立

現有 `get_or_create_db()` 只做單次 `databases.create`,而本專案的 DB 之間有 relation(交易↔帳戶、食譜↔食材)。Notion API 建立 relation 時 `database_id` 必須**已存在**。

**做法:** 擴充 `notion_db.py`,把 schema 拆成兩段:

```python
_SCHEMAS = { "交易明細": { ...非 relation 欄位... } }
_RELATIONS = {
    "交易明細": {"帳戶": {"relation": {"database_id": "@帳戶", "type": "single_property"}}},
}
```

`get_or_create_db()` 先建立所有無 relation 的 DB,再走第二輪 `databases.update()` 把 `@DB名` 解析成實際 db_id 後補上 relation。維持既有的 fallback 語意(失敗回 None,不 raise)。

### 3.2 雙 Gmail 帳號

現況 `GMAIL_USER` 為單數、憑證存 `token.pickle`。

**改為:**
- `GMAIL_ACCOUNTS`:逗號分隔,`renhezheng44@gmail.com,jenho.cheng@gmail.com`
- 每帳號一組 refresh token,存 Infisical:`GMAIL_REFRESH_TOKEN_<slug>`
- `get_gmail_service(account)` 帶參數;既有無參數呼叫預設第一個帳號以維持相容

**⚠️ 既有風險:** Railway 檔案系統是暫時的,`token.pickle` 重啟即失效。現有程式碼靠 `_save_creds()` 寫本機檔。改成從 Infisical 讀 refresh token、記憶體內換 access token,不落地。

**不使用現有的 `轉寄-jenho` 轉寄規則** — 只有 78 封、不進收件匣、且規則生效前的信全部缺漏。

### 3.3 去重

每筆交易算指紋:

```
fingerprint = sha256(f"{帳戶}|{日期}|{金額}|{商店}|{來源類型}").hexdigest()[:32]
```

寫入前先用 `Fingerprint` 屬性 query Notion,存在就跳過。重跑排程不會產生重複資料。

### 3.4 排程

| 工作 | 時間(台灣) | crontab (UTC) |
|---|---|---|
| 既有每日推播 | 07:00 | 現有 `DAILY_CRON` 不動 |
| **財務同步** | **15:30** | `30 7 * * *` |
| **食材到期檢查** | 併入 07:00 那班 | — |

**為何是 15:30:** 實測信件送達時間 — 國泰「消費彙整通知」每天固定台灣時間 14:2x–14:5x(彙整前一日授權),富邦證券成交回報盤後約 14:25。15:30 時當日該到的信都到齊,台股也已收盤(13:30),可同班更新持倉現價與淨值快照。美股用前一交易日收盤。

**一天一次即足夠** — 這些信一天只來一封,每小時跑有 23 次是空轉。

### 3.5 成本

Gmail API、Notion API、yfinance/twstock 皆免費。這兩條管線**不呼叫 LLM**(信件格式固定,用 BeautifulSoup + regex 解析即可),因此不增加既有的 Anthropic 費用。Railway 增量約每月 15 分鐘 CPU。

---

## 4. 模組一:財務中心

### 4.1 Notion 資料庫(5 個)

#### 交易明細 `Transactions`
| 屬性 | 型別 | 說明 |
|---|---|---|
| 摘要 | title | `商店 或 說明` |
| 日期 | date | |
| 金額 | number (dollar) | 支出為正,方向欄區分 |
| 方向 | select | 支出 / 收入 / 轉帳 / 還款 |
| 類別 | select | 見下方 10 類 |
| 商店 | rich_text | |
| 帳戶 | **relation → 帳戶** | |
| 狀態 | select | 授權中 / 已結帳 |
| 來源 | select | 國泰消費彙整 / 國泰電子帳單 / 富邦轉帳 / PDF對帳單 / **手動** |
| 原信連結 | url | Gmail permalink,可追溯 |
| Fingerprint | rich_text | 去重鍵 |

**類別(固定 10 個)** — 直接沿用國泰帳單自帶分類,不自創:
餐飲、超市∕量販、百貨公司、服飾∕鞋∕精品、家電∕３Ｃ通訊、旅遊、電信服務、醫療、訂閱服務、其他

#### 帳戶 `Accounts`
名稱(title)、類型(select:信用卡/存款/證券)、銀行、幣別、末四碼、目前餘額(number)、歸屬Gmail(select)、餘額更新時間(date)

#### 信用卡帳單 `CardStatements`
期別(title,如 `2026-07`)、卡片(relation → 帳戶)、結帳日、繳款截止日、應繳總額、最低應繳、實際繳款、狀態(select:未繳/已繳/自動扣繳)

#### 持倉 `Holdings`
代號(title)、名稱、市場(select:TW/US)、股數、平均成本、現價、市值、未實現損益、報酬率、更新時間

> 資料來源直接接既有 `portfolio.py::_aggregate_portfolio()` 的輸出,不重寫解析邏輯。

#### 淨值快照 `NetWorthSnapshots`
日期(title,`YYYY-MM-DD`)、現金、股票市值、信用卡未繳、淨值。每日一筆。

### 4.2 新增 parser

新檔 `parsers/` 套件,每個發信人一個模組,統一介面:

```python
def parse(msg: dict) -> list[Transaction]   # msg 來自 gmail_reader
```

| 模組 | 發信人 | 產出 |
|---|---|---|
| `cathay_daily.py` | `service@pxbillrc01.cathaybk.com.tw`(消費彙整通知) | 逐筆交易,狀態=授權中 |
| `cathay_statement.py` | 同上(信用卡電子帳單) | 1 筆帳單 + 補正當期交易為已結帳 |
| `cathay_payment.py` | 同上(繳款入帳彙整通知) | 1 筆還款交易 |
| `fubon_transfer.py` | `mbank@dfm.taipeifubon.com.tw`(轉帳成功通知) | 1 筆轉帳 + **更新帳戶即時餘額** |

PDF 類(台新 / 台北富邦 / New New Bank / 富邦證券月對帳單 / 複委託)**沿用既有 `extract_trades_from_pdf()`**,只需新增對應的 subject 判斷與欄位對應。

### 4.3 授權 vs 入帳的兩階段對帳

國泰「消費彙整通知」是**授權當下**的紀錄,月帳單才是**最終入帳金額**(可能因外幣結匯、退款而不同)。

**流程:**
1. 每日抓到授權筆 → 寫入,`狀態=授權中`
2. 月帳單到達 → 逐筆比對(日期 ±3 天 + 商店 + 金額 ±5%)
3. 命中 → 更新金額為帳單值,`狀態=已結帳`
4. 未命中的帳單筆 → 新增一筆 `狀態=已結帳`
5. 未命中的授權筆(超過 45 天) → 標記待人工確認,不自動刪除

這樣當天就看得到花費,月底又不會失真。

---

## 5. 模組二:煮飯模板

### 5.1 Notion 資料庫(4 個)

#### 食材庫存 `Pantry`
| 屬性 | 型別 | 說明 |
|---|---|---|
| 名稱 | title | |
| 數量 | number | |
| 單位 | select | 顆/片/克/包/盒/瓶 |
| 購買日 | date | |
| 到期日 | date | |
| 剩餘天數 | formula | `dateBetween(prop("到期日"), now(), "days")` |
| 存放位置 | select | 冷藏/冷凍/常溫/調味櫃 |
| 分類 | select | 蔬菜/肉類/海鮮/蛋奶/主食/調味料/罐頭乾貨 |
| 熱量 | number | 每 100g,自動帶入 |
| 蛋白質 / 碳水 / 脂肪 | number | 每 100g,自動帶入 |
| 狀態 | select | 在庫/用完/丟棄 |

#### 食譜 `Recipes`
名稱(title)、所需食材(relation → 食材庫存)、步驟(rich_text)、烹調時間、難度(select)、份數、每份熱量、圖片(files)、來源(url)、標籤(multi_select)

#### 本週菜單 `MealPlan`
日期(title)、餐別(select:早/午/晚)、食譜(relation → 食譜)、已完成(checkbox)

#### 採購清單 `ShoppingList`
品名(title)、數量、分類、已購買(checkbox)、來源(select:手動/低庫存自動/食譜缺料)

### 5.2 輸入路徑:LINE

擴充 `command_router.py`,新增 kind:

| 指令 | 行為 |
|---|---|
| `買了 高麗菜1顆 番茄5顆 雞胸肉2片` | 解析寫入食材庫存,自動查營養、依分類套用預設保存天數推算到期日 |
| `庫存` | 回目前在庫食材,依剩餘天數排序 |
| `用掉 高麗菜` | 數量歸零、狀態改用完 |
| `煮什麼` | 依快過期食材推薦食譜 |

解析採**保守策略**:看不懂的詞不猜,回問使用者。到期日推算用「分類 → 預設天數」對照表(葉菜 3 天、根莖 14 天、肉類冷藏 2 天/冷凍 90 天…),使用者可在 Notion 手動改。

### 5.3 營養資料源

**主要:** 衛福部食藥署「食品營養成分資料庫」開放資料(CSV,離線比對,涵蓋台灣常見食材)
**備援:** USDA FoodData Central API(進口食品)
**查不到:** 留白,不猜測數值。

比對用中文品名模糊匹配,命中率不足時記錄未命中清單供人工補對照表。

### 5.4 到期提醒與推薦

併入既有每日 07:00 推播,新增一段:

```
🥬 食材提醒
⚠️ 2 天內過期:菠菜、板豆腐
💡 建議今天煮:菠菜豆腐味噌湯(用掉 2 樣快過期食材)
```

推薦邏輯:以「即將過期食材的覆蓋數」為主要排序,烹調時間為次要。不使用 LLM。

---

## 6. 錯誤處理

沿用 `notion_db.py` 既有語意 —— **所有 Notion 失敗都 fallback,print warn 但不 raise**,避免拖垮主流程(每日推播不能因為記帳寫入失敗而整個掛掉)。

| 情境 | 處理 |
|---|---|
| Notion 未設定 / API 失敗 | 跳過寫入,記 log,主流程續行 |
| PDF 密碼錯誤 | 記錄該封信 id,不重試,推播提醒 |
| 信件格式改版(regex 失配) | 記錄原文片段到 log,**不寫入半殘資料** |
| 營養查不到 | 欄位留空 |
| 某個 Gmail 帳號授權失效 | 只跳過該帳號,另一個照常 |

---

## 7. 測試

沿用既有 `tests/` 與 pytest。

- 每個 parser 用**真實信件的去識別化樣本**做 fixture,驗證欄位對應
- 去重:同一封信解析兩次,只產生一筆
- 授權→入帳對帳:給定授權筆與帳單筆,驗證命中與金額補正
- Notion 層用 mock client,不打真實 API
- 營養比對:給定品名清單,驗證命中率與未命中處理

---

## 8. 實作順序

1. **`notion_db.py` relation 兩階段建立** — 兩個模板都依賴,先做
2. **煮飯模板** — 相對單純(不需 PDF、不需第二個 Gmail),用它把擴充模式跑通
3. **雙 Gmail 帳號支援**
4. **財務中心 parser(純文字/HTML 四個)**
5. **財務中心 PDF 類**
6. **授權→入帳對帳邏輯**
7. **持倉 / 淨值快照寫入**(接既有 `portfolio.py`)

---

## 9. 新增環境變數(Infisical)

```
NOTION_TOKEN                  # 既有
NOTION_PARENT_PAGE_ID         # 既有
GMAIL_ACCOUNTS                # 新:逗號分隔兩個帳號
GMAIL_REFRESH_TOKEN_RENHE     # 新
GMAIL_REFRESH_TOKEN_JENHO     # 新
PDF_PASSWORD_PREFIX           # 既有(身分證字號)
FINANCE_CRON                  # 新:預設 "30 7 * * *"(台灣 15:30)
USDA_API_KEY                  # 新:選用,營養備援
```

---

## 10. 未決事項

1. **食藥署營養資料庫的實際涵蓋率** — 需先抓下來實測常見食材命中率,若過低則改以 USDA 為主。
2. **`token.pickle` 在 Railway 的持久化** — 既有風險,本次一併改為 Infisical refresh token。需確認既有 OAuth client 設定是否支援。
3. **持倉起始值** — 富邦成交回報只有「成交」,無起始庫存。需從最近一期月對帳單做一次性初始化。
4. **ReportRobot 既有的 `finance_overview` 指令** — 與新的財務中心可能重疊,待確認是否合併。
