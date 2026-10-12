"""
交易明細 / 共同帳本的「來源」欄：一欄看懂這筆帳是在哪裡記的、還是哪封信抓來的。

Notion 上顯示的名稱跟程式內部用的值分兩層：
  - 內部值（"手動"、"國泰消費彙整"…）是 parser 算去重指紋的材料，改了會讓
    同一筆刷卡被當成新的、重複記帳 —— 所以內部值永遠不動
  - 寫進 Notion 時換成顯示名稱，讀回來再換回內部值

記錄人：人記的寫名字，信件自動抓的寫「🤖 自動」。
"""

import os
import time

# Notion 每秒約 3 次請求，一次補標上百筆會被限流（2026-10-12 第一次跑就撞到）
_PACE = 0.4

LINE_DM = "💬 LINE私訊"
LINE_GROUP = "👥 LINE群組"
FORM = "📝 記帳表單"
NOTION = "✍️ Notion手打"
SYNCED = "🔁 從交易明細同步"
# 表單 10/7 才上線，之前的「手動」幾乎都是 LINE 私訊記的，但沒辦法確定 ——
# 「（舊）」標示這是推測的
LINE_OLD = "💬 LINE（舊）"

AUTO_RECORDER = "🤖 自動"

# 交易明細：自動抓的那幾種，內部值 → 顯示名稱
_AUTO = {
    "國泰消費彙整": "📧 國泰通知信",
    "國泰電子帳單": "📧 國泰帳單",
    "國泰繳款入帳": "📧 國泰繳款",
    "富邦轉帳": "📧 富邦轉帳",
    "PDF對帳單": "📄 PDF對帳單",
    "載具發票": "🧾 載具發票",
}
_AUTO_BACK = {v: k for k, v in _AUTO.items()}
_MANUAL = {LINE_DM, LINE_GROUP, FORM, NOTION, LINE_OLD}

# 共同帳本：舊值 → 顯示名稱
_COUPLE = {"LINE": LINE_GROUP, "表單": FORM, "自動同步": SYNCED}


def is_auto(internal):
    return internal in _AUTO


def txn_display(internal, channel=None):
    """寫進交易明細時用的名稱。手動記的看是哪個管道，沒說就當 LINE 私訊。"""
    if not internal:
        return None            # 沒說來源就不寫，不要猜
    if internal in _AUTO:
        return _AUTO[internal]
    if internal == "手動":
        return channel or LINE_DM
    return internal


def txn_internal(raw):
    """Notion 讀回來的值 → 程式內部值。空的是有人在 Notion 直接新增的，也算手動。"""
    if not raw or raw in _MANUAL or raw == "手動":
        return "手動"
    return _AUTO_BACK.get(raw, raw)


def txn_channel(raw):
    """給人看的來源。舊資料還沒換新名稱的也照新名稱顯示。"""
    if not raw:
        return NOTION
    if raw == "手動":
        return LINE_OLD
    return _AUTO.get(raw, raw)


def couple_display(raw):
    """共同帳本的來源：舊值換新名稱，空的是 Notion 手打。"""
    if not raw:
        return NOTION
    return _COUPLE.get(raw, raw)


_owner_name = {}


def owner_name():
    """bot 主人的 LINE 暱稱 —— 交易明細是他的帳，人記的都是他記的。"""
    uid = os.environ.get("ADMIN_LINE_USER_ID", "")
    if not uid:
        return ""
    if uid not in _owner_name:
        try:
            import couple_members
            _owner_name[uid] = couple_members.name_of(uid) or ""
        except Exception as e:
            print(f"[sources] 主人暱稱查詢失敗：{e}")
            return ""
    return _owner_name[uid]


# ── 舊資料補標 ───────────────────────────────────────────

def backfill():
    """兩張表的舊資料換成新的來源名稱、補記錄人。重跑安全：只改不一樣的列。"""
    import notion_db

    stats = {"交易明細": 0, "共同帳本": 0}
    me = owner_name()
    for t in notion_db.transactions_load(limit=3000):
        raw, rec = t.get("source_raw"), t.get("recorder")
        want = txn_channel(raw)
        if not rec:
            if is_auto(txn_internal(raw)):
                rec = AUTO_RECORDER
            elif not raw:
                rec = notion_db.page_creator_name(t.get("created_by")) or me
            else:
                rec = me
        if want != raw or rec != t.get("recorder"):
            if notion_db.transaction_set_origin(t["page_id"], want, rec):
                stats["交易明細"] += 1
            time.sleep(_PACE)

    for r in notion_db.couple_load(limit=3000):
        raw = r.get("source_raw")
        want = couple_display(raw)
        rec = r.get("recorder_raw")
        if not rec and not raw:
            rec = notion_db.page_creator_name(r.get("created_by")) or ""
        if want != raw or (rec and rec != r.get("recorder_raw")):
            if notion_db.couple_set_origin(r["page_id"], want, rec or None):
                stats["共同帳本"] += 1
            time.sleep(_PACE)
    return stats
