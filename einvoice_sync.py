"""財政部「消費發票彙整通知」→ 食材庫存的自動同步。

跟 `finance_sync` 同一個形狀(Gmail → parser → 去重 → Notion),
差別在國泰那封是 **HTML 內文**,財政部這封是 **CSV 附件** ——
附件要多一次 attachments().get() 才拿得到內容。

為什麼會有這條線
────────────
`einvoice.py` 的 API 路徑寫好了但個人拿不到 AppID(112/3/31 起個人不再
列入開發者範圍,門檻是 ISO27001)。手動匯出雖然可行,但要記得每月做一次;
彙整通知服務開通後財政部會自己把 CSV 寄來,這支就能把最後一段手工也去掉。

**這條線不碰交易明細。** 記帳歸國泰彙整信管,載具負責「買了什麼菜」。

2026-09-07 首次以真實信件跑通(寫入 174 筆)
────────────────────────────────
寄件人已釘死(見 QUERY)。主旨仍維持寬鬆比對:查詢寫太死會靜默抓不到信,
而「沒抓到」跟「這個月沒有新信」在 log 上長得一樣。
"""

import base64

import einvoice_csv
import einvoice_pantry


# 寄件人於 2026-09-07 收到第一封真實信件後釘死 —— 在那之前整個查詢
# 都是照公開資訊猜的,每跑一次就要把一堆無關的信抓下來拆附件。
#
# 主旨那段維持寬鬆:真實主旨是
#   「財政部電子發票整合服務平台[手機條碼]-消費發票彙整通知,手機條碼: …(每月)…」
# 前後都掛著會變動的東西(前綴、載具號碼、英文對照),寫死全文會在
# 財政部改版時整批漏掉,而漏掉跟「這個月沒有新信」在 log 上長得一樣。
QUERY = ('from:einvoice@einvoice.nat.gov.tw '
         'subject:(消費發票彙整通知 OR 消費彙整通知) has:attachment')

DEFAULT_MAX_RESULTS = 20


def _csv_parts(payload):
    """遞迴找出所有 CSV 附件節點。

    只認副檔名不認 mimeType —— 實務上寄件系統常把 CSV 標成
    application/octet-stream。反過來說信裡若附了 PDF 說明,
    拿去餵 CSV parser 只會得到一堆垃圾。
    """
    if not isinstance(payload, dict):
        return

    filename = payload.get("filename") or ""
    body = payload.get("body") or {}
    if filename.lower().endswith(".csv") and body.get("attachmentId"):
        yield payload

    for sub in payload.get("parts") or []:
        yield from _csv_parts(sub)


def _decode_attachment(data):
    """base64url → 文字。解不開回空字串,由呼叫端跳過該封。

    109/6 起彙整通知是 UTF-8,在那之前是 BIG5;平台匯出還會帶 BOM。
    兩種都試,因為使用者可能拿舊檔來補匯入。
    """
    if not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data.encode("utf-8"))
    except Exception:
        return ""

    for encoding in ("utf-8-sig", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _header(payload, name):
    """從 Gmail payload 抽一個 header。缺 header 是常態,回空字串。"""
    for h in (payload or {}).get("headers") or []:
        if (h.get("name") or "").lower() == name.lower():
            return h.get("value") or ""
    return ""


def fetch_rows(service, query=QUERY, max_results=DEFAULT_MAX_RESULTS,
               with_meta=False):
    """Gmail → 可寫入食材庫存的列。

    單封信失敗只略過該封:這個工作跟每日推播共用同一個 process,
    不能因為一封壞信拖垮整批。

    with_meta=True 時另回每封命中信件的寄件人與主旨。當初是為了讓系統
    自己回報「財政部用哪個位址寄」,拿到之後 QUERY 已經收窄。留著當
    除錯用:下次抓不到信時,把它打開就能看見實際命中了什麼。
    """
    rows = []
    mails = []
    try:
        listed = service.users().messages().list(
            userId="me", q=query, maxResults=max_results).execute()
    except Exception as e:
        print(f"[einvoice] 列信失敗：{e}")
        return (rows, mails) if with_meta else rows

    for meta in listed.get("messages") or []:
        mid = meta.get("id")
        try:
            msg = service.users().messages().get(
                userId="me", id=mid, format="full").execute()
        except Exception as e:
            print(f"[einvoice] 讀信失敗 {mid}：{e}")
            continue

        for part in _csv_parts(msg.get("payload")):
            att_id = (part.get("body") or {}).get("attachmentId")
            try:
                att = service.users().messages().attachments().get(
                    userId="me", messageId=mid, id=att_id).execute()
            except Exception as e:
                print(f"[einvoice] 附件下載失敗 {mid}：{e}")
                continue

            text = _decode_attachment(att.get("data"))
            if not text:
                print(f"[einvoice] 附件解碼失敗 {mid}（編碼不是 UTF-8 也不是 BIG5）")
                continue

            rows.extend(einvoice_pantry.to_pantry_rows(einvoice_csv.parse(text)))
            payload = msg.get("payload") or {}
            mails.append({
                "id": mid,
                "from": _header(payload, "From"),
                "subject": _header(payload, "Subject"),
                "filename": part.get("filename") or "",
            })

    return (rows, mails) if with_meta else rows


def sync(service=None, notion=None):
    """抓信 → 去重 → 寫入食材庫存。回 (寫入數, 跳過數)。

    去重沿用匯入腳本那套 (名稱, 購買日) —— 彙整通知每月一封,
    但同一封信可能被重複處理(重跑排程 / 信件重寄),鍵一樣就會擋掉。
    """
    import import_einvoice

    if service is None:
        import gmail_reader
        service = gmail_reader.get_gmail_service()
    if notion is None:
        import notion_db as notion

    rows = fetch_rows(service)
    if not rows:
        return 0, 0

    to_add, skipped = import_einvoice.plan_import(rows, notion.pantry_load())

    added = 0
    for row in to_add:
        if notion.pantry_add(row):
            added += 1
    print(f"[einvoice] 食材庫存寫入 {added} 筆，跳過 {len(skipped)} 筆")

    return added, len(skipped)
