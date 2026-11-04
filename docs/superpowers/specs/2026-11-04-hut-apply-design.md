# 山屋自動申請（驗證碼轉 LINE）設計

日期：2026-11-04
前一版：`2026-11-02-hut-monitor-design.md`（只監控）。這一版由使用者決定放寬紅線：程式可以登入、填表、送出，但**驗證碼一定由本人看圖回覆**，回覆＝同意送出那一筆。

## 風險（使用者已知悉）

林業署須知：「以非法程式進行申請或干擾系統」→ 全隊停權 3 年（檜谷永久）。本人看驗證碼可降低、但不能排除風險。所以：

- 一次只送一筆（本機 `~/.hut_monitor/apply.lock`），本人沒回就不送，5 分鐘過期。
- 不自動辨識驗證碼、不裝 stealth、不改 UA、不狂刷（步驟間 2 秒）；被擋就停、LINE 告知，不繞。

## 流程

```
本機 tools/hut_apply.py（Playwright，帳密／隊伍從 Infisical 注入）
  登入（林業署）→ 有驗證碼 → POST /admin/hut-captcha {圖, 摘要, step=login}
  填申請表 → 驗證碼 → POST /admin/hut-captcha {圖, 摘要, step=apply}
Railway hut_captcha.py → LINE push 本人（文字＋圖一次 push，配額算 1）
本人 LINE 私訊「驗證碼 1234」／「驗證碼 取消」→ command_router（限 admin）
本機每 3 秒 GET /admin/hut-captcha/<id> → 拿到 code → 填入 → 送出
本機 POST /admin/hut-captcha/<id>/result {結果截圖} → LINE
出錯 → POST /admin/hut-notify {錯誤＋截圖} → LINE
```

摘要只放站名、日期、晚數、人數、隊員姓名；不放身分證、帳密。圖片只存 Railway 記憶體，網址帶隨機 token，5～30 分鐘過期。

一筆申請約用 LINE 配額 3 次（登入驗證碼、申請驗證碼、結果）。

## 網站

| 站 | 登入 | 驗證碼 |
|---|---|---|
| 一站式 hike.taiwan.gov.tw（排雲、九九、七卡、奇萊、南湖） | 不用（草稿用身分證＋Email 取回） | 送出前 |
| 林業署 jmlnt／tconline／kgonline（同一套系統） | 會員帳密 | 登入、送出（待 recon 確認） |

選擇器寫在 `tools/hut_apply.py` 的 `FLOWS`，**目前全是 None**（Claude 的雲端環境連不到這些網站）。先在本機跑 `--recon` 存欄位清單，再補選擇器、`--dry-run` 驗證，最後才真的送。

## Infisical

| 變數 | 用途 |
|---|---|
| `HUT_FOREST_ACCOUNT` / `HUT_FOREST_PASSWORD` | 林業署會員帳密 |
| `HUT_<SITE>_ACCOUNT` / `_PASSWORD` | 某站帳號不同時覆寫（例 `HUT_KGONLINE_ACCOUNT`） |
| `HUT_TEAM_JSON` | 隊伍：`{"team_name","leader":{name,id_no,birthday,gender,phone,email,address},"members":[…],"emergency":{name,phone,relation}}` |

本機執行一律 `infisical run --env=prod -- python tools/hut_apply.py …`，秘密不落地成檔案。

## 之後

1. recon → 補 FLOWS → dry-run → 真送一筆。
2. 接上監控：`hut_browser.py` 抓到符合 `HUT_WANT`（想要的站／日期／人數）的空位時自動叫 `hut_apply.py`。
3. 抽籤制（排雲、嘉明湖、檜谷）在開放日排程自動送抽籤申請。
