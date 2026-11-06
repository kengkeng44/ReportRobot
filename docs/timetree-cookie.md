# TimeTree

## 目前做法：請 Claude 手動抓（2026-11-07 起）

使用者在 Chrome（仁和設定檔）登入 timetreeapp.com 後，Claude 用 Claude in Chrome
在那個分頁裡呼叫 `/api/v2/calendars`、`/api/v1/calendar/{id}/events/sync`
（瀏覽器自己帶 cookie，Claude 不碰 cookie 值），整理成 rows，
POST 到 `/admin/timetree-import`（X-Admin-Token），寫進 Notion「TimeTree 行程」
（共同記帳底下，DB ac9b55d4fccb4e7aaf4e6557750722b1），用 TimeTree ID upsert。
進度：`GET /admin/timetree-import`。row 格式見 `timetree_import.py` 開頭。

## 備案：cookie 自動讀（目前沒用）

帳號是 Google 登入、沒有密碼，所以機器人用瀏覽器的登入 cookie 讀 TimeTree。
cookie 過期時喵管家會私訊「TimeTree 登入過期」，照下面重抓（約 2 分鐘，要電腦）。

1. 電腦 Chrome 開 https://timetreeapp.com ，用 Google 登入，確認看得到行事曆
2. 按 F12 → 上方「Application」（應用程式）分頁 → 左邊 Cookies → `https://timetreeapp.com`
3. 找 Name 是 `_session_id` 那列，雙擊 Value 全選複製
4. Infisical → reportrobot 專案 → `dev` 環境 → `TIMETREE_SESSION_COOKIE` 貼上取代 → 存
5. 等 Infisical 同步 Railway 重新部署（幾分鐘），打 `POST /admin/timetree-probe` 確認 `ok: true`

注意：cookie 等於登入憑證，不要貼進聊天、不要 commit。
在網頁按「登出」會讓這顆 cookie 失效，抓完直接關分頁就好。

## 紀錄

| 日期 | 事件 |
|------|------|
| 2026-11-07 | 第一次設定 |
