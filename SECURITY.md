# 安全政策 (Security Policy)

## 本專案的資安做法

這個專案會處理 LINE Channel Token、Gmail OAuth 憑證、Anthropic API Key 等敏感資料，因此採取了以下保護措施：

### 機密管理 (Secrets management)

- **環境變數優先**：`server.py` / `line_sender.py` / `gmail_reader.py` / `weather.py` / `stock_news.py` / `premarket.py` 等模組全部優先讀 `os.environ`，找不到才 fallback 到本機 `config.py`。Railway 部署時完全不需要 `config.py`，所有金鑰存在 Railway Variables 頁。
- **Infisical 集中管理**：所有金鑰統一在 Infisical 維護，自動 sync 到 Railway，避免在 Railway dashboard 手動維護多份。
- **`.gitignore` 全面攔截**：`config.py`、`credentials.json`、`token.pickle`、`*.pickle`、`.env`、`railway_env.txt`、`token_b64.txt` 都被列入 ignore，避免任何金鑰意外進入版本控制。
- **OAuth token 不入庫**：Gmail OAuth 的 `token.pickle` 在本機由 `InstalledAppFlow` 產生；雲端則用 base64 編碼後放在 `TOKEN_PICKLE_B64` 環境變數，由 `gmail_reader._load_creds()` 還原。憑證檔本身永遠不會出現在 git 歷史。
- **PDF 密碼動態覆蓋**：富邦對帳單密碼支援用 `PDF_PASSWORD` 環境變數覆蓋 `PDF_PASSWORD_PREFIX`，密碼非預設格式時不必改程式碼。
- **最小權限 OAuth scope**：Gmail API 只要 `gmail.readonly` 一個 scope，無法寫入或刪除信件。

### Webhook 與 API 入口

- **HMAC-SHA256 webhook 簽章驗證**：`server.py` 的 `verify_line_signature()` 對每筆 LINE webhook payload 驗章，拒絕任何未經 LINE 平台簽章的偽造請求，防止他人偽造請求觸發 Claude API 呼叫燒錢、騷擾使用者。比對採用 `hmac.compare_digest` 防 timing attack；驗章失敗回 `403`（不是 200，避免 LINE 平台誤以為成功而停止重送）。
- **Admin endpoint 保護**：`/admin/run-daily`、`/admin/cost-stats` 都要 `X-Admin-Token` header 比對 `ADMIN_TOKEN` 環境變數，未設 token 時 admin 功能整個停用（`503`）。

### 日誌脫敏

`security_utils.mask()` / `mask_source()` 工具：

- LINE userId / groupId / roomId 在 webhook 與排程 log 內以 `頭4***尾4` 格式輸出
- 不會將 access_token / refresh_token / API key 原值印到 stdout 或 LINE 訊息
- Railway log 雖然只有 owner 可看，但仍以「假設可能被截圖」為前提處理

### 可靠性

- **外部 API exponential backoff retry**（`http_utils.py`）：對 timeout / 429 / 5xx 自動重試 3 次（2→4→8 秒），4xx 不重試。
- **Graceful degradation**：盤前報告整合 5+ 外部資料源（CWA、Yahoo、TWSE、OWM、Anthropic），單一來源失敗不影響整體報告；段落層級失敗時推一段「⚠️ 暫時無法取得」降級文案，避免無聲消失。
- **管理員錯誤通知**（`admin_notify.py`）：retry 用盡、排程錯誤、command 例外都會 push 到 `ADMIN_LINE_USER_ID`，含 5 分鐘 throttle 避免某個 API 持續掛掉時被通知洪水淹沒。通知自身崩潰只往 stderr 留底，不影響主程式。
- **排程冪等性**：APScheduler 設 `coalesce=True` + `misfire_grace_time=300`，搭配每日 flag 檔，避免服務重啟正好踩在排程點導致重複推播。

## Fork 後的使用者須知

如果你 fork 這個 repo 自己用，請務必：

1. **自行建立 `config.py`**：repo 不含 `config.py`（已 gitignore），你需要手動建立一份本機備用設定檔。建議從 `.env.example` 對照欄位，把預設值填進 `config.py` 的 `_env(name, default)` 第二個參數。或是更乾淨的做法：建一個 `.env` 用 `python-dotenv` 載入，完全不寫 `config.py`。
2. **絕對不要 commit 敏感檔案**：
   - 不要 `git add config.py`
   - 不要 `git add credentials.json`、`token.pickle`、`*.pickle`
   - 不要把含真實金鑰的 `.env` / `railway_env.txt` 提交上來
   - commit 前永遠先跑 `git status` 確認沒帶到敏感檔案
3. **不要 force push 把金鑰歷史塗掉就以為沒事**：一旦金鑰進入 GitHub（即使是 private repo 然後 force push 移除），都應視同外洩。請立刻到對應 console 撤銷並重新發行：
   - LINE Channel Access Token：[LINE Developers Console](https://developers.line.biz/) → Messaging API → Channel access token → 重新 issue
   - LINE Channel Secret：同上頁 → Basic settings → Channel secret → 重新 issue
   - Google OAuth：[Google Cloud Console](https://console.cloud.google.com/apis/credentials) 撤銷 client 後重建
   - Anthropic：[console.anthropic.com](https://console.anthropic.com/) 撤銷 API key
   - CWA / OWM：到對應後台重新申請
4. **本機 LINE Channel 與你 fork 的人不同**：請申請自己的 LINE Messaging API Channel，不要直接拿原作者的 token。

## 漏洞回報方式

發現安全性問題（程式碼漏洞、依賴套件 CVE、敏感資料外洩風險等）請透過以下方式回報：

- **開 GitHub Issue**：到 [Issues](https://github.com/kengkeng44/ReportRobot/issues) 開新 issue，標題前綴加 `[SECURITY]`。
- **不要在 issue 內公開貼出真實金鑰、token、PII**：
  - 描述問題本身（例如「`gmail_reader.py:123` 沒驗證 PDF 來源，惡意 PDF 可觸發 X」）即可
  - 如果你必須示範，請用假值（`sk-ant-XXXXXXXX`、`channel_token_FAKE`）
  - 真實的金鑰外洩證據請私下透過 GitHub 的 [Private Vulnerability Reporting](https://github.com/kengkeng44/ReportRobot/security/advisories) 提交，不要貼在公開 issue
- **不要在 PR 描述、commit message、討論串貼真實金鑰**：即使打算事後刪除，GitHub 的 webhook、email 通知、第三方 mirror 都已收到內容。

收到回報後會在合理時間內回覆並評估修復。
