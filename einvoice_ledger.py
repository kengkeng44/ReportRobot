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
import re
import unicodedata
from datetime import date, timedelta

import einvoice_pantry

NEW_ROWS_FROM = date(2026, 9, 1)
SOURCE = "載具發票"
UNSURE = "❓"
_DAY_SLACK = 1

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


def same_brand(seller, shop):
    """發票賣方跟刷卡店名是不是同一家。寧可說「不確定」也不要亂配。"""
    if not seller or not shop:
        return False
    up_shop = _wide(shop)
    for key, names in _ALIASES.items():
        if key in seller and any(n in up_shop for n in names):
            return True
    a, b = _norm(seller), _norm(shop)
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
    }


def plan(invoices, txns):
    """回傳要做的事：[(動作, 發票, 交易或 None)]，動作是 skip/attach/unsure/new/old。

    純函式，不碰 Notion —— dry-run 跟測試都靠它。
    """
    known = {t.get("invoice_no") for t in txns if t.get("invoice_no")}
    free = [t for t in txns
            if not t.get("invoice_no") and (t.get("direction") or "支出") == "支出"
            and _as_date(t.get("date"))]
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

    def near(day):
        return [t for t in free if t["page_id"] not in taken
                and abs((_as_date(t["date"]) - day).days) <= _DAY_SLACK]

    def closest(cands, day):
        return min(cands, key=lambda t: abs((_as_date(t["date"]) - day).days))

    def total(t):
        v = t.get("total") if t.get("total") is not None else t.get("amount")
        try:
            return round(float(v or 0))
        except (TypeError, ValueError):
            return None

    # 第一輪：金額 + 日期 + 品牌全對
    for num, day, inv in todo:
        hits = [t for t in near(day) if total(t) == _amount(inv)
                and same_brand(inv.get("seller"), t.get("shop"))]
        if hits:
            t = closest(hits, day)
            taken.add(t["page_id"])
            actions[num] = ("attach", inv, t)

    # 第二輪：只有一半像的
    for num, day, inv in todo:
        if num in actions:
            continue
        cands = near(day)
        half = ([t for t in cands if total(t) == _amount(inv)]
                or [t for t in cands if same_brand(inv.get("seller"), t.get("shop"))])
        if half:
            t = closest(half, day)
            taken.add(t["page_id"])
            actions[num] = ("unsure", inv, t)
        elif day >= NEW_ROWS_FROM:
            actions[num] = ("new", inv, None)
        else:
            actions[num] = ("old", inv, None)

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
        if kind == "attach":
            ok = notion.transaction_attach_invoice(t["page_id"], inv["inv_num"], items_text(inv))
        elif kind == "unsure":
            ok = notion.transaction_attach_invoice(
                t["page_id"], inv["inv_num"], items_text(inv, unsure=True))
        elif kind == "new":
            ok = bool(notion.transaction_add(new_txn(inv)))
        else:
            done.append((kind, inv, t))
            continue
        if ok:
            done.append((kind, inv, t))
        time.sleep(0.4)          # Notion 每秒約 3 次，見 sources._PACE
    return summary(done)


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
