"""載具發票 → 交易明細：只「補明細」，不重複記帳。

2026-10-12 使用者確認的規則
──────────────────────
同一筆消費常常兩邊都有：國泰通知信記了「統一超商－貿捷 55」，載具發票也有
「統一超商股份有限公司 55」。店名寫法不同，指紋對不上，直接新增就會記兩次。
所以載具的角色是**補明細**：

  1. 發票號碼已經在交易明細裡 → 跳過（同一份 CSV 匯幾次都一樣）
  2. 金額相同 + 日期差 ≤ 1 天 + 店名品牌對得上 → 同一筆，把發票號碼與品項
     補進那筆刷卡，不新增
  3. 只有一半像（金額對、店名對不上；或店名對、金額不同）→ 一樣補上，
     但明細前面標 ❓，讓人在 Notion 自己判斷 —— 不猜
  4. 都找不到 → 付現或沒追蹤的卡，才新增一筆（來源「🧾 載具發票」）

第 4 條只對 NEW_ROWS_FROM 之後的發票生效：彙整通知一次帶整個月、Gmail 也會
撈到舊信，補明細無害，但把好幾個月前的現金消費突然塞進帳本，舊月份的報表
會無聲改變。

比對分兩輪：先讓所有「完全對上」的發票認領刷卡，再處理一半像的 ——
否則一張只有半像的發票可能先搶走另一張本來完全對得上的刷卡。
"""

import hashlib
import json
import re
import unicodedata
from datetime import date, timedelta

import einvoice_pantry

NEW_ROWS_FROM = date(2026, 9, 1)
SOURCE = "載具發票"
UNSURE = "❓"
_DAY_SLACK = 1
_SMALL_DIFF = 5

# 公司登記名跟刷卡店名對不起來的連鎖店：發票上的關鍵字 → 刷卡可能出現的寫法
_ALIASES = {
    "統一超商": ("7-ELEVEN", "7-11", "7ELEVEN", "統一超商"),
    "全家便利": ("全家", "FAMILYMART", "FAMILY"),
    "家福": ("家樂福", "CARREFOUR"),
    "萊爾富": ("萊爾富", "HILIFE"),
    "來來超商": ("OK", "來來"),
    "好市多": ("好市多", "COSTCO"),
    "星巴克": ("星巴克", "STARBUCKS"),
    "麥當勞": ("麥當勞", "MCDONALD"),
    "全聯": ("全聯", "PXMART", "PX MART"),
    "康是美": ("康是美", "COSMED"),
    "屈臣氏": ("屈臣氏", "WATSONS"),
    "寶雅": ("寶雅", "POYA"),
    # 以下是 2026-10-12 拿真實發票比對時抓到的
    "和德昌": ("麥當勞", "MCDONALD"),
    "酷澎": ("COUPANG", "酷澎"),
    "冠誠": ("GLOBALMALL", "GLOBAL MALL", "環球"),
    "台北金融大樓": ("101",),
    "宜家": ("IKEA", "宜家"),
}

# 公司名裡太常見、不能拿來判斷是同一家的字
_NOISE = ("股份有限公司", "有限公司", "分公司", "企業社", "商行", "公司",
          "台灣", "臺灣", "國際", "企業", "實業", "餐飲", "食品", "百貨",
          "商店", "門市", "分店", "總店")


def _wide(text):
    """全形轉半形（國泰的店名常是「ＧｌｏｂａｌＭａｌｌ」「１０１」）。"""
    return unicodedata.normalize("NFKC", text or "").upper()


def _norm(text):
    s = _wide(text)
    for w in _NOISE:
        s = s.replace(w, "")
    return re.sub(r"[\s\-－_()（）·・.,，]", "", s)


def _brand_part(seller):
    """公司登記名去掉「股份有限公司」之後的分公司／門市段。"""
    s = _wide(seller)
    for cut in ("股份有限公司", "有限公司"):
        if cut in s:
            head = s.split(cut)[0]
            return head or s
    return s


def same_brand(seller, shop):
    """發票賣方跟刷卡店名是不是同一家。寧可說「不確定」也不要亂配。"""
    if not seller or not shop:
        return False
    up_shop = _wide(shop)
    for key, names in _ALIASES.items():
        if key in seller and any(n in up_shop for n in names):
            return True
    # 只比品牌，不比分店：「遠東百貨…板橋新站分公司」跟「全家便利商店－板橋廣榮店」
    # 都有「板橋」，2026-10-27 就這樣被配成同一家
    a, b = _norm(_brand_part(seller)), _norm(_wide(shop).split("－")[0])
    if not a or not b:
        return False
    # 共用任何兩個連續字就算同一家（全聯福利中心 / 全聯實業 → 「全聯」）
    grams = {a[i:i + 2] for i in range(len(a) - 1)}
    return any(b[i:i + 2] in grams for i in range(len(b) - 1))


def items_text(inv, unsure=False):
    """品項 → 「鮮奶 x2、雞胸肉、高麗菜」。折扣、店家代碼不列。"""
    parts = []
    for it in inv.get("items") or []:
        name = (it.get("name") or "").strip()
        if not name or name.isdigit():
            continue
        if any(k in name for k in einvoice_pantry._NON_ITEM_KEYWORDS):
            continue
        qty = it.get("qty")
        if qty and qty > 1:
            name += f" x{int(qty) if float(qty).is_integer() else qty}"
        parts.append(name)
    text = "、".join(parts)
    if unsure:
        text = f"{UNSURE}可能是 {inv.get('seller') or ''} 的發票：{text}"
    return text[:1900]          # Notion 單一文字欄上限 2000 字


def _as_date(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _amount(inv):
    try:
        return round(float(inv.get("amount") or 0))
    except (TypeError, ValueError):
        return 0


def _fingerprint(inv_num):
    return hashlib.sha256(f"einvoice|{inv_num}".encode("utf-8")).hexdigest()[:32]


def new_txn(inv):
    """找不到刷卡的發票 → transaction_add 用的 dict。"""
    day = _as_date(inv.get("date"))
    seller = (inv.get("seller") or "").strip()
    return {
        "date": day.isoformat(),
        "amount": _amount(inv),
        "direction": "支出",
        "shop": seller,
        "status": "已結帳",
        "source": SOURCE,
        "fingerprint": _fingerprint(inv["inv_num"]),
        "invoice_no": inv["inv_num"],
        "items": items_text(inv),
        "invoice_data": pack(inv),
    }


# ── 發票資料：存在 Notion 那筆帳上，人工確認時才拿得回原始發票 ──────

def pack(inv, skip=()):
    """發票 → 一小段 JSON（「發票資料」欄）。品項太長就砍尾，Notion 單欄上限 2000 字。"""
    d = {"n": inv["inv_num"], "d": _as_date(inv.get("date")).isoformat(),
         "a": _amount(inv), "s": inv.get("seller") or "",
         "i": [[it.get("name"), it.get("qty")] for it in inv.get("items") or []],
         "x": list(skip)}
    text = json.dumps(d, ensure_ascii=False)
    while len(text) > 1900 and d["i"]:
        d["i"].pop()
        text = json.dumps(d, ensure_ascii=False)
    return text


def unpack(text):
    """「發票資料」→ (發票 dict, 已經被否決的帳 page_id)。壞掉回 (None, [])。"""
    try:
        d = json.loads(text or "")
        inv = {"inv_num": d["n"], "date": _as_date(d["d"]), "amount": d["a"],
               "seller": d.get("s") or "",
               "items": [{"name": n, "qty": q} for n, q in d.get("i") or []]}
        return inv, list(d.get("x") or [])
    except (ValueError, KeyError, TypeError):
        return None, []


# ── 比對 ─────────────────────────────────────────────────

def _total(t):
    v = t.get("total") if t.get("total") is not None else t.get("amount")
    try:
        return round(float(v or 0))
    except (TypeError, ValueError):
        return None


def _gap(inv, t):
    return abs((_as_date(t["date"]) - _as_date(inv.get("date"))).days)


def tier(inv, t):
    """這筆帳跟發票像到什麼程度。數字越小越像，None = 不是候選。

    0 金額 + 日期 + 店家全對          → 直接算是
    1 金額一樣、同一天（店名對不上）  → 也算是（2026-10-12 使用者決定）
      店家一樣、金額只差 ≤ 5 元且 ≤ 3% → 也算是（刷卡常比發票多購物袋這類零頭，
                                        2026-10-27 四筆 ❓ 全是差 2～5 元）
    2 金額一樣、差一天                → ❓
    3 店家一樣、金額不同              → ❓
    """
    if _gap(inv, t) > _DAY_SLACK:
        return None
    total = _total(t)
    same_amount = total == _amount(inv)
    brand = same_brand(inv.get("seller"), t.get("shop"))
    if same_amount and brand:
        return 0
    diff = abs(total - _amount(inv)) if total is not None else None
    if brand and diff is not None and diff <= _SMALL_DIFF and diff <= _amount(inv) * 0.03:
        return 1
    if same_amount:
        return 1 if _gap(inv, t) == 0 else 2
    if brand:
        return 3
    return None


SURE_TIERS = (0, 1)


def _free(txns):
    return [t for t in txns
            if not t.get("invoice_no") and (t.get("direction") or "支出") == "支出"
            and _as_date(t.get("date")) and t.get("page_id")]


def best(inv, txns, skip=()):
    """還沒掛發票的帳裡最像的那筆 → (tier, 帳)；沒有回 (None, None)。"""
    ranked = [(tier(inv, t), _gap(inv, t), t) for t in _free(txns)
              if t["page_id"] not in skip]
    ranked = [r for r in ranked if r[0] is not None]
    if not ranked:
        return None, None
    k, _, t = min(ranked, key=lambda r: (r[0], r[1]))
    return k, t


def plan(invoices, txns):
    """回傳要做的事：[(動作, 發票, 交易或 None)]，動作是 skip/attach/unsure/new/old。

    純函式，不碰 Notion —— dry-run 跟測試都靠它。
    依 tier 一輪一輪認領：越像的先挑，免得一張半像的發票先搶走
    另一張完全對得上的刷卡。
    """
    known = {t.get("invoice_no") for t in txns if t.get("invoice_no")}
    free = _free(txns)
    taken = set()
    actions = {}

    todo = []
    for inv in invoices or []:
        num = (inv.get("inv_num") or "").strip()
        day = _as_date(inv.get("date"))
        if not num or day is None or _amount(inv) <= 0:
            continue
        if "作廢" in (inv.get("status") or ""):
            continue
        if num in known:
            actions[num] = ("skip", inv, None)
            continue
        todo.append((num, day, inv))

    for level in (0, 1, 2, 3):
        for num, day, inv in todo:
            if num in actions:
                continue
            hits = [t for t in free if t["page_id"] not in taken and tier(inv, t) == level]
            if hits:
                t = min(hits, key=lambda t: _gap(inv, t))
                taken.add(t["page_id"])
                actions[num] = ("attach" if level in SURE_TIERS else "unsure", inv, t)

    for num, day, inv in todo:
        if num not in actions:
            actions[num] = ("new" if day >= NEW_ROWS_FROM else "old", inv, None)

    return list(actions.values())


def summary(actions):
    n = {k: 0 for k in ("attach", "unsure", "new", "skip", "old")}
    for kind, _, _ in actions:
        n[kind] += 1
    return (f"補明細 {n['attach']}、待確認 {n['unsure']}、新增 {n['new']}、"
            f"已處理過 {n['skip']}、太舊略過 {n['old']}")


def apply(actions, notion=None):
    """照 plan 寫進 Notion。單筆失敗不擋整批。回 summary 字串。"""
    if notion is None:
        import notion_db as notion
    import time

    done = []
    for kind, inv, t in actions:
        ok = True
        if kind in ("attach", "unsure"):
            ok = notion.transaction_attach_invoice(
                t["page_id"], inv["inv_num"], items_text(inv, unsure=kind == "unsure"),
                pack(inv))
        elif kind == "new":
            ok = bool(notion.transaction_add(new_txn(inv)))
        else:
            done.append((kind, inv, t))
            continue
        if ok:
            done.append((kind, inv, t))
        time.sleep(0.4)          # Notion 每秒約 3 次，見 sources._PACE
    return summary(done)


# ── 人工確認：Notion「發票處理」下拉選單 ──────────────────────

OK = "✅ 正確"
NEXT = "➡️ 換下一筆"
CASH = "➕ 沒刷卡，新增一筆"


def review(notion=None):
    """處理你在 Notion 選了「發票處理」的帳。排程每 5 分鐘跑。回處理筆數。

    ✅ 正確       → 拿掉 ❓
    ➡️ 換下一筆   → 這筆不是，發票改掛到下一個最像的帳（還是 ❓）；
                   一個都沒有了就新增一筆
    ➕ 沒刷卡     → 這筆不是，發票自己新增一筆消費
    選完會把選單清空，所以同一筆不會被處理兩次。
    """
    if notion is None:
        import notion_db as notion
    done = 0
    for row in notion.transactions_invoice_review():
        pid, choice = row["page_id"], row.get("invoice_action")
        if not choice and row.get("invoice_ok"):
            choice = OK                                  # 勾「發票正確」= 選「✅ 正確」
        inv, skip = unpack(row.get("invoice_data"))
        if inv is None:
            print(f"[einvoice] {pid} 沒有發票資料，無法處理「{choice}」")
            notion.transaction_clear_invoice_action(pid)
            continue
        own_row = row.get("source") == SOURCE        # 本來就是發票自己新增的那筆

        if choice == OK:
            notion.transaction_attach_invoice(pid, inv["inv_num"], items_text(inv),
                                              pack(inv, skip), clear_action=True)
        elif choice == NEXT:
            skip = skip + [pid]
            _detach(notion, pid, own_row)
            txns = notion.transactions_load(
                limit=500, since=(inv["date"] - timedelta(days=_DAY_SLACK)).isoformat(),
                until=(inv["date"] + timedelta(days=_DAY_SLACK)).isoformat())
            _, t = best(inv, txns, skip)
            if t:
                notion.transaction_attach_invoice(
                    t["page_id"], inv["inv_num"], items_text(inv, unsure=True), pack(inv, skip))
            else:
                notion.transaction_add(new_txn(inv))
        elif choice == CASH:
            if own_row:
                notion.transaction_attach_invoice(pid, inv["inv_num"], items_text(inv),
                                                  pack(inv, skip), clear_action=True)
            else:
                _detach(notion, pid, own_row)
                notion.transaction_add(new_txn(inv))
        else:
            notion.transaction_clear_invoice_action(pid)       # 不認得的選項
            continue
        done += 1
    return done


def _detach(notion, pid, own_row):
    """把發票從這筆帳拿掉。發票自己新增的那筆直接刪掉（垃圾桶 30 天內救得回來）。"""
    if own_row:
        notion.transaction_delete(pid)
    else:
        notion.transaction_attach_invoice(pid, None, None, None, clear_action=True)


def load_txns(invoices, notion=None):
    """撈發票期間（前後多抓 2 天）的交易明細，給 plan 比對。"""
    if notion is None:
        import notion_db as notion
    days = [d for d in (_as_date(i.get("date")) for i in invoices or []) if d]
    if not days:
        return []
    since = (min(days) - timedelta(days=_DAY_SLACK + 1)).isoformat()
    return notion.transactions_load(limit=3000, since=since)


def sync(invoices, notion=None):
    """發票清單 → 交易明細。回 summary 字串。"""
    if not invoices:
        return summary([])
    return apply(plan(invoices, load_txns(invoices, notion)), notion)


# ── 重複待確認 ──────────────────────────────────────────

def find_duplicates(txns):
    """同一張發票掛在兩列以上 → {發票號碼: [那幾列]}。"""
    groups = {}
    for t in txns:
        num = (t.get("invoice_no") or "").strip()
        if num and t.get("page_id"):
            groups.setdefault(num, []).append(t)
    return {num: rows for num, rows in groups.items() if len(rows) >= 2}


def dedup(notion=None, dry_run=False):
    """重複的列勾「重複待確認」，交給你在 Notion「🔁 重複待確認」刪掉其中一列。

    不自動刪、不併資料：哪列該留只有你知道。刪掉一列後，剩下那列的勾下次跑會拿掉。
    回報告文字（只列這次新勾的，勾過的不重複推），沒事回 ""。
    """
    if notion is None:
        import notion_db as notion
    txns = notion.transactions_load(limit=5000)
    dups = find_duplicates(txns)
    dup_ids = {r["page_id"] for rows in dups.values() for r in rows}
    lines = []
    for num, rows in dups.items():
        fresh = [r for r in rows if not r.get("dup_flag")]
        if not fresh:
            continue
        if not dry_run:
            for r in fresh:
                notion.transaction_set_duplicate(r["page_id"], True)
        desc = "／".join(f"{r.get('date')} {r.get('shop') or ''} {r.get('amount')}" for r in rows)
        lines.append(f"・{num}：{desc}")
    if not dry_run:
        for t in txns:
            if t.get("dup_flag") and t.get("page_id") not in dup_ids:
                notion.transaction_set_duplicate(t["page_id"], False)
    if not lines:
        return ""
    head = ("🔁 交易明細有重複，到 Notion「🔁 重複待確認」刪掉其中一列就好"
            + ("（試跑，沒動資料）" if dry_run else ""))
    return "\n".join([head] + lines)
