# 交接文件 — 小宇宙逐字稿 + 發票歸戶待辦

**最後更新：** 2026-11-07
**分支：** `claude/xiaoyuzhou-transcribe-gxvr00`（已推上 GitHub，尚未開 PR、尚未合併 main）
**狀態：** 腳本可用，第 112 期已實測完成；有幾個待決定事項（見第 4 節）

> 給下一個 session：先讀「4. 待辦／待決定」和「3. 踩過的坑」。

---

## 1. TL;DR

- 目標：貼小宇宙連結 → 自動下載音檔 → Whisper 轉寫 → OpenCC `s2twp` 轉台灣繁體。不用博主提供的文稿。
- 腳本：`xyz_transcribe.py`（主流程）、`tools/xyz_resume_transcribe.py`（長集數的可續跑轉寫）。
- 實測：第 112 期《改變人生的12個槓桿策略》（105 分鐘）已轉寫完成，整理筆記在
  `transcripts/112_改变人生的12个杠杆策略｜106本心理学工具书极致总结_整理.md`。
- 另外在 Notion「共同待辦」加了一筆「發票歸戶」待辦（第 5 節）。

---

## 2. 檔案與 commit

| Commit | 內容 |
|---|---|
| `7c305ec` | 新增 `xyz_transcribe.py`；`.gitignore` 排除音檔 |
| `4159dfa` | 讓 casts-down 的 aiohttp 走 `HTTPS_PROXY`（`trust_env=True`） |
| `545243a` | 註明安裝時需要 `"av<16"` |
| `587ef08` | `.gitignore` 排除 `transcripts/*.segments.jsonl` |
| `138f93a` | 第 112 期整理筆記 `*_整理.md`；`.gitignore` 排除完整逐字稿 txt/srt |
| （本次） | `tools/xyz_resume_transcribe.py` + 本交接文件 |

### 安裝

```bash
pip install casts_down opencc-python-reimplemented "av<16"
```

### 用法

```bash
# 單集
python xyz_transcribe.py https://www.xiaoyuzhoufm.com/episode/<id>
# 節目最新 N 集（1–15）
python xyz_transcribe.py https://www.xiaoyuzhoufm.com/podcast/<id> --latest 3
```

輸出都在 `transcripts/`：`<標題>.m4a`、`<標題>.txt/.srt`（簡體原稿）、`<標題>_繁.txt/.srt`。

### 長集數（雲端 session 會撞到 2 小時上限時）

```bash
# 先用主腳本下載音檔（轉寫失敗或被停掉都沒關係，音檔會留著）
python -I tools/xyz_resume_transcribe.py "transcripts/<標題>.m4a"
# 被停掉就再跑同一行，會從 .segments.jsonl 的最後時間點接著跑
# 跑完再轉繁體：
python -c "import xyz_transcribe as x; from pathlib import Path; print(x.convert_to_traditional(Path('transcripts')))"
```

---

## 3. 踩過的坑（別重蹈）

1. **網路被擋**（雲端 session）：預設環境擋掉小宇宙和 Hugging Face。已建立個人環境 **「ReportRobot」**，允許以下網域：
   `www.xiaoyuzhoufm.com`、`media.xyzcdn.net`、`huggingface.co`、`cdn-lfs.huggingface.co`、
   `cas-bridge.xethub.hf.co`、`cas-server.xethub.hf.co`、`us.aws.cdn.hf.co`、`transfer.xethub.hf.co`。
   **新 session 要選這個環境**，不要用 Default。
2. **casts-down 參數順序**：它是 click group，`-o/-m/--skip-existing/--latest` 必須放在 URL **前面**，否則會出現 `No such command '-o'`。
3. **aiohttp 不走代理**：casts-down 內部用 aiohttp，預設 `trust_env=False`，在代理環境會出現 `Name or service not known`。`xyz_transcribe.py` 已在子行程裡打開 `trust_env`。
4. **PyAV 19 不相容**：faster-whisper 1.2.1 呼叫 `av.open(..., metadata_errors=...)`，PyAV 16 之後拿掉了這個參數，會出現 `TypeError`。要裝 `av<16`（實測 15.1.0 可用）。
5. **速度與時間上限**：雲端只有 4 核 CPU，medium 模型大約 0.9 倍即時，105 分鐘的節目要約 2 小時。背景工作上限是 2 小時，casts-down 只在最後才寫檔，被停掉就全部白跑。超過約 90 分鐘的集數改用 `tools/xyz_resume_transcribe.py`。
6. **第一次跑會下載模型**：medium 約 1.5 GB，存在 `~/.cache/huggingface`。新容器要重新下載。

---

## 4. 待辦／待決定

- [ ] **完整逐字稿要放哪裡？（等使用者決定）** repo 是公開的，完整逐字稿是節目方的內容，所以目前只推了 `*_整理.md`（摘要、分段筆記、金句），`*_繁.txt/.srt` 被 `.gitignore` 排除。
  - ⚠️ 上一個 session 的全文只存在那個雲端容器裡，容器回收後就沒了。需要的話，用第 2 節的指令重新轉寫第 112 期（約 2 小時）。
  - 選項：放到使用者的 Notion 私人頁面，或改用 private repo。使用者如果要「全文修錯字、補標點」版本，也一起放在那裡，不要放進公開 repo。
- [ ] **是否開 PR 合併到 main**：使用者還沒要求，不要自己開。
- [ ] （可選）把續跑功能整合進 `xyz_transcribe.py`，例如加 `--resume` 參數，就不用分兩支腳本。
- [ ] （可選）`xyz_transcribe.py` 目前沒有單元測試；`classify_url`、`build_command`、`convert_to_traditional` 都是純函式，容易補測試（`tests/` 底下用 pytest）。

---

## 5. 發票歸戶待辦（Notion）

- 頁面：[發票歸戶：把各通路會員載具歸戶到手機條碼](https://app.notion.com/p/4107854a2f6c81fabbe4d88e91e16793)
  位置：ReportRobot / 共同記帳 / 共同待辦（生活／📋 手續／P2，專案「💳 辦卡與歸戶」）。
- 依據：Gmail 近 6 個月的發票通知。以下通路的發票開在會員或平台載具，不是手機條碼 `/EK***VW`：
  全聯福利卡（已有另一筆「全聯載具歸戶」待辦）、蝦皮、Coupang 酷澎、麥當勞 App、好市多線上購物、綠界 ECPay（專心練）、ezPay（上聯國際展覽）、光貿 amego（繪馬梨企業社）。
- 手機條碼本身已經啟用自動匯款（115 年 3–4、5–6、7–8 月期都有中獎匯款紀錄）。
- 這是使用者要自己操作的事（登入財政部電子發票平台或各 App），session 只負責整理清單。
