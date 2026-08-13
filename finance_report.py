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


# ─────────────────────────────────────────────────────────
# 本月支出
# ─────────────────────────────────────────────────────────

def format_monthly_spending(txns, month):
    """month 格式 YYYY-MM。只算支出，收入與還款排除。"""
    rows = [t for t in txns
            if (t.get("date") or "").startswith(month) and _is_spending(t)]
    if not rows:
        return _EMPTY_MONTH

    by_cat = defaultdict(float)
    for t in rows:
        by_cat[t.get("category") or "其他"] += t.get("amount") or 0
    total = sum(by_cat.values())

    lines = [f"💳 {month} 支出　NT${_money(total)}", f"　共 {len(rows)} 筆", ""]
    for cat, amt in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        pct = round(amt / total * 100) if total else 0
        lines.append(f"・{cat}　NT${_money(amt)}　{pct}%")
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
        lines.append(f"・{day}　{name}　NT${_money(t.get('amount'))}{mark}")
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
        "category": "其他",
        "direction": direction,
        "status": "已結帳",          # 手動輸入就是最終金額，不需要對帳
        "source": "手動",
        "fingerprint": make_manual_fingerprint(day, amount, shop),
    }


# ─────────────────────────────────────────────────────────
# 最近一天消費（每日推播用）
# ─────────────────────────────────────────────────────────

_WEEKDAY_ZH = "一二三四五六日"


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

    return "\n".join(lines)
