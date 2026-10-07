"""
LIFF 記帳表單的後端：驗身分、給建議、寫入。

表單在 LINE 裡開（https://liff.line.me/<LIFF_ID>），一頁填完品項、金額、
共同/個人，按一次送出。兩本帳：
  personal → 交易明細（只有 ADMIN_LINE_USER_ID 本人能寫）
  couple   → 共同帳本（記帳群組的成員都能寫）

身分靠 LIFF 給的 ID token，由 LINE 的 verify 端點驗。**不信任前端送來的
userId** —— 那是任何人都能填的字串。
"""

import os

import requests

_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


def liff_id():
    return os.environ.get("LIFF_ID", "").strip()


def liff_url():
    lid = liff_id()
    return f"https://liff.line.me/{lid}" if lid else ""


def _channel_id():
    """LIFF ID 的格式是「<LINE Login channel ID>-<亂碼>」，前半段就是驗 token 要的 client_id。"""
    return liff_id().split("-", 1)[0]


def verify_id_token(token):
    """ID token → LINE userId。驗不過回 None。"""
    if not token or not liff_id():
        return None
    try:
        r = requests.post(_VERIFY_URL, data={"id_token": token,
                                             "client_id": _channel_id()},
                          timeout=5)
    except Exception as e:
        print(f"[liff] token 驗證例外：{e}")
        return None
    if r.status_code != 200:
        print(f"[liff] token 驗證失敗 {r.status_code}: {r.text[:200]}")
        return None
    return (r.json() or {}).get("sub")


def allowed_modes(user_id):
    """這個人能寫哪幾本帳。順序就是表單預設的優先序。"""
    import couple_ledger
    import line_sender

    modes = []
    gid = couple_ledger.couple_group_id()
    # "" 是 LINE API 暫時查不到 —— 寫入權限寧可這次擋下，也不要放行陌生人
    if gid and line_sender.group_member_name(gid, user_id):
        modes.append("couple")
    admin = os.environ.get("ADMIN_LINE_USER_ID", "")
    if admin and user_id == admin:
        modes.append("personal")
    return modes


def _personal_suggestions():
    import command_router
    import finance_report as fr

    txns = command_router._cached_txns()
    items = fr.frequent_expense_items(txns)
    return {
        "combos": [{"item": s, "total": t, "kind": k}
                   for s, t, k in fr.frequent_combos(txns)],
        "items": items,
        "amounts": {i: fr.frequent_amounts(txns, i, limit=6) for i in items},
    }


def bootstrap(user_id):
    """表單打開時要的全部資料：能寫哪幾本、各自的常用按鈕。"""
    import couple_ledger

    modes = allowed_modes(user_id)
    out = {"modes": modes, "suggestions": {}}
    for mode in modes:
        try:
            out["suggestions"][mode] = (couple_ledger.suggestions()
                                        if mode == "couple"
                                        else _personal_suggestions())
        except Exception as e:
            # 建議拿不到不影響記帳本身，表單照樣能手打
            print(f"[liff] {mode} 建議載入失敗：{e}")
            out["suggestions"][mode] = {"combos": [], "items": [], "amounts": {}}
    return out


def _clean(payload):
    item = str((payload or {}).get("item") or "").strip()[:30]
    try:
        total = float((payload or {}).get("amount"))
    except (TypeError, ValueError):
        return None
    if not item or total <= 0 or total > 10_000_000:
        return None
    total = int(total) if total == int(total) else round(total, 2)
    kind = "個人" if (payload or {}).get("kind") == "個人" else "共同"
    return {"item": item, "total": total, "kind": kind}


def record(user_id, payload):
    """寫一筆。回 (http 狀態碼, {"ok", "message"})。"""
    mode = (payload or {}).get("mode")
    if mode not in allowed_modes(user_id):
        return 403, {"ok": False, "message": "你沒有寫入這本帳的權限。"}
    entry = _clean(payload)
    if not entry:
        return 400, {"ok": False, "message": "品項或金額不對。"}

    if mode == "couple":
        import couple_ledger
        ok, row = couple_ledger.record(entry, user_id, source="表單")
        if not ok:
            return 502, {"ok": False, "message": "寫入 Notion 失敗，請稍後再試。"}
        return 200, {"ok": True, "message": couple_ledger.confirm_text(row)}

    # personal：沿用私訊記一筆的同一套（交易明細、共同只記我那半）
    import command_router
    import finance_report
    import notion_db
    from tz_utils import today_tpe

    txn = finance_report.build_manual_txn(entry["item"], entry["total"],
                                          entry["kind"], today=today_tpe())
    if not notion_db.transaction_add(txn):
        return 502, {"ok": False, "message": "寫入 Notion 失敗，請稍後再試。"}
    command_router._remember_txn(txn)
    if txn["split_type"] == "共同":
        msg = (f"✅ {txn['shop']} 共同 NT${txn['total']:,}，"
               f"你分攤 NT${txn['amount']:,}")
    else:
        msg = f"✅ {txn['shop']} NT${txn['amount']:,}"
    return 200, {"ok": True, "message": msg}
