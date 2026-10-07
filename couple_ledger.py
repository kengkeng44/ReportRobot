"""
情侶記帳群組：在群組裡直接打「午餐 120」就記進 Notion「共同帳本」。

跟私訊的「記一筆」是兩條線：
  私訊   → 交易明細（我的帳；共同只記我那半）
  本群組 → 共同帳本（兩個人的帳；記整筆，誰付的另外記，月底結算）

預設是共同 —— 這個群組存在的理由就是記一起花的錢。尾巴寫「個人」或
「單人」才是自己的，那種只記錄、不列入結算。

哪個群組是記帳群組由 COUPLE_GROUP_ID 決定。在群組裡打「記帳群組」
會回這個群組的 ID（只有 ADMIN_LINE_USER_ID 本人打才回）。
"""

import os
import re
import time

# 跟 command_router._QUICK_ENTRY_RE 同一個形狀，多吃一個可有可無的
# 「記一筆」前綴。不共用那支：兩邊的分攤預設相反，綁在一起改一邊會壞另一邊。
_ENTRY_RE = re.compile(
    r"^(?:記一筆|記帳)?\s*(?=[^\d])(.{1,12}?)\s*(\d+(?:\.\d+)?)\s*(?:元|塊)?"
    r"\s*(個人|單人|共同)?$")
_CJK_RE = re.compile(r"[一-鿿]")
_MENU_RE = re.compile(r"^(?:記一筆|記帳)\s*(.*)$")

_SETUP_KEYWORDS = {"記帳群組", "設定記帳群組"}
_SETTLE_KEYWORDS = {"結算", "本月結算", "這個月結算"}
_SETTLE_LAST_KEYWORDS = {"上個月結算", "上月結算"}
_RECENT_KEYWORDS = {"共同明細", "最近", "明細", "最近幾筆"}
_UNDO_KEYWORDS = {"撤銷", "刪掉上一筆", "刪除上一筆", "取消上一筆", "記錯了"}
_HELP_KEYWORDS = {"記帳說明", "記帳教學"}

HELP_TEXT = (
    "💑 共同記帳怎麼用\n\n"
    "直接打就好，不用打「記一筆」：\n"
    "　午餐 120　　← 共同，兩人平分\n"
    "　衣服 990 個人　← 自己的，不列入結算（單人也可以）\n\n"
    "其他：\n"
    "　記一筆　　← 跳常記的按鈕，按一下就記好\n"
    "　結算　　　← 這個月誰該給誰多少\n"
    "　上個月結算\n"
    "　最近　　　← 最近 10 筆\n"
    "　撤銷　　　← 刪掉你自己記的最後一筆\n\n"
    "誰打的字就算誰付的錢。"
)


def couple_group_id():
    return os.environ.get("COUPLE_GROUP_ID", "").strip()


def is_couple_chat(ctx):
    gid = couple_group_id()
    return bool(ctx and gid
                and ctx.get("source_type") in ("group", "room")
                and ctx.get("group_id") == gid)


def setup_reply(text, ctx):
    """「記帳群組」→ 回這個群組的 ID，給使用者貼進 Infisical。

    只回本人：群組 ID 不是機密，但任何群組成員一打就跳一串亂碼，
    家人群組裡會很莫名其妙。
    """
    if (text or "").strip().lstrip("/") not in _SETUP_KEYWORDS:
        return None
    if not ctx or ctx.get("source_type") not in ("group", "room"):
        return None
    admin = os.environ.get("ADMIN_LINE_USER_ID", "")
    if not admin or ctx.get("user_id") != admin:
        return None
    gid = ctx.get("group_id") or ""
    if gid and gid == couple_group_id():
        return "✅ 這個群組已經是記帳群組了。打「記帳說明」看怎麼用。"
    return ("這個群組的 ID：\n\n"
            f"{gid}\n\n"
            "把它貼到 Infisical 的 COUPLE_GROUP_ID，部署完之後\n"
            "這個群組打「午餐 120」就會記進共同帳本。")


def parse_entry(text):
    """「午餐 120」/「記一筆 晚餐 600 個人」→ {item, total, kind}。不是記帳回 None。"""
    m = _ENTRY_RE.match((text or "").strip())
    if not m:
        return None
    item = m.group(1).strip()
    if not item or not _CJK_RE.search(item):
        return None
    total = float(m.group(2))
    total = int(total) if total == int(total) else total
    if total <= 0:
        return None
    kind = "個人" if m.group(3) in ("個人", "單人") else "共同"
    return {"item": item, "total": total, "kind": kind}


# ── 快取（給按鈕用；理由同 command_router._TXN_CACHE）───────────
_CACHE = {"at": 0.0, "rows": None}
_CACHE_TTL = 600


def _cached_rows():
    import notion_db
    now = time.monotonic()
    if _CACHE["rows"] is None or now - _CACHE["at"] > _CACHE_TTL:
        _CACHE["rows"] = notion_db.couple_load()
        _CACHE["at"] = now
    return _CACHE["rows"]


def warm_cache():
    import notion_db
    if couple_group_id() and notion_db.is_configured():
        _cached_rows()


def _as_txns(rows):
    """共同帳本的列 → finance_report 統計函式吃的形狀，按鈕邏輯整套沿用。"""
    return [{"source": "手動", "direction": "支出", "date": r.get("date"),
             "shop": r.get("item"), "total": r.get("total"),
             "amount": r.get("total"), "split_type": r.get("kind")}
            for r in rows or []]


def suggestions(limit_items=13):
    """表單與按鈕用的建議：一鍵組合、常記品項、各品項常用金額。"""
    import finance_report as fr
    txns = _as_txns(_cached_rows())
    items = fr.frequent_expense_items(txns, limit=limit_items)
    return {
        "combos": [{"item": s, "total": t, "kind": k}
                   for s, t, k in fr.frequent_combos(txns)],
        "items": items,
        "amounts": {i: fr.frequent_amounts(txns, i, limit=6) for i in items},
    }


# ── 記帳 ─────────────────────────────────────────────────

def record(entry, user_id, group_id=None, source="LINE"):
    """寫進共同帳本。回 (成功與否, 寫入的列)。

    付款人 = 打字的人。暱稱查不到就留空照記 —— 錢的紀錄比名字重要，
    結算靠的是付款人ID 不是名字。
    """
    import finance_report
    import line_sender
    import notion_db
    from tz_utils import today_tpe

    payer = line_sender.group_member_name(group_id or couple_group_id(), user_id) or ""
    row = {
        "date": today_tpe().isoformat(),
        "item": entry["item"],
        "total": entry["total"],
        "kind": entry["kind"],
        "payer": payer,
        "payer_id": user_id or "",
        "category": finance_report.guess_category(entry["item"]),
        "source": source,
    }
    page_id = notion_db.couple_add(row)
    if not page_id:
        return False, row
    row["page_id"] = page_id
    if _CACHE["rows"] is not None:
        _CACHE["rows"] = [row] + list(_CACHE["rows"])
    return True, row


def confirm_text(row):
    who = row.get("payer") or "你"
    if row["kind"] == "個人":
        return f"✅ {row['item']} NT${row['total']:,}（{who} 個人，不列入結算）"
    return f"✅ {row['item']} NT${row['total']:,}（共同・{who}付）"


# ── 結算 ─────────────────────────────────────────────────

def settle_text(rows, label):
    """這段期間誰該給誰多少。rows 是 couple_load 的輸出。

    共同的每筆兩人各負擔一半，所以 A 的淨額 = A 付的 − 共同總額 / 2。
    正的是多付了、該收錢；負的是該給錢。只有一個人付過錢時另一個人
    的名字無從得知，寫「對方」。
    """
    shared = [r for r in rows if r.get("kind") != "個人" and r.get("total")]
    personal = [r for r in rows if r.get("kind") == "個人" and r.get("total")]
    if not shared and not personal:
        return f"💑 {label}還沒有記帳。"

    names, paid = {}, {}
    for r in shared + personal:
        pid = r.get("payer_id") or r.get("payer") or "?"
        if r.get("payer"):
            names.setdefault(pid, r["payer"])
    for r in shared:
        pid = r.get("payer_id") or r.get("payer") or "?"
        paid[pid] = paid.get(pid, 0) + r["total"]

    total = sum(r["total"] for r in shared)
    lines = [f"💑 {label}共同支出 NT${_fmt(total)}（{len(shared)} 筆）"]
    for pid, amt in sorted(paid.items(), key=lambda kv: -kv[1]):
        lines.append(f"・{names.get(pid, '對方')} 付了 NT${_fmt(amt)}")

    half = total / 2
    if len(paid) == 1:
        (pid, amt), = paid.items()
        owe = amt - half
        if owe >= 1:
            lines.append(f"→ 對方要給 {names.get(pid, '付款的人')} NT${_fmt(owe)}")
    elif len(paid) == 2:
        (a, pa), (b, pb) = sorted(paid.items(), key=lambda kv: -kv[1])
        owe = pa - half
        if owe >= 1:
            lines.append(f"→ {names.get(b, '對方')} 要給 {names.get(a, '對方')} "
                         f"NT${_fmt(owe)}")
        else:
            lines.append("→ 剛好打平 👍")
    elif paid:
        lines.append("（付款人超過兩位，請到 Notion 看明細）")

    if personal:
        per = {}
        for r in personal:
            pid = r.get("payer_id") or r.get("payer") or "?"
            per[pid] = per.get(pid, 0) + r["total"]
        parts = [f"{names.get(p, '對方')} NT${_fmt(v)}" for p, v in per.items()]
        lines.append("")
        lines.append("個人（不列入結算）：" + "、".join(parts))
    return "\n".join(lines)


def _fmt(n):
    n = int(n + 0.5) if isinstance(n, float) else n
    return f"{n:,}"


def _month_bounds(today, offset=0):
    """offset=0 本月、-1 上個月 → (since, until, 標籤)。"""
    import calendar
    from datetime import date
    y, m = today.year, today.month + offset
    while m < 1:
        y, m = y - 1, m + 12
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1).isoformat(), date(y, m, last).isoformat(), f"{m} 月"


def recent_text(rows, n=10):
    if not rows:
        return "共同帳本還是空的。打「午餐 120」記第一筆。"
    out = ["💑 最近幾筆"]
    for r in rows[:n]:
        tag = "個人" if r.get("kind") == "個人" else "共同"
        who = r.get("payer") or ""
        out.append(f"{r.get('date', '')[5:]}　{r.get('item')}　"
                   f"NT${_fmt(r.get('total') or 0)}　{tag}{('・' + who) if who else ''}")
    return "\n".join(out)


# ── 按鈕 ─────────────────────────────────────────────────

def _menu_reply(arg):
    """「記一筆」→ 表單 + 一鍵組合 + 常記品項；「記一筆 午餐」→ 金額按鈕。"""
    import finance_report as fr
    import liff_api
    from flex_builder import QUICK_REPLY_MAX, quick_reply_text

    try:
        txns = _as_txns(_cached_rows())
    except Exception as e:
        print(f"共同帳本載入失敗：{e}")
        return HELP_TEXT

    if arg:
        amounts = fr.frequent_amounts(txns, arg)
        hint = f"{arg} 多少錢？點下面的，或直接打「{arg} 95」"
        return quick_reply_text(hint, [(f"{a:,}" if isinstance(a, int) else str(a),
                                        f"{arg} {a}") for a in amounts])

    options = []
    url = liff_api.liff_url()
    if url:
        options.append(("📝 表單", url))
    for s, t, k in fr.frequent_combos(txns):
        tail = " 個人" if k == "個人" else ""
        label = (f"{s} {t:,}" if isinstance(t, int) else f"{s} {t}") + tail
        options.append((label, f"{s} {t}{tail}"))
    room = QUICK_REPLY_MAX - len(options)
    options += [(n, f"記一筆 {n}")
                for n in fr.frequent_expense_items(txns, limit=max(room, 0))]
    return quick_reply_text("要記什麼？按組合直接記好，按品項再選金額。\n"
                            "也可以直接打「午餐 120」。", options)


# ── 入口 ─────────────────────────────────────────────────

def handle(text, ctx):
    """記帳群組的訊息。不是記帳相關回 None，讓 command_router 照常處理。"""
    import notion_db
    from tz_utils import today_tpe

    t = (text or "").strip().lstrip("/").strip()
    if not t:
        return None
    user_id = (ctx or {}).get("user_id")

    if t in _HELP_KEYWORDS:
        return HELP_TEXT

    try:
        if t in _SETTLE_KEYWORDS or t in _SETTLE_LAST_KEYWORDS:
            offset = -1 if t in _SETTLE_LAST_KEYWORDS else 0
            since, until, label = _month_bounds(today_tpe(), offset)
            return settle_text(notion_db.couple_load(limit=500, since=since,
                                                     until=until), label)

        if t in _RECENT_KEYWORDS:
            return recent_text(notion_db.couple_load(limit=10))

        if t in _UNDO_KEYWORDS:
            return _undo(user_id)

        entry = parse_entry(t)
        if entry:
            ok, row = record(entry, user_id, (ctx or {}).get("group_id"))
            if not ok:
                return "寫入 Notion 失敗，請稍後再試。"
            from flex_builder import quick_reply_text
            # 打錯字最常見的補救就是馬上刪掉，按鈕放在確認訊息上最順手
            return quick_reply_text(confirm_text(row), [("撤銷這筆", "撤銷")])

        m = _MENU_RE.match(t)
        if m:
            return _menu_reply(m.group(1).strip())
    except Exception as e:
        print(f"共同記帳失敗：{e}")
        return "共同帳本暫時連不上，等一下再試。"

    return None


def _undo(user_id):
    """刪掉這個人自己記的最後一筆。只刪自己的 —— 不能幫對方撤銷。"""
    import notion_db
    rows = notion_db.couple_load(limit=30)
    mine = next((r for r in rows if r.get("payer_id") == user_id), None)
    if not mine:
        return "找不到你記過的帳。"
    if not notion_db.couple_delete(mine["page_id"]):
        return "刪除失敗，請稍後再試。"
    if _CACHE["rows"] is not None:
        _CACHE["rows"] = [r for r in _CACHE["rows"]
                          if r.get("page_id") != mine["page_id"]]
    return (f"🗑️ 已撤銷：{mine['date'][5:]} {mine['item']} "
            f"NT${_fmt(mine.get('total') or 0)}")
