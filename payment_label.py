"""交易明細「付款／載具」欄：一眼看出這筆是誰的卡、誰的載具、誰記的。

2026-11-07 使用者在交易明細看到一張阿里山的載具發票，以為是小螢的 ——
原本的欄位只有「來源：🧾 載具發票」，看不出是誰的載具、誰刷的卡。

全部由現有欄位推出來（來源、卡末四碼、發票號碼、記錄人），所以舊資料補得回來。
"""

import threading
import time

# 財政部彙整信只寄到仁和的信箱，裡面只有一個載具（手機條碼 /EKZS6VW，使用者確認是他的）。
# 小螢的載具發票在她自己的「小螢交易明細」，不進這張表。
CARRIER_OWNER = "仁和"
CARD_OWNER = "仁和"          # 國泰通知信、電子帳單都寄到仁和的信箱，卡也都是他的

_BANKS = ("國泰", "富邦", "郵局")


def label(source, card_last4="", invoice_no="", recorder=""):
    source = source or ""
    bank = next((b for b in _BANKS if b in source), "")
    if "載具" in source:
        base = f"🧾 {CARRIER_OWNER}的載具"
    elif "轉帳" in source:
        base = f"🏦 {bank}轉帳（{CARD_OWNER}）"
    elif card_last4:
        base = f"💳 {bank or '信用卡'} *{card_last4}（{CARD_OWNER}）"
    elif "截圖" in source:
        base = f"📸 截圖辨識（{CARD_OWNER}）"
    elif source:
        who = recorder if recorder and "自動" not in recorder else CARD_OWNER
        base = f"✍️ {who}記的"
    else:
        return ""
    if invoice_no and "載具" not in source:
        base += f"＋🧾 {CARRIER_OWNER}的載具"
    return base


def _text(props, name):
    p = props.get(name) or {}
    if p.get("type") == "select":
        return (p.get("select") or {}).get("name") or ""
    return "".join(x.get("plain_text", "") for x in p.get("rich_text") or p.get("title") or [])


def from_props(props):
    return label(_text(props, "來源"), _text(props, "卡末四碼"),
                 _text(props, "發票號碼"), _text(props, "記錄人"))


def rich(value):
    return {"rich_text": [{"text": {"content": value}}] if value else []}


# ── 舊資料補標（背景跑，幾百筆要幾分鐘）──────────────────────────
_status = {"state": "idle"}


def backfill(client=None, sleep=0.35):
    import notion_db
    client = client or notion_db._get_client()
    db_id = notion_db.get_or_create_db("交易明細")
    stats = {"checked": 0, "updated": 0, "failed": 0}
    cursor = None
    while True:
        kw = {"database_id": db_id, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        resp = client.databases.query(**kw)
        for p in resp["results"]:
            stats["checked"] += 1
            want = from_props(p["properties"])
            if not want or _text(p["properties"], "付款／載具") == want:
                continue
            try:
                client.pages.update(page_id=p["id"], properties={"付款／載具": rich(want)})
                stats["updated"] += 1
            except Exception as e:
                stats["failed"] += 1
                print(f"[payment_label] {p['id']} 失敗：{str(e)[:120]}")
            if sleep:
                time.sleep(sleep)
        if not resp.get("has_more"):
            return stats
        cursor = resp["next_cursor"]


def start_backfill():
    if _status.get("state") == "running":
        return False
    _status.clear()
    _status.update({"state": "running", "started": time.time()})

    def work():
        try:
            _status.update({"state": "done", **backfill()})
        except Exception as e:
            _status.update({"state": "error", "error": str(e)[:200]})
        _status["seconds"] = round(time.time() - _status["started"])

    threading.Thread(target=work, daemon=True).start()
    return True


def status():
    return dict(_status)
