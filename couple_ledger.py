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
_PANEL_KEYWORDS = {"選單", "面板", "記帳選單"}
_CHART_RE = re.compile(r"^(?:圖表|圓餅圖|圓餅|統計)(?:\s*(.+))?$")
_TODO_RE = re.compile(r"^(?:待辦|todo)(?:\s+(.*))?$", re.IGNORECASE)

HELP_TEXT = (
    "💑 共同記帳怎麼用\n\n"
    "直接打就好，不用打「記一筆」：\n"
    "　午餐 120　　← 共同，兩人平分\n"
    "　衣服 990 個人　← 自己的，不列入結算（單人也可以）\n\n"
    "其他：\n"
    "　記一筆　　← 跳常記的按鈕，按一下就記好\n"
    "　結算　　　← 這個月誰該給誰多少\n"
    "　上個月結算 / 結算 9月　← 看任何一個月\n"
    "　最近　　　← 最近 10 筆\n"
    "　撤銷　　　← 刪掉你自己記的最後一筆\n"
    "　圖表　　　← 本月共同支出圓餅圖（圖表 9月 看別的月）" + chr(10) +
    "　選單　　　← 叫出按鈕卡，長按設成公告就會釘在最上面\n\n"
    "兩人共用的待辦：\n"
    "　待辦 買衛生紙　← 新增\n"
    "　待辦　　　　　← 看清單，按「完成」就劃掉\n\n"
    "誰打的字就算誰付的錢。私訊或表單記成「共同」的、" + chr(10) +
    "全聯刷卡，也會自動同步過來（算你付的）。" + chr(10) + chr(10) +
    "結算：每筆共同的錢兩人各出一半，" + chr(10) +
    "多付的人收回差額 —— 只看當月，月與月分開算。"
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


# ── 從交易明細自動同步 ───────────────────────────────────

def mirror_txn(txn):
    """交易明細裡「共同」的那筆，同步一份到共同帳本。回 True 表示這次有寫入。

    付款人一律是你（ADMIN_LINE_USER_ID）：交易明細是你的帳 —— 你的信用卡、
    你私訊記的、你的表單 —— 錢都是你付的。金額用原始總額（整筆），結算
    時才分一半；交易明細的「金額」已經是你那半，拿它會少算一半。

    去重靠交易明細的 Fingerprint：國泰同步每天都會重跑，沒有去重的話
    同一筆全聯會每天多一筆。
    """
    import finance_report
    import line_sender
    import notion_db

    if not txn or txn.get("split_type") != "共同" or txn.get("direction") == "收入":
        return False
    gid = couple_group_id()
    admin = os.environ.get("ADMIN_LINE_USER_ID", "")
    # 在 Notion 手動新增的列沒有 Fingerprint，退而用頁面 ID 去重
    fp = txn.get("fingerprint") or txn.get("page_id")
    if not (gid and admin and fp):
        return False
    if notion_db.couple_has_source(fp):
        return False

    total = txn.get("total") if txn.get("total") is not None else txn.get("amount")
    if not total:
        return False
    item = (txn.get("shop") or txn.get("category") or "消費").strip()
    row = {
        "date": (txn.get("date") or "")[:10],
        "item": item,
        "total": int(total) if float(total) == int(total) else total,
        "kind": "共同",
        "payer": line_sender.group_member_name(gid, admin) or "",
        "payer_id": admin,
        "category": txn.get("category") or finance_report.guess_category(item),
        "source": "自動同步",
        "source_id": fp,
    }
    page_id = notion_db.couple_add(row)
    if not page_id:
        return False
    row["page_id"] = page_id
    if _CACHE["rows"] is not None:
        _CACHE["rows"] = [row] + list(_CACHE["rows"])
    return True


def mark_shared(txn):
    """把交易明細的一筆改成共同（金額改成我那半）並同步進共同帳本。

    只有「原始總額還沒填」時才砍半：那代表金額欄還是整筆。已經填過的
    （私訊記的共同、全聯自動規則）金額本來就是我那半，再砍會變四分之一。
    """
    import finance_report
    import notion_db

    t = dict(txn)
    if not t.get("total_set"):
        total = t.get("amount")
        if not total:
            return False
        half = finance_report.my_share_of(total)
        if not notion_db.transaction_set_split(t["page_id"], "共同", half, total):
            return False
        t.update(amount=half, total=total, total_set=True)
    elif t.get("split_raw") != "共同":
        if not notion_db.transaction_set_split(t["page_id"], "共同"):
            return False
    t["split_type"] = t["split_raw"] = "共同"
    mirror_txn(t)
    return True


def rescan(limit=1000):
    """重掃交易明細，該進共同帳本的都補進去。每天排程跑，也可手動觸發。重跑安全。

    三種會被抓到：
      1. 已經是共同、但還沒進帳本的（國泰同步、私訊記一筆）
      2. 你在 Notion 手動把分攤類型改成共同的 —— 金額欄還是整筆，順手砍成你那半
      3. 還沒標分攤、但店名在自動共同清單裡的（全聯、康達盛通），舊資料一起補標
    標了「個人」的不動：那是你明確決定過的。
    """
    import finance_report
    import notion_db

    stats = {"mirrored": 0, "marked": 0}
    for t in notion_db.transactions_load(limit=limit):
        if t.get("direction") == "收入" or not t.get("page_id"):
            continue
        if t.get("split_raw") == "共同":
            if not t.get("total_set"):
                if mark_shared(t):
                    stats["marked"] += 1
            elif mirror_txn(t):
                stats["mirrored"] += 1
        elif t.get("split_raw") is None and finance_report.is_shared_shop(t.get("shop")):
            if mark_shared(t):
                stats["marked"] += 1
    return stats


def backfill(limit=1000):
    """舊名保留給 /admin/couple-backfill。"""
    st = rescan(limit)
    return st["mirrored"] + st["marked"]


# ── 私訊「整理共同」：一筆一筆點 ─────────────────────────

_REVIEW_KEYWORDS = {"整理共同", "標共同", "整理雙人"}
# 換行用 chr(10)：這個專案的編輯流程把字面量 \n 轉成真的換行踩過好幾次
_NL = chr(10)
_REVIEW = {"rows": [], "skip": set()}


def _pending_review(refresh=False):
    """還沒標分攤的支出（新到舊）。整理期間用快取，每按一下不必重撈 Notion。"""
    import notion_db
    if refresh or not _REVIEW["rows"]:
        _REVIEW["rows"] = [t for t in notion_db.transactions_load(limit=1000)
                           if t.get("split_raw") is None and t.get("page_id")
                           and t.get("direction") != "收入" and t.get("amount")]
        _REVIEW["skip"] = set()
    return [t for t in _REVIEW["rows"] if t["page_id"] not in _REVIEW["skip"]]


def _review_card(note=""):
    rows = _pending_review()
    if not rows:
        return (note + _NL if note else "") + "✅ 沒有還沒分的帳了。"
    t = rows[0]
    name = (t.get("shop") or "").strip() or f"（{t.get('category') or '沒有店名'}）"
    src = "國泰" if (t.get("source") or "").startswith("國泰") else (t.get("source") or "")
    body = (f"{(t.get('date') or '')[5:]}　{name}{_NL}NT${int(t['amount']):,}　{src}"
            f"{_NL}{_NL}這筆是兩個人一起的嗎？（還有 {len(rows)} 筆）")
    if note:
        body = note + _NL + _NL + body

    def _pb(label, choice):
        return {"type": "action", "action": {
            "type": "postback", "label": label, "displayText": label,
            "data": f"action=split_mark&pid={t['page_id']}&s={choice}"}}

    return {"type": "text", "text": body, "quickReply": {"items": [
        _pb("共同", "shared"), _pb("個人", "personal"),
        _pb("跳過", "skip"), _pb("先到這", "stop")]}}


def review_start():
    return _review_card()


def review_postback(page_id, choice):
    """按了共同 / 個人 / 跳過 / 先到這。回下一張卡。"""
    import notion_db

    if choice == "stop":
        _REVIEW["rows"] = []
        return "好，先整理到這。想繼續再打「整理共同」。"
    t = next((r for r in _REVIEW["rows"] if r["page_id"] == page_id), None)
    if t is None:
        # 伺服器重啟過或卡片太舊，重新撈一次接著整理
        _pending_review(refresh=True)
        return _review_card("那張卡片過期了，從最新的接著整理：")
    note = ""
    if choice == "shared":
        note = "👍 已標共同，也進共同帳本了。" if mark_shared(t) else "⚠️ 寫入失敗，這筆先跳過。"
    elif choice == "personal":
        note = ("已標個人。" if notion_db.transaction_set_split(page_id, "個人")
                else "⚠️ 寫入失敗，這筆先跳過。")
    _REVIEW["skip"].add(page_id)
    return _review_card(note)


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


_SETTLE_MONTH_RE = re.compile(
    r"^(?:結算\s*(?:(\d{4})[-/年])?(\d{1,2})\s*月?|(?:(\d{4})[-/年])?(\d{1,2})\s*月\s*結算)$")


def _settle_offset(text, today):
    """「結算」→0、「上個月結算」→-1、「結算 9月」「9月結算」「結算 2026-09」→ 跟今天差幾個月。
    不是結算指令回 None。未來的月份當成去年的（10 月打「結算 12」是去年 12 月）。"""
    if text in _SETTLE_KEYWORDS:
        return 0
    if text in _SETTLE_LAST_KEYWORDS:
        return -1
    m = _SETTLE_MONTH_RE.match(text)
    if not m:
        return None
    year = m.group(1) or m.group(3)
    month = int(m.group(2) or m.group(4))
    if not 1 <= month <= 12:
        return None
    y = int(year) if year else today.year
    offset = (y - today.year) * 12 + (month - today.month)
    if offset > 0 and not year:
        offset -= 12
    return offset if offset <= 0 else None


def _settle_reply(rows, label, offset, today):
    """結算文字 + 前後月份按鈕，一路點回去看每個月。"""
    from flex_builder import quick_reply_text

    options = []
    for off in (offset - 1, offset + 1, 0):
        if off > 0 or off == offset or any(o[1] == _month_cmd(today, off) for o in options):
            continue
        options.append((_month_label(today, off), _month_cmd(today, off)))
    return quick_reply_text(settle_text(rows, label), options)


def _chart_reply(offset, today):
    """圓餅圖 + 一句總結（附前後月份按鈕）。沒資料就只回文字。"""
    import couple_chart
    import notion_db
    from flex_builder import quick_reply_text

    since, until, label = _month_bounds(today, offset)
    rows = notion_db.couple_load(limit=500, since=since, until=until)
    image = couple_chart.chart_messages(rows, label)
    if not image:
        return f"💑 {label}還沒有共同支出，沒有圖可以畫。"
    options = []
    for off in (offset - 1, offset + 1):
        if off <= 0:
            options.append((_month_label(today, off),
                            "圖表 " + _month_cmd(today, off).split(" ", 1)[1]))
    options.append(("看結算", _month_cmd(today, offset)))
    return [image, quick_reply_text(f"{label}共同支出依品項分。打「結算」看誰該給誰。",
                                    options)]


def _ym(today, offset):
    y, m = today.year, today.month + offset
    while m < 1:
        y, m = y - 1, m + 12
    return y, m


def _month_label(today, offset):
    y, m = _ym(today, offset)
    return f"{m} 月" if y == today.year else f"{y} 年 {m} 月"


def _month_cmd(today, offset):
    y, m = _ym(today, offset)
    return f"結算 {y}-{m:02d}"


def _month_bounds(today, offset=0):
    """offset=0 本月、-1 上個月 → (since, until, 標籤)。"""
    import calendar
    from datetime import date
    y, m = _ym(today, offset)
    last = calendar.monthrange(y, m)[1]
    return (date(y, m, 1).isoformat(), date(y, m, last).isoformat(),
            _month_label(today, offset))


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

    if t in _PANEL_KEYWORDS:
        return panel_flex()

    # 待辦要排在記帳之前：「待辦 繳電費 1200」長得像一筆帳
    m = _TODO_RE.match(t)
    if m:
        return _todo((m.group(1) or "").strip(), ctx)

    try:
        today = today_tpe()

        m = _CHART_RE.match(t)
        if m:
            # 「圖表 9月」借用結算的月份解析，兩個指令吃一樣的月份寫法
            offset = _settle_offset(("結算 " + m.group(1)) if m.group(1) else "結算", today)
            if offset is not None:
                return _chart_reply(offset, today)

        offset = _settle_offset(t, today)
        if offset is not None:
            since, until, label = _month_bounds(today, offset)
            rows = notion_db.couple_load(limit=500, since=since, until=until)
            return _settle_reply(rows, label, offset, today)

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


def _todo(body, ctx):
    """群組待辦：兩人共用一份，用群組 ID 當清單主人。

    personal.py 的待辦本來就是「依 ID 分清單」，拿群組 ID 當 ID 就得到一份
    共用清單，存取、Notion 同步、完成按鈕全部沿用。每日個人信只讀
    PERSONAL_USER_ID 那份，所以群組待辦不會混進你的信裡。
    """
    import command_router
    import personal
    from flex_builder import todo_list_flex

    key = (ctx or {}).get("group_id")
    if not key:
        return None
    try:
        if not body:
            return todo_list_flex(personal.list_todos(key))
        return command_router._handle_todo_subcmd(key, body)
    except Exception as e:
        print(f"群組待辦失敗：{e}")
        return "待辦暫時連不上，等一下再試。"


def panel_flex():
    """群組的常駐面板：一張大按鈕卡，設成公告就會一直釘在群組最上面。

    LINE 的六格選單只出現在跟 bot 的私訊，群組裡唯一能「一直看得到」的
    是公告。所以做一張卡讓使用者長按設成公告，而不是每次打字叫出來。
    """
    import liff_api

    def _btn(label, action, style="secondary", color=None):
        b = {"type": "button", "style": style, "height": "md", "action": action}
        if color:
            b["color"] = color
        return b

    def _msg(label, text):
        return {"type": "message", "label": label, "text": text}

    rows = []
    url = liff_api.liff_url()
    if url:
        # 黃底黑字：整個聊天室裡最亮的一塊，一眼就找得到
        rows.append(_btn("記帳", {"type": "uri", "label": "📝 開記帳表單", "uri": url},
                         style="secondary", color="#FFD400"))
    rows.append({"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
        _btn("結算", _msg("結算", "結算")),
        _btn("最近", _msg("最近", "最近")),
    ]})
    rows.append({"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
        _btn("待辦", _msg("待辦", "待辦")),
        _btn("撤銷", _msg("撤銷", "撤銷")),
    ]})
    rows.append(_btn("圖表", _msg("📊 本月圓餅圖", "圖表")))
    try:
        import notion_db
        notion_url = notion_db.couple_db_url()
    except Exception as e:
        print(f"共同帳本網址取得失敗：{e}")
        notion_url = ""
    if notion_url:
        rows.append(_btn("Notion", {"type": "uri", "label": "📒 Notion 共同帳本",
                                    "uri": notion_url}, style="link"))

    return {
        "type": "flex",
        "altText": "💑 記帳面板",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box", "layout": "vertical", "spacing": "md",
                "contents": [
                    {"type": "text", "text": "💑 我們的記帳", "weight": "bold", "size": "lg"},
                    {"type": "text", "wrap": True, "size": "xs", "color": "#8a96a0",
                     "text": "也可以直接打「午餐 120」。長按這張卡 → 設為公告，就會釘在最上面。"},
                    *rows,
                ],
            },
        },
    }


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
