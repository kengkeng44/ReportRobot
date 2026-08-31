# 每日個人報 v2:語句庫 + 金句庫 + 消費圓餅圖 — 設計

**日期:** 2026-09-01
**狀態:** 設計已確認,尚未實作
**前置:** `docs/superpowers/specs/2026-08-10-notion-finance-kitchen-design.md`(Notion schema 機制)

---

## 1. 問題

每日個人信(`daily_report.py:_email_personal_report`)目前六個區塊:
待辦 / 提醒 / 本月消費明細 / 最新消費 / 冰箱 / 天氣。兩個問題:

**其一,沒有學習內容。** 使用者在 Preply 上英文與西班牙文各有課,老師整理的
句子散在 Preply Classroom 裡。Preply **沒有公開 API**(2026-09-01 查證:
只有第三方爬蟲能抓老師檔案,學生的 Vocabulary 區官方明說僅老師可編輯),
所以自動同步這條路是死的。

**其二,「本月消費明細」把整個月逐筆列出來,長到沒人看。** 使用者要的是
「一個月花在哪」的**分布**,不是流水帳;流水帳只需要最近幾天。

---

## 2. 決定與理由

### 2.1 檔案不用傳,直接開 Notion 資料庫當入口

原始構想是使用者把老師整理的檔案傳給 Claude,再由程式讀。改成
**在 Notion 開一張表,使用者直接貼**。

理由是 `notion_db.py` 已經有 `_SCHEMAS` + `_SECTIONS` + `ensure_all_dbs()`
這套 schema-as-code:新增一筆 schema 定義,線上資料庫會自己長出來,欄位、
下拉選項、區塊子頁全自動。走檔案反而要多一個「檔案放哪、誰負責解析、
格式變了怎麼辦」的問題。

已知限制:`ensure_all_dbs()` 的補洞邏輯(`notion_db.py:466`)**只增不減**,
欄位改名等於棄用舊欄位。所以 schema 一次想好。

### 2.2 兩張表,不是一張

| | 語句庫 | 金句庫 |
|---|---|---|
| 內容 | 英文 / 西班牙文實用句 | 中文金句 |
| 來源 | Preply 老師整理 + AI 補位 | 從 renhezheng44 帳號搬過來 |
| 出現規則 | **間隔重複** | **隨機不重複** |
| 目的 | 記起來 | 被提醒 |

合成一張表要多一個「模式」欄位去分流,而那個欄位的值永遠等於語言欄位 ——
兩張表反而少一個可以填錯的地方。搬遷也單純:金句庫直接對應原本那張表。

### 2.3 中文不排複習曲線

英文西班牙文是**要背的**,隔一個月再看有回升價值。中文金句是**要被啟發的**,
同一句名言隔一個月再看不會產生同樣的回升。所以金句庫沿用 `humor.py` 那套
「撈過的記著、不重複」,不排間隔。

### 2.4 間隔重複用固定表,不吃回饋

```python
INTERVALS = (1, 7, 30, 90, 180)   # 天
下次出現 = 今天 + INTERVALS[min(出現次數, len(INTERVALS) - 1)]
```

第 5 次之後固定 180 天一輪。使用者原話是「隔一個月、三個月再重傳」,
對應第 3、第 4 級。

已排除:在信裡放「記得了 / 忘了」連結打 `server.py` 新端點,依回饋伸縮間隔
(真正的 SM-2)。那才是完整的遺忘曲線,但要多一個端點、一組 token、
一份狀態機,而使用者明確選了零互動。**Notion schema 也不預留回饋欄位** ——
真要加的時候補一欄就是,預留一個永遠是空的欄位只會讓表看起來壞掉。

### 2.5 圓餅圖只畫前 6 大

`交易明細` 的 `類別` 欄位有 14 種(`notion_db.py:99` `_SPEND_CATEGORIES`)。
14 片的圓餅圖是色票不是圖表。取金額前 6 大,其餘合併成一片「其他」。

只算 **TWD 支出**。外幣不換算併入 —— `format_monthly_detail` 已經有同樣的
判斷與註解(把 US$15 加進台幣會得到一個沒有意義、而且看不出哪裡怪的數字)。

### 2.6 「近三天」是有資料的最近三天,不是日曆三天

國泰消費彙整信每天彙整前一日、**當天下午才寄到**,早上寄信時昨天的資料
還沒進 Notion。`format_latest_day_spending`(`finance_report.py:597`)的
註解已經記錄過這個坑。用日曆算,月初與同步中斷時使用者會看到一片空白。

所以取 **`交易明細` 裡最近的三個有支出的日期**。

---

## 3. Notion schema 新增

`_SECTIONS` 新增一個區塊:

```python
"語言學習": {
    "icon": "📚",
    "dbs": ("語句庫", "金句庫"),
},
```

`_SCHEMAS` 新增兩張表:

```python
"語句庫": {
    "句子": {"title": {}},
    "語言": _select(("英文", "blue"), ("西班牙文", "orange")),
    "中文意思": {"rich_text": {}},
    "情境備註": {"rich_text": {}},      # 老師的補充、用法陷阱
    "來源": _select(("Preply課堂", "green"), ("自己整理", "gray"),
                    ("AI生成", "purple")),
    "加入日期": {"date": {}},
    "出現次數": {"number": {"format": "number"}},
    "上次出現": {"date": {}},
    "下次出現": {"date": {}},           # 間隔重複的排程欄位
},

"金句庫": {
    "金句": {"title": {}},
    "出處": {"rich_text": {}},
    "加入日期": {"date": {}},
    "上次出現": {"date": {}},           # 有值代表講過了
},
```

新句子由使用者手動貼進 Notion 時,`出現次數` / `下次出現` 會是空的。
`phrasebook` 把「`下次出現` 為空」視同「今天就該出現」—— 使用者貼完不必再
去填任何欄位,否則這張表就變成一件家事。

---

## 4. 模組設計

### 4.1 `phrasebook.py`(新,純邏輯)

不碰 Notion、不碰 AI,只做決策 —— 這樣測試不需要任何 mock 網路。

```python
INTERVALS = (1, 7, 30, 90, 180)

def next_due(appeared_count, today)      -> date
def pick_due(rows, today)                -> row | None
def advance(row, today)                  -> dict   # 要寫回 Notion 的欄位
def format_daily(en, es, quote)          -> str | None
```

`pick_due` 的排序:`下次出現` 最舊的優先(逾期最久的先還債);
`下次出現` 為空的視為最優先(新貼的句子當天就上場)。

### 4.2 `notion_db.py` 新增讀寫

```python
def phrases_due(language, today, limit=20)   # 撈到期的,已排序
def phrase_advance(page_id, fields)          # 寫回出現次數/上次出現/下次出現
def phrase_add(sentence, language, meaning, note, source, today)
def quotes_unseen(limit=20)                  # 撈沒講過的金句
def quote_mark_seen(page_id, today)
```

`quotes_unseen` 全部講過時的處理:**把 `上次出現` 最舊的當候選**,
而不是回空。金句庫用完不該讓區塊消失,輪回去重講是可接受的。

### 4.3 AI 補位

語句庫撈不到到期的(庫是空的、或都還沒到期)→ 用 `anthropic` 現生一句,
沿用 `humor.py` 的 `_ai()` 模式與 `usage_tracker`。

**生完寫回語句庫**(來源標 `AI生成`,`下次出現` = 明天)。這樣 AI 生的句子
會跟著進入複習循環,庫在使用者不貼檔的日子也會長大,而不是生完就丟。

防重複不另建歷史表 —— 語句庫本身就是歷史,把最近 N 句塞進 prompt 當
avoid 清單即可。這比 `humor.py` 少一層,因為 `humor.py` 的內容是拋棄式的,
這裡的內容本來就要留著。

### 4.4 `spending_chart.py`(新)

```python
def build_pie(txns, month) -> (chart_path, summary_text) | (None, None)
```

- matplotlib `Agg` backend,中文字型沿用 `weather.py` 那套 `font_manager` 載入
  (那個坑已經踩平,不要再寫第二份)
- 依 `類別` 彙總 TWD 支出 → 前 6 大 + 「其他」
- 每片標 `類別 NT$金額 (xx%)`
- PNG 存 `tempfile.gettempdir()`,跟 `weather.generate_temp_chart` 同一套
- `summary_text` 是 `本月合計 NT$12,345(共 87 筆)` —— **純文字版信件沒有圖**,
  合計數字必須也活在文字裡

當月沒有任何 TWD 支出 → 回 `(None, None)`,呼叫端整個區塊不放。

### 4.5 `finance_report.py` 新增

```python
def format_recent_days(txns, today, days=3)   # 有資料的最近三個日期
```

`format_recent_days` 同時取代每日信裡的「本月消費明細」與「最新消費」兩塊 ——
三天的明細已經包含最近一天,再放一次是重複。

`format_monthly_detail` 與 `format_latest_day_spending` **兩個都保留不動** ——
LINE 的「/財務」與「最新消費」指令查詢還在用它們。只有每日信改成不呼叫。
刪掉它們會弄壞指令查詢,而那個壞法在每日信上看不出來。

### 4.6 `mailer.py` 支援內嵌圖片

現在 `_build_message()` 只組 plain + html 兩層,塞不進圖。改成:

```
multipart/related
├─ multipart/alternative
│  ├─ text/plain          ← 沒有圖,所以 summary_text 必須在這裡
│  └─ text/html           ← <img src="cid:spending">
└─ image/png  Content-ID: <spending>
```

介面改為 `send_email(subject, body, html=None, images=None)`,
`images` 是 `{cid: 檔案路徑}`。**不給 `images` 時行為與現在完全一致** ——
既有 8 個測試不該因為這次改動而修改。

### 4.7 `digest.py` 支援圖片卡片

`build_digest_html(date_str, blocks)` 的 `blocks` 元素從 `(標題, 內容)`
擴充成也接受 `(標題, 內容, cid)`。三元組時在卡片內文上方插
`<img src="cid:..." style="max-width:100%;border-radius:8px">`。

兩元組繼續運作 —— 向後相容是刻意的,既有測試是這個 repo 的資產。

### 4.8 信件標題帶金額

```
📮 每日個人報 2026-09-01 · 本月 NT$12,345
```

手機通知列直接看到數字,不用點開。拿不到金額時退回原本的標題。

### 4.9 寄信重試

`send_email` 失敗重試 2 次(間隔 2 秒)。現在一次失敗整封就沒了,而且
使用者不會知道。三次都失敗才丟例外進 `notify_admin`。

---

## 5. 信件區塊與順序

```
🗣️ 今日三句       ← 新增,放第 2 位
📋 今日待辦
⏰ 進行中提醒
📊 本月消費分布    ← 新增(圓餅圖)
🧾 近三天消費      ← 取代原本的「本月消費明細」+「最新消費」
🍳 冰箱快過期・煮什麼
🌤️ 天氣
```

`🗣️ 今日三句` 放第 2 位而不是最後:學習內容放在信尾容易被滑過去。
待辦仍然排最前 —— 那是當天要做的事。

區塊內容:

```
🗣️ 今日三句

[EN] Let's play it by ear.
     再看情況決定吧
     💡 字面是「靠耳朵演奏」,口語很常用

[ES] Me da igual.
     我都可以／隨便
     💡 比 no me importa 更輕鬆

[中] 你以為的極限,只是別人的起點。
     —— 出處
```

`_build_personal_sections` 既有的「空區塊不放」規則不變。

---

## 6. 測試

這個 repo 連 86 行的 `mailer.py` 都配 8 個測試,新東西照規矩。

| 檔案 | 涵蓋 |
|---|---|
| `tests/test_phrasebook.py` | `next_due` 五級間隔與封頂、`pick_due` 排序與空欄位優先、`advance` 回傳欄位、`format_daily` 缺語言時的降級 |
| `tests/test_spending_chart.py` | 類別彙總、前 6 + 其他的併法、外幣被排除、無資料回 `(None, None)`。**不驗 PNG 像素** |
| `tests/test_finance_report.py` | `format_recent_days` 取「有資料的三天」而非日曆三天 |
| `tests/test_mailer.py`(擴充) | 給 `images` 時的 multipart/related 結構;不給時結構與現況一致 |
| `tests/test_digest.py` | 三元組 blocks 產生 `<img src="cid:...">`;兩元組行為不變 |

---

## 7. 分四段,每段可獨立上線

| 段 | 內容 | 使用者看得到的 |
|---|---|---|
| 1 | Notion schema + `phrasebook.py` + 讀寫 + 測試 | Notion 長出空的語句庫/金句庫,可以開始貼 |
| 2 | 信件加 `🗣️ 今日三句` + AI 補位 | 隔天早上信裡有三句 |
| 3 | `spending_chart.py` + `mailer` 內嵌圖 + `digest` 圖片卡片 | 信裡有圓餅圖 |
| 4 | 近三天取代整月明細 + 標題帶金額 + 寄信重試 | 信變短、通知列有數字 |

---

## 8. 使用者要手動做的事

1. **把 renhezheng44 帳號的金句資料庫搬到 jenho.cheng 的 workspace。**
   Notion integration token 是綁 workspace 的,跨帳號分享頁面只解決「人看得到」,
   解決不了「機器人讀得到」。用 Notion 的匯出 CSV → 匯入,或「Duplicate to」。
   搬完貼進第 1 段建好的「金句庫」。
2. **把 Preply 老師整理的句子貼進「語句庫」。** 有空再貼,庫空的日子由 AI 補位。

兩件事都不擋開發 —— 第 1 段建完表就能開始貼,程式那邊表空也不會壞。

---

## 9. 已排除的方案

| 方案 | 為什麼不做 |
|---|---|
| 接 Preply API | 沒有公開 API。第三方爬蟲只能抓老師檔案,抓不到學生的單字本 |
| 傳檔給 Claude 再解析 | 多一層「檔案放哪、格式變了怎麼辦」。Notion 直接當入口更短 |
| 第二顆 Notion token 讀 renhezheng44 | 多一顆 secret + `notion_db` 改多 client。金句庫是靜態內容,一次搬完更划算 |
| 信裡放「記得/忘了」回饋連結 | 真 SM-2 要多一個端點與狀態機。使用者選了零互動 |
| 語句庫與金句庫合成一張表 | 要多一個「模式」欄位,而它的值永遠等於語言欄位 |
| 圓餅圖畫全部 14 個類別 | 14 片是色票不是圖表 |
| 改 `format_monthly_detail` | LINE 的「/財務」查詢還在用。每日信改成不呼叫它就好 |
