# 山屋空位存 Notion＋儀表板（2026-11-03）

接續 [2026-11-02-hut-monitor-design.md](2026-11-02-hut-monitor-design.md)。

## 目標

1. 每次檢查的空位寫進 Notion，可以回頭分析、重新部署不再清空。
2. 通知只寄住宿日是週五、六、日的空位增加；平日變化只記錄。
3. 一個 artifact 儀表板：讀 Notion 的最新空位＋更新時間，下面接交通手冊。

不做：新增抓取站點、改檢查頻率（維持 07:00／16:30／23:30）、國定假日判斷。

## Notion 結構

`notion_db._SECTIONS` 加一區「山屋」（icon ⛰️），掛在 ReportRobot 根頁底下，跟財務中心、語言學習同一層。兩張表：

### 山屋空位（最新狀態，upsert）

每個「地點×單位×住宿日」一列，另外每個站一列「站況」。同時有效的約 300 列；過期的列留著不動，一年約多 3,600 列。

| 欄位 | 類型 | 說明 |
|---|---|---|
| 鍵 | title | `site|unit|YYYY-MM-DD`；站況列是 `site|站況` |
| 地點 | select | 嘉明湖、天池、檜谷、排雲、九九、七卡、奇萊、南湖 |
| 單位 | rich_text | Slot.unit |
| 類型 | select | 床位／營地／站況 |
| 住宿日 | date | 站況列空白 |
| 星期 | select | 一～日 |
| 週末 | checkbox | 住宿日是週五、六、日 |
| 剩餘 | number | None 就空白 |
| 報名 | number | Slot.applicants |
| 更新時間 | date（含時間） | 這列最後一次被寫入的時間 |
| 備註 | rich_text | 站況列放錯誤訊息；成功放「正常」 |

- 只在剩餘或報名數變了才更新那列，沒變不寫（省寫入次數）。
- 站況列每次檢查都更新：它的「更新時間」就是儀表板頁首的「資料更新時間」，成功或失敗都看得到。
- 住宿日已過的列不封存、不刪除，留著給之後分析。

### 山屋變化紀錄（只增不改）

剩餘數變了才新增一列（增加、減少都記）。

| 欄位 | 類型 |
|---|---|
| 摘要 | title，例「九九 11/21(五) 0→2」 |
| 地點 | select |
| 單位 | rich_text |
| 住宿日 | date |
| 週末 | checkbox |
| 之前 | number |
| 之後 | number |
| 變化時間 | date（含時間） |

第一次建立基準時不寫變化紀錄（不然一次塞 300 列「空白→N」）。

### 寫入量

- 第一次：約 300 列新增，每秒 3 筆約 2 分鐘。
- 之後每次：站況 8 列＋有變的列（通常個位數）＋變化紀錄同數量。
- 寫 Notion 放在 `run_check` 寄信之後，用背景執行緒跑，不拖慢檢查。

## 程式改動

### 新檔 `hut_notion.py`

只負責跟 Notion 對話，不碰比對邏輯：

- `load_rows(today)` → `{鍵: (page_id, 剩餘, 報名)}`：只讀住宿日 ≥ 今天的列加站況列（Notion 查詢加 filter，約 3 頁 × 100 列），過期列再多也不會變慢。
- `sync(slots, errors, now, baseline_sites)`：
  1. `load_rows()` 拿現況。
  2. 新鍵 → create；剩餘或報名變了 → update＋（不是基準站時）在變化紀錄新增一列。
  3. 每站更新站況列。
  4. 回傳 `{"created", "updated", "changes"}`。
- Notion 沒設定（`notion_db.is_configured()` 是 False）就直接回傳，什麼都不做。
- 用 `notion_db.get_or_create_db("山屋空位")`、`get_or_create_db("山屋變化紀錄")` 建表，schema 加進 `notion_db._SCHEMAS`。

### `hut_monitor.py`

- 新增 `WEEKEND = {4, 5, 6}`（週五、六、日的 `weekday()`）。
- `_diff` 不變（算出所有增加）；`run_check` 寄信前過濾 `c.slot.day.weekday() in WEEKEND`。平日的增加不寄，但快照照樣更新（下次不會重報）。
- `run_check` 結尾在背景執行緒呼叫 `hut_notion.sync(...)`；失敗連續 3 次才 `notify`，跟抓取失敗同一套計數。
- 開機基準：`_snapshot` 是空的時候先 `hut_notion.load_rows()` 補回快照並把有資料的站放進 `_baselined`。重新部署後就能直接比對，不用再白跑一次基準。讀 Notion 失敗就照舊（第一次只記不報）。

### `hut_report.py`

信件主旨與內文不變，只是收到的 changes 已經過濾成週末。07:00 抽籤／開放日提醒不受週末過濾影響。

## 儀表板 artifact

- 發布成新 artifact「山屋儀表板」，舊的「山屋交通手冊」保留。
- 宣告 `capabilities: {mcp: {servers: [{server: "claude_ai_Notion", tools: [...]}]}}`，用檢視者自己的 Notion connector 讀資料。
  - 讀 view，不用 SQL（SQL 額度跟其他儀表板共用會用完）。
  - 要用哪個工具、參數名稱，寫計畫時先讀 connector 的工具 schema 再定，不猜。
  - 讀不到（沒授權、connector 沒連）→ 顯示「讀不到 Notion」和原因，交通手冊照常顯示。
- 頁首：資料更新時間（站況列最新的更新時間）、各站正常／失敗。超過 12 小時沒更新標成過期。
- 每個地點一張卡：
  - 上半：未來 8 週的週五、六、日格子，數字是剩餘，有空位的格子上色；抽籤站標「抽籤前」。
  - 下半：交通手冊（沿用現有 artifact 的內容與來源標籤）。
- 外觀：安裝 Anthropic 官方 `frontend-design` skill（從 GitHub anthropics/skills 下載，讀過全文再放進 `~/.claude/skills/`），照它的原則重做；同時遵守 memory 的「介面不要有 AI 感」清單。

## 錯誤處理

| 狀況 | 行為 |
|---|---|
| Notion 沒設定 | 跳過寫入，檢查與寄信照舊 |
| Notion 寫入失敗 | 印 log；連續 3 次推 LINE 一次 |
| 寫到一半失敗 | 下次 `load_rows()` 重新比對，會補寫（upsert 可重跑） |
| 開機讀 Notion 失敗 | 照舊第一次只建基準 |
| 儀表板讀不到 | 顯示原因，交通區塊照常 |

## 測試

- `hut_notion`：用假的 Notion client 測新增、更新、沒變不寫、基準不寫變化紀錄、站況列、`load_rows` 有帶住宿日 filter。
- `hut_monitor`：週末過濾（週四的增加不寄、週五的寄）；開機從 Notion 補快照後第一次就能報變化；Notion 失敗不影響寄信。
- 既有山屋測試全部要過。
- 儀表板：發布後用 Chrome 開，用 `get_page_text` 確認資料與更新時間有出來；外觀只截一張圖確認。
