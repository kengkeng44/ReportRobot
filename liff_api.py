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
    out = {"modes": modes, "suggestions": {}, "me": user_id, "members": []}
    if "couple" in modes:
        try:
            import couple_members
            out["members"] = couple_members.for_form()
        except Exception as e:
            # 名單拿不到就只能記自己付的，表單照樣能用
            print(f"[liff] 成員名單載入失敗：{e}")
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
    # 其中幫對方付的金額（只有共同帳本用得到）：0 到整筆，超過就當整筆
    try:
        helped = float((payload or {}).get("helped") or 0)
    except (TypeError, ValueError):
        helped = 0
    helped = min(max(helped, 0), total)
    helped = int(helped) if helped == int(helped) else round(helped, 2)
    return {"item": item, "total": total, "kind": kind, "helped": helped}


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
        payer_id = (payload or {}).get("payer_id") or None
        if payer_id and payer_id != user_id and payer_id not in _member_ids():
            return 400, {"ok": False, "message": "付款人不在記帳成員名單上。"}
        entry["date"] = _clean_date((payload or {}).get("date"))
        ok, row = couple_ledger.record(entry, user_id, source="表單", payer_id=payer_id)
        if not ok:
            return 502, {"ok": False, "message": "寫入 Notion 失敗，請稍後再試。"}
        return 200, {"ok": True, "message": couple_ledger.confirm_text(row)}

    # personal：沿用私訊記一筆的同一套（交易明細、共同只記我那半）
    import command_router
    import finance_report
    import notion_db
    from tz_utils import today_tpe

    from datetime import date
    day = _clean_date((payload or {}).get("date"))
    txn = finance_report.build_manual_txn(
        entry["item"], entry["total"], entry["kind"],
        today=date.fromisoformat(day) if day else today_tpe())
    import sources
    txn["channel"] = sources.FORM
    if not notion_db.transaction_add(txn):
        return 502, {"ok": False, "message": "寫入 Notion 失敗，請稍後再試。"}
    command_router._remember_txn(txn)
    if txn["split_type"] == "共同":
        msg = (f"✅ {txn['shop']} 共同 NT${txn['total']:,}，"
               f"你分攤 NT${txn['amount']:,}")
    else:
        msg = f"✅ {txn['shop']} NT${txn['amount']:,}"
    return 200, {"ok": True, "message": msg}


def edit_url(book, page_id):
    """「最近」清單上「編輯」按鈕的網址。LIFF 會把 ? 後面原封不動帶給表單。"""
    base = liff_url()
    return f"{base}?edit={page_id}&book={book}" if base else ""


# ── 編輯 ─────────────────────────────────────────────────
# 權限跟記帳一樣：共同帳本群組成員都能改、都能刪（使用者決定大家權限
# 一樣，靠「記錄人 + 編輯紀錄」讓每一次改動都看得到是誰做的）；
# 我的帳只有本人。

def _can(user_id, book):
    return book in ("couple", "personal") and book in allowed_modes(user_id)


def _personal_view(t):
    return {
        "id": t["page_id"], "item": t.get("shop") or t.get("category") or "",
        "total": t.get("total") or t.get("amount") or 0,
        "kind": t.get("split_type") or "個人", "date": t.get("date"),
        "source": t.get("channel") or "",
        # 銀行同步的刪了會被下一次同步加回來（去重只看還在的列），所以不給刪
        "deletable": (t.get("source") or "手動") == "手動",
    }


def get_entry(user_id, book, page_id):
    """編輯表單打開時要的那筆 + 它的異動紀錄。"""
    import notion_db

    if not _can(user_id, book):
        return 403, {"ok": False, "message": "你沒有這本帳的權限。"}
    if book == "couple":
        row = notion_db.couple_get(page_id)
        if not row:
            return 404, {"ok": False, "message": "找不到這筆，可能已經被刪掉了。"}
        history = notion_db.couple_log_load(limit=20, entry_id=row["page_id"])
        return 200, {"ok": True, "entry": {
            "id": row["page_id"], "item": row.get("item"), "total": row.get("total"),
            "kind": row.get("kind"), "date": row.get("date"),
            "helped": row.get("helped") or 0,
            "payer_id": row.get("payer_id"), "payer": row.get("payer"),
            "recorder": row.get("recorder"), "source": row.get("source") or "",
            "deletable": True,
        }, "history": history}
    t = notion_db.transaction_get(page_id)
    if not t:
        return 404, {"ok": False, "message": "找不到這筆，可能已經被刪掉了。"}
    return 200, {"ok": True, "entry": _personal_view(t), "history": []}


def _clean_date(v):
    from datetime import date
    try:
        return date.fromisoformat(str(v or "")[:10]).isoformat()
    except ValueError:
        return None


def save_entry(user_id, book, page_id, payload):
    """存修改。回 (http 狀態碼, {"ok", "message"})。"""
    if not _can(user_id, book):
        return 403, {"ok": False, "message": "你沒有這本帳的權限。"}
    entry = _clean(payload)
    if not entry:
        return 400, {"ok": False, "message": "品項或金額不對。"}
    entry["date"] = _clean_date((payload or {}).get("date"))
    if book == "couple":
        import couple_ledger
        payer_id = (payload or {}).get("payer_id") or None
        if payer_id and payer_id not in _member_ids():
            return 400, {"ok": False, "message": "付款人不在記帳成員名單上。"}
        ok, msg = couple_ledger.update_entry(page_id, dict(entry, payer_id=payer_id), user_id)
        return (200 if ok else 502), {"ok": ok, "message": msg}
    return _save_personal(page_id, entry)


def delete_entry(user_id, book, page_id):
    import notion_db

    if not _can(user_id, book):
        return 403, {"ok": False, "message": "你沒有這本帳的權限。"}
    if book == "couple":
        import couple_ledger
        ok, msg = couple_ledger.delete_entry(page_id, user_id)
        return (200 if ok else 404), {"ok": ok, "message": msg}
    t = notion_db.transaction_get(page_id)
    if not t:
        return 404, {"ok": False, "message": "找不到這筆，可能已經被刪掉了。"}
    if not _personal_view(t)["deletable"]:
        return 400, {"ok": False, "message": "銀行同步的帳刪了明天會再出現，改金額或改成個人就好。"}
    if not notion_db.transaction_delete(page_id):
        return 502, {"ok": False, "message": "刪除失敗，請稍後再試。"}
    _sync_mirror(t, None, user_id)
    _forget_txn_cache()
    return 200, {"ok": True, "message": f"🗑️ 已刪除：{_personal_view(t)['item']}"}


def _member_ids():
    import couple_members
    return {m["id"] for m in couple_members.for_form()}


def _forget_txn_cache():
    import command_router
    command_router._TXN_CACHE["rows"] = None


def _save_personal(page_id, entry):
    """我的帳：改交易明細，共同的那半照私訊記一筆的規則重算。"""
    import finance_report
    import notion_db

    old = notion_db.transaction_get(page_id)
    if not old:
        return 404, {"ok": False, "message": "找不到這筆，可能已經被刪掉了。"}
    kind = entry["kind"]
    if old.get("direction") == "收入":
        kind = "個人"        # 收入不跟人分
    total = entry["total"]
    amount = finance_report.my_share_of(total) if kind == "共同" else total
    fields = {"shop": entry["item"], "total": total, "amount": amount, "split_type": kind}
    if entry.get("date"):
        fields["date"] = entry["date"]
    if entry["item"] != (old.get("shop") or ""):
        fields["category"] = finance_report.guess_category(entry["item"])
    if not notion_db.transaction_update(page_id, fields):
        return 502, {"ok": False, "message": "寫入 Notion 失敗，請稍後再試。"}
    new = dict(old, shop=entry["item"], total=total, amount=amount, split_type=kind,
               date=fields.get("date", old.get("date")))
    _sync_mirror(old, new, None)
    _forget_txn_cache()
    if kind == "共同":
        msg = f"✏️ 已更新：{entry['item']} 共同 NT${total:,}，你分攤 NT${amount:,}"
    else:
        msg = f"✏️ 已更新：{entry['item']} NT${total:,}"
    return 200, {"ok": True, "message": msg}


def _sync_mirror(old, new, actor_id):
    """交易明細改了 / 刪了，共同帳本裡自動同步的那筆跟著動。

    new=None 是刪除。改成個人就把共同帳本那筆刪掉；從個人改成共同就同步一份過去。
    同步那筆的付款人一律是 bot 主人（理由見 couple_ledger.mirror_txn）。
    """
    import couple_ledger
    import notion_db

    actor_id = actor_id or os.environ.get("ADMIN_LINE_USER_ID", "")
    fp = old.get("fingerprint") or old.get("page_id")
    try:
        mirror = notion_db.couple_find_by_source(fp) if fp else None
        if mirror and (new is None or new.get("split_type") != "共同"):
            couple_ledger.delete_entry(mirror, actor_id)
        elif mirror:
            couple_ledger.update_entry(mirror, {
                "item": new.get("shop"), "total": new.get("total"),
                "date": new.get("date")}, actor_id)
        elif new is not None and new.get("split_type") == "共同":
            couple_ledger.mirror_txn(dict(new, fingerprint=fp))
    except Exception as e:
        # 交易明細已經改好了；共同帳本跟不上明天的重掃也補不回來，至少留 log
        print(f"[liff] 共同帳本連動失敗：{e}")
