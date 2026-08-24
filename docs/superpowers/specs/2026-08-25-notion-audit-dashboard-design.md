# Notion 架構健檢、修復與財務 Dashboard

日期：2026-08-25

## 背景

ReportRobot 的 Notion 層由 `notion_db.py` 以 declarative schema（`_SCHEMAS`）
兩階段建立。上線數月後首次做全面健檢，比對「程式碼宣稱的架構」與
「線上實際存在的架構」。

## 健檢結果（2026-08-25 實測）

根頁 `ReportRobot` (3597854a-2f6c-805e-9236-c3bf9520192b)

| DB | 區塊 | 線上筆數 | 狀態 |
|---|---|---|---|
| Todos | 根頁 | 0 | 空 |
| Reminders | 根頁 | 0 | 空 |
| LineQuota | 根頁 | 4 | 正常 |
| 今日一則 | 根頁 | 18 | 正常 |
| 交易明細 | 財務中心 | 32 | 正常（08-07 ~ 08-22） |
| 帳戶 | 財務中心 | 0 | 空，且被 relation 依賴 |
| 持倉 | 財務中心 | 4 | 正常 |
| 淨值快照 | 財務中心 | 13 | 正常 |
| 信用卡帳單 | 財務中心 | 0 | 空 |
| 食材庫存 | 煮飯模板 | 0 | 空 |
| 食譜 / 本週菜單 / 採購清單 | 煮飯模板 | — | **線上不存在** |

### 缺陷

1. **類別 schema drift**：`_SPEND_CATEGORIES` 定義 10 個類別，線上實際有 14 個。
   多出「線上付款 / 教育∕學費 / 一般購物 / 家具家飾裝潢」。
   成因：`parsers/cathay_daily.py` 直接採用國泰原字串，Notion 對未定義的
   select 值會自動擴充選項而不報錯。後果是任何按類別分組的報表都會漏桶，
   且不會有任何錯誤訊號。

2. **帳戶 relation 為空殼**：`交易明細.帳戶` relation 已建立，但
   `transaction_add()` 從未寫入該欄，32 筆全空；`帳戶` DB 本身 0 筆。

3. **煮飯模板死鏈**：`食材庫存` 為空 → `daily_report.py:68` 的
   `expiring_soon()` 回空即 `return None` → 永遠不會執行到 `recipes_load()`
   → lazy create 未被觸發 → 三個 DB 至今未建立。
   注意：短路邏輯本身正確（無庫存本就不該發提醒），病灶在 lazy create。

4. **次要**：`networth_load()` 未指定 sorts，回傳順序未定義；
   無 `holdings_load()` 讀取函式。

## 決策

| 項目 | 決定 |
|---|---|
| 類別 drift | 修：建立白名單 + 正規化 |
| 帳戶 relation | 移除 relation 定義（不補資料） |
| 煮飯模板 | 修好（使用者確認會用） |
| Dashboard 類型 | 財務數據儀表板 |
| Dashboard 資料來源 | Python 腳本產靜態 HTML |

## 設計

### A. 類別白名單

- `_SPEND_CATEGORIES` 補齊為 14 個，與線上現況一致。
- 新增 `normalize_spend_category(raw)`，行為對齊既有的
  `normalize_todo_category`：白名單命中則採用，否則回「其他」並 print 警告。
- **正規化位置：`transaction_add()` 寫入端，而非 parser 內。**
  理由：寫入端是唯一的 choke point，手動記帳與未來新增的 parser
  一併受保護；parser 維持純粹，仍輸出原始字串，便於除錯。

取捨：國泰日後新增類別會落入「其他」而非自動長出選項。此為刻意選擇 ——
可見的「其他」增加優於無聲的 schema 膨脹。

### B. 移除帳戶 relation

- `_RELATIONS` 刪除 `"交易明細": {"帳戶": "帳戶"}`。
- `_ensure_properties()` 只補不刪，線上既有的空欄位需手動移除，
  步驟記於交付說明。
- `信用卡帳單.卡片 → 帳戶` 暫不變動（該 DB 尚未啟用）。

### C. 煮飯模板修復

- 新增 `ensure_all_dbs()`：走訪 `_SCHEMAS` 全部呼叫 `get_or_create_db`。
- `server.py` 的 `lifespan` 以背景 thread 執行，避免阻塞啟動
  （Notion 限流 3 req/s，13 個 DB 需數十次請求）。
- 失敗僅 print，不使 startup crash，風格對齊既有的
  `restore_reminders_from_notion`。

### D. 次要修復

- `networth_load()` 加上依日期排序。
- 新增 `holdings_load()`。

### E. `build_dashboard.py`

- 執行方式：`python build_dashboard.py`，需 `NOTION_TOKEN` 與
  `NOTION_PARENT_PAGE_ID` 環境變數。
- 資料來源：`notion_db` 既有讀取函式。
- 產出：`dashboard.html` 單檔，資料內嵌為 JSON，圖表以手寫 SVG 繪製，
  無外部資源請求，可離線開啟。
- 不走 Railway 路由：免處理存取權限，且不為財務資料新增對外入口。

版面：

1. KPI 列 + 資料新鮮度徽章（最後一筆交易距今天數）
2. 類別佔比 —— 橫向長條（14 個類別不適合圓餅）
3. 每日支出 —— 直條
4. 淨值走勢 —— 折線
5. 持倉表
6. 最近交易表

頁面明確標註「淨值 = 股票市值，現金與卡費資料源尚未接上」。

## 不在範圍內

- 補建帳戶資料
- 國泰電子帳單 parser（交易狀態由「授權中」轉「已結帳」）
- 信用卡帳單 DB 的啟用
- Todos / Reminders 為空的處理（功能正常，只是未使用）
