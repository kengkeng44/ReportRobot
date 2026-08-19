"""財務分頁的內容產生：本月支出 / 最近交易 / 卡費 / 淨值 / 手動記一筆。

純邏輯，不碰 Notion 也不碰 LINE，方便測試也方便被排程與指令共用。

原則：沒有資料時要講清楚「為什麼沒有」跟「怎麼開始」。
只回一句「無資料」會讓人分不出是壞了還是本來就空的。
"""

import hashlib
import re
from collections import defaultdict
from datetime import date


# 這些字出現在描述裡就當收入。記成支出會讓月結完全失真。
_INCOME_HINTS = ("薪水", "薪資", "獎金", "退款", "退費", "股利", "配息", "利息", "收入")

_EMPTY_MONTH = (
    "這個月還沒有支出紀錄。\n"
    "信用卡消費會在每天 15:30 自動同步；也可以按「記一筆」手動加。"
)
_EMPTY_RECENT = (
    "還沒有任何交易紀錄。\n"
    "信用卡消費每天 15:30 自動同步,或按「記一筆」手動加。"
)
_EMPTY_CARD = (
    "還沒有帳單資料。\n"
    "信用卡月帳單的解析還沒接上,目前只會自動抓每日消費明細。"
)
_EMPTY_NET = (
    "還沒有淨值紀錄。\n"
    "需要先有持倉與帳戶餘額,淨值快照才會開始累積。"
)


def _money(n):
    """3,450 這樣的千分位。整數就不顯示小數。"""
    if n is None:
        return "-"
    if float(n) == int(n):
        return f"{int(n):,}"
    return f"{n:,.2f}"


def _is_spending(txn):
    return (txn.get("direction") or "支出") == "支出"


def _currency(txn):
    """遷移前的資料沒有幣別欄，當時只有國泰一個來源，一律台幣。"""
    return txn.get("currency") or "TWD"


def _is_overseas(txn):
    """非台灣的消費。國泰標的是授權當下的台幣估算，結匯後金額會變。"""
    region = txn.get("region") or ""
    return bool(region) and region != "TW"


# ─────────────────────────────────────────────────────────
# 本月支出
# ─────────────────────────────────────────────────────────

def format_monthly_spending(txns, month):
    """month 格式 YYYY-MM。只算支出，收入與還款排除。"""
    rows = [t for t in txns
            if (t.get("date") or "").startswith(month) and _is_spending(t)]
    if not rows:
        return _EMPTY_MONTH

    # 不同幣別分開算。把 US$30 加進台幣總計會得到一個沒有意義的數字，
    # 而且畫面上看不出來哪裡怪 —— 目前資料都是台幣，這是給未來的保險。
    twd_rows = [t for t in rows if _currency(t) == "TWD"]
    other_rows = [t for t in rows if _currency(t) != "TWD"]

    by_cat = defaultdict(float)
    for t in twd_rows:
        by_cat[t.get("category") or "其他"] += t.get("amount") or 0
    total = sum(by_cat.values())

    lines = [f"💳 {month} 支出　NT${_money(total)}", f"　共 {len(twd_rows)} 筆", ""]
    for cat, amt in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        pct = round(amt / total * 100) if total else 0
        lines.append(f"・{cat}　NT${_money(amt)}　{pct}%")

    by_ccy = defaultdict(float)
    for t in other_rows:
        by_ccy[_currency(t)] += t.get("amount") or 0
    for ccy, amt in sorted(by_ccy.items()):
        lines.append(f"・（{ccy}）　{_money(amt)}")

    # 海外消費的金額還會因結匯變動，總額看起來精確其實不是
    overseas = [t for t in twd_rows if _is_overseas(t)]
    if overseas:
        amt = sum(t.get("amount") or 0 for t in overseas)
        lines.append("")
        lines.append(f"🌐 含海外 {len(overseas)} 筆 NT${_money(amt)}（結匯後會變動）")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# 最近交易
# ─────────────────────────────────────────────────────────

def format_recent(txns, limit=10):
    if not txns:
        return _EMPTY_RECENT

    rows = sorted(txns, key=lambda t: t.get("date") or "", reverse=True)[:limit]
    lines = [f"🧾 最近 {len(rows)} 筆"]
    for t in rows:
        day = (t.get("date") or "")[5:]           # MM-DD
        name = t.get("shop") or t.get("category") or "消費"
        # 授權中代表金額還可能變（外幣結匯、退款），要讓人看得出來
        mark = "（授權）" if t.get("status") == "授權中" else ""
        # 海外那筆的台幣金額是估算，標出來才知道帳單來的時候可能對不上
        area = f"🌐{t.get('region')}" if _is_overseas(t) else ""
        ccy = _currency(t)
        unit = "NT$" if ccy == "TWD" else f"{ccy} "
        lines.append(f"・{day}　{name}　{unit}{_money(t.get('amount'))}{area}{mark}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# 卡費
# ─────────────────────────────────────────────────────────

def format_card_bills(bills):
    if not bills:
        return _EMPTY_CARD

    lines = ["💳 信用卡帳單"]
    for b in sorted(bills, key=lambda x: x.get("period") or "", reverse=True)[:3]:
        lines.append(
            f"・{b.get('period')}　應繳 NT${_money(b.get('amount'))}"
            f"　繳款截止 {b.get('due') or '-'}"
        )
        if b.get("minimum") is not None:
            lines.append(f"　　最低應繳 NT${_money(b['minimum'])}　{b.get('status') or ''}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# 淨值
# ─────────────────────────────────────────────────────────

def format_net_worth(snapshots):
    if not snapshots:
        return _EMPTY_NET

    rows = sorted(snapshots, key=lambda s: s.get("date") or "")
    latest = rows[-1]
    lines = [
        f"📈 淨值　NT${_money(latest.get('net'))}",
        f"　{latest.get('date')}",
        "",
        f"・現金　NT${_money(latest.get('cash'))}",
        f"・股票市值　NT${_money(latest.get('stock'))}",
        f"・信用卡未繳　NT${_money(latest.get('card_due'))}",
    ]
    if len(rows) >= 2:
        diff = (latest.get("net") or 0) - (rows[-2].get("net") or 0)
        arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "－")
        lines.append("")
        lines.append(f"　較前次 {arrow} NT${_money(abs(diff))}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# 手動記一筆
# ─────────────────────────────────────────────────────────

_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|塊)?")

# 認得的餐飲品項。用子字串比對而非完全相等：手打時常是「跟同事吃午餐」
# 這種句子，只認兩個字的話大多數手打紀錄都會落到「其他」。
# 已知取捨：「咖啡機」會被判成餐飲。發生率遠低於前者，接受。
_FOOD_HINTS = ("早餐", "午餐", "晚餐", "咖啡", "飲料", "點心", "宵夜", "下午茶")


def guess_category(shop):
    """品項 → 消費類別。認不出來回「其他」，不自創類別。

    類別沿用國泰帳單自帶分類（notion_db._SPEND_CATEGORIES）。國泰沒有
    「交通」，所以「搭車」記成「其他」—— 增生分類會讓 Notion 長出
    兩套命名系統，之後兩邊都對不起來。
    """
    name = (shop or "").strip()
    return "餐飲" if any(k in name for k in _FOOD_HINTS) else "其他"


def make_manual_fingerprint(day, amount, shop):
    raw = f"手動|{day}|{amount}|{shop}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def parse_manual(text, today=None):
    """「午餐 120」或「120 午餐」→ 交易 dict。沒有金額就回 None。

    沒金額不猜 —— 記一筆金額錯的帳，比沒記更難發現也更難修。
    """
    if not text:
        return None
    cleaned = text.strip()

    m = _AMOUNT_RE.search(cleaned)
    if not m:
        return None
    amount = float(m.group(1))
    amount = int(amount) if amount == int(amount) else amount

    shop = (cleaned[:m.start()] + " " + cleaned[m.end():]).strip()
    shop = re.sub(r"\s+", " ", shop)
    if not shop:
        shop = "未命名"

    day = (today or date.today()).isoformat()
    direction = "收入" if any(k in shop for k in _INCOME_HINTS) else "支出"

    return {
        "date": day,
        "amount": amount,
        "shop": shop,
        "category": guess_category(shop),
        "direction": direction,
        "status": "已結帳",          # 手動輸入就是最終金額，不需要對帳
        "source": "手動",
        "fingerprint": make_manual_fingerprint(day, amount, shop),
    }


# ─────────────────────────────────────────────────────────
# 記帳 Quick Reply 的按鈕內容
#
# 兩段式：先跳品項、再跳該品項的金額。純邏輯，不碰 Notion 也不碰 LINE。
# ─────────────────────────────────────────────────────────

# 沒有記帳歷史時的預設按鈕。用一陣子後會被真實習慣取代。
_DEFAULT_ITEMS = ["午餐", "晚餐", "早餐", "咖啡", "飲料", "點心"]


def frequent_expense_items(txns, limit=6, pad=True):
    """常記品項：手動記過越多次的排前面。

    只看 source == "手動"。交易明細裡混著信用卡自動同步的資料，商店名
    長這樣「全聯福利中心－板橋板新」—— 放到按鈕上沒有意義，而且 LINE 的
    label 上限 20 字會把它截成半截店名。

    同次數保持第一次出現的順序：每次跳出來的按鈕位置都在動，比排序不準
    更難用。（與 kitchen.frequent_items 同一套理由，過濾條件不同故各自實作。）

    pad=True 時用 _DEFAULT_ITEMS 補到 limit：沒歷史就給空按鈕列，
    等於這個功能第一天不存在。
    """
    counts = {}
    order = []
    for t in txns or []:
        if (t.get("source") or "") != "手動":
            continue
        name = (t.get("shop") or "").strip()
        if not name:
            continue
        if name not in counts:
            order.append(name)
        counts[name] = counts.get(name, 0) + 1

    ranked = sorted(order, key=lambda n: (-counts[n], order.index(n)))
    out = ranked[:limit]

    if pad:
        for name in _DEFAULT_ITEMS:
            if len(out) >= limit:
                break
            if name not in counts:
                out.append(name)
    return out


# 種子金額：該品項還沒有歷史時的按鈕。記過幾次之後就由真實資料接手。
# 沒列在這裡的品項（使用者自己打的「搭車」）回空 list，呼叫端只給文字提示。
_SEED_AMOUNTS = {
    "早餐": [50, 60, 80],
    "午餐": [100, 120, 150],
    "晚餐": [120, 150, 200],
    "咖啡": [55, 65, 85],
    "飲料": [35, 50, 60],
    "點心": [40, 50, 80],
}


def frequent_amounts(txns, item, limit=5, pad=True):
    """某個品項的常用金額，記過越多次的排前面。

    依品項分別統計：共用一份全域金額清單會讓咖啡的按鈕上出現 200 元。
    與 frequent_expense_items 一樣只看 source == "手動"。

    整數金額回 int，否則按鈕上會出現「120.0」。
    """
    key = (item or "").strip()
    if not key:
        return []

    counts = {}
    order = []
    for t in txns or []:
        if (t.get("source") or "") != "手動":
            continue
        if (t.get("shop") or "").strip() != key:
            continue
        amount = t.get("amount")
        if amount is None:
            continue
        amount = int(amount) if float(amount) == int(amount) else amount
        if amount not in counts:
            order.append(amount)
        counts[amount] = counts.get(amount, 0) + 1

    ranked = sorted(order, key=lambda a: (-counts[a], order.index(a)))
    out = ranked[:limit]

    if pad:
        for amount in _SEED_AMOUNTS.get(key, []):
            if len(out) >= limit:
                break
            if amount not in counts:
                out.append(amount)
    return out


# ─────────────────────────────────────────────────────────
# 最近一天消費（每日推播用）
# ─────────────────────────────────────────────────────────

_WEEKDAY_ZH = "一二三四五六日"
_STALE_HINT = "可能是沒刷卡,也可能是同步中斷"


def _to_date(value):
    """'2026-08-12' 或 '2026-08-12T00:00' → date。壞資料回 None。"""
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def format_latest_day_spending(txns, today, stale_days=3, max_rows=5):
    """資料裡最新一天的支出明細 + 本月累計。沒有任何支出就回 None。

    刻意不是「昨天」：國泰消費彙整信每天彙整前一日，今天早上推播時昨天的
    資料還沒進 Notion（見 spec 第 2 節）。硬寫「昨日」會每天都是空的。

    沒資料時回 None 而不是說明文案 —— 這是每天自動來的推播，不是使用者
    主動按按鈕查詢。剛啟用時天天跳「還沒有紀錄」會讓人略過整則推播。
    """
    rows = []
    for t in txns or []:
        if not _is_spending(t):
            continue
        day = _to_date(t.get("date"))
        if day is None or day > today:
            continue
        rows.append((day, t))

    if not rows:
        return None

    latest = max(day for day, _ in rows)
    day_rows = [t for day, t in rows if day == latest]
    total = sum(t.get("amount") or 0 for t in day_rows)

    head = f"{latest.month}/{latest.day:02d}（{_WEEKDAY_ZH[latest.weekday()]}）"
    lines = [f"{head}　NT${_money(total)}　{len(day_rows)} 筆", ""]

    ordered = sorted(day_rows, key=lambda t: t.get("amount") or 0, reverse=True)
    for t in ordered[:max_rows]:
        name = t.get("shop") or t.get("category") or "消費"
        lines.append(f"・{name}　NT${_money(t.get('amount'))}")

    if len(ordered) > max_rows:
        lines.append(f"　…另 {len(ordered) - max_rows} 筆")

    # 同步默默壞掉時，畫面會停在舊資料卻長得很正常 —— 要講出來
    stale = (today - latest).days
    if stale > stale_days:
        lines.append("")
        lines.append(f"⚠️ 已 {stale} 天沒新消費資料")
        lines.append(f"　{_STALE_HINT}")

    month = today.strftime("%Y-%m")
    month_total = sum(
        t.get("amount") or 0 for day, t in rows if day.strftime("%Y-%m") == month
    )

    lines.append("")
    lines.append(f"本月累計 NT${_money(month_total)}")

    return "\n".join(lines)
