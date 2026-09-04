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

    # 「金額」欄已經是我實際負擔，這行是為了看得到整桌花多少。
    # 沒有共同消費的月份不印 —— 常態是零的欄位每個月都佔一行，
    # 會讓人不再讀它。
    shared = [t for t in twd_rows if (t.get("split_type") or "個人") == "共同"]
    if shared:
        mine = sum(t.get("amount") or 0 for t in shared)
        gross = sum((t.get("total") if t.get("total") is not None
                     else t.get("amount")) or 0 for t in shared)
        lines.insert(2, f"　其中共同分攤 NT${_money(mine)}"
                        f"（原始 NT${_money(gross)}）")

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


def format_monthly_detail(txns, month):
    """month 格式 YYYY-MM。當月每一筆花銷逐日列出。

    既有的 format_monthly_spending 只給分類統計，看不到單筆花在哪 ——
    使用者要在每日信裡看到整個月的明細（2026-08-26）。

    日期由新到舊：信是每天早上看的，最近的花費要先看到，舊的往下捲。
    外幣不併進台幣總計 —— 把 US$15 加進台幣會得到一個沒有意義的數字，
    而且畫面上看不出來哪裡怪。
    """
    rows = [t for t in txns
            if (t.get("date") or "").startswith(month) and _is_spending(t)]
    if not rows:
        return _EMPTY_MONTH

    by_day = defaultdict(list)
    for t in rows:
        by_day[t.get("date")].append(t)

    lines = []
    twd_total = 0.0
    other_total = defaultdict(float)
    for day in sorted(by_day, reverse=True):
        lines.append(f"■ {day[5:].replace('-', '/')}")
        day_total = 0.0
        for t in by_day[day]:
            amount = t.get("amount") or 0
            currency = _currency(t)
            shop = t.get("shop") or "（未填商家）"
            if currency == "TWD":
                lines.append(f"　・{shop}　NT${_money(amount)}")
                day_total += amount
                twd_total += amount
            else:
                lines.append(f"　・{shop}　{currency} {_money(amount)}")
                other_total[currency] += amount
        if day_total:
            lines.append(f"　小計 NT${_money(day_total)}")
        lines.append("")

    lines.append(f"本月合計：NT${_money(twd_total)}（共 {len(rows)} 筆）")
    for currency, amount in sorted(other_total.items()):
        lines.append(f"　＋ {currency} {_money(amount)}（外幣另計）")
    return "\n".join(lines)


def month_spending_total(txns, month):
    """當月台幣支出總額（純數字，給每日信摘要列用）。

    只算台幣：外幣併進台幣總計會得到一個沒有意義的數字（跟
    format_monthly_detail 同一個理由）。沒有支出回 0，呼叫端據此決定
    要不要放這格摘要 —— 0 元不放，避免「本月 NT$0」的雜訊。
    """
    return sum(
        t.get("amount") or 0
        for t in txns or []
        if (t.get("date") or "").startswith(month)
        and _is_spending(t)
        and _currency(t) == "TWD"
    )


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


# ── 共同消費分攤 ─────────────────────────────────────────
# 共同消費裡我負擔的比例。寫成常數而不是散落各處的 / 2 ——
# 這是一個政策決定，不是數學。散落的 / 2 讀起來像數學，改的時候會漏。
MY_SHARE = 0.5


def my_share_of(total):
    """共同消費裡我負擔多少。四捨五入到整數 —— 台幣沒有小數。

    不用內建 round()：那是 banker's rounding，round(302.5) 得 302 而
    round(303.5) 得 304，同樣是 .5 卻一個往下一個往上。共同消費除以 2
    在金額為奇數時大量產生 .5，忽上忽下對帳時查不出規律。
    """
    return int((total or 0) * MY_SHARE + 0.5)


# 這些店的消費一律算共同 —— 買回來的東西兩個人一起吃、一起用。
# 用子字串比對而非完全相等：國泰給的店名是「全聯福利中心－板橋板新」，
# 分店會變但「全聯」不會。要加別的店（量販、超市）改這一行就好。
_SHARED_SHOPS = ("全聯",)


def is_shared_shop(shop):
    """這家店的消費算不算共同。"""
    name = (shop or "").strip()
    return any(k in name for k in _SHARED_SHOPS)


def apply_shared_rule(txn):
    """自動同步進來的交易，商店在共同清單裡就改記成共同並只留我那半。

    沒有這條規則，每個月都要手動把全聯那幾筆改一次 —— 手動維護的東西
    遲早會漏，漏掉的那個月支出就悄悄高估。

    **不動 fingerprint**：那是去重鍵，由 parser 從原始信件內容算出。改了
    它，同一封信下次同步會算出不同指紋、被當成新的一筆，每天重複寫入。

    回新 dict 不就地改：parser 產出的 dict 還帶著 mail_url 等欄位，
    就地改會讓呼叫端分不出哪些欄位動過。
    """
    if not txn or txn.get("direction") == "收入":
        return txn                      # 退款不用跟人分
    if not is_shared_shop(txn.get("shop")):
        return txn
    total = txn.get("amount")
    if total is None:
        return txn
    out = dict(txn)
    out["total"] = total
    out["amount"] = my_share_of(total)
    out["split_type"] = "共同"
    return out


def guess_category(shop):
    """品項 → 消費類別。認不出來回「其他」，不自創類別。

    類別沿用國泰帳單自帶分類（notion_db._SPEND_CATEGORIES）。國泰沒有
    「交通」，所以「搭車」記成「其他」—— 增生分類會讓 Notion 長出
    兩套命名系統，之後兩邊都對不起來。
    """
    name = (shop or "").strip()
    return "餐飲" if any(k in name for k in _FOOD_HINTS) else "其他"


# 分攤類型只認這兩個詞。不加「一起」「共用」這類同義詞 ——
# 「一起吃飯 300」會被誤判成共同消費，而使用者是用按鈕選的，
# 同義詞只擴大誤判面不增加可用性。
_SPLIT_TYPES = ("個人", "共同")


def _strip_split_type(text):
    """從尾端剝離「個人」/「共同」。回 (剩下的文字, split_type or None)。

    只認尾端：「共同基金 3000」的共同在開頭，那是商店名不是分攤類型。
    """
    cleaned = (text or "").strip()
    for name in _SPLIT_TYPES:
        if cleaned.endswith(name):
            return cleaned[: -len(name)].strip(), name
    return cleaned, None


def make_manual_fingerprint(day, amount, shop, split_type=None):
    """個人維持既有四段格式，共同才加後綴。

    個人 300 與共同 600（分攤 300）的「金額」欄都是 300，不加區別就會
    算出相同 fingerprint。個人不動格式是為了不改變既有資料的比對基準。
    """
    raw = f"手動|{day}|{amount}|{shop}"
    if split_type == "共同":
        raw += "|共同"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def parse_manual(text, today=None):
    """「午餐 120」或「晚餐 600 共同」→ 交易 dict。沒有金額就回 None。

    沒金額不猜 —— 記一筆金額錯的帳，比沒記更難發現也更難修。

    split_type 的三態約定，呼叫端靠它決定下一步：
      沒有金額        → 回 None，呼叫端跳金額按鈕
      「晚餐 600」    → split_type=None，呼叫端跳個人/共同按鈕
      「晚餐 600 共同」→ split_type="共同"，呼叫端寫入 Notion
    split_type=None 時 amount 先等於 total，但那是還沒決定分攤前的暫定值，
    呼叫端不該拿去寫入。
    """
    if not text:
        return None
    cleaned, split_type = _strip_split_type(text)

    m = _AMOUNT_RE.search(cleaned)
    if not m:
        return None
    total = float(m.group(1))
    total = int(total) if total == int(total) else total

    shop = (cleaned[:m.start()] + " " + cleaned[m.end():]).strip()
    shop = re.sub(r"\s+", " ", shop)
    if not shop:
        shop = "未命名"

    day = (today or date.today()).isoformat()
    direction = "收入" if any(k in shop for k in _INCOME_HINTS) else "支出"

    # 收入不跟人分攤。留成 None 的話「薪水 50000」也會跳出個人/共同那一段。
    if direction == "收入" and split_type is None:
        split_type = "個人"

    amount = my_share_of(total) if split_type == "共同" else total

    return {
        "date": day,
        "amount": amount,               # 我實際負擔 —— 六處報表都讀這個
        "total": total,                 # 掏出去的全額
        "split_type": split_type,
        "shop": shop,
        "category": guess_category(shop),
        "direction": direction,
        "status": "已結帳",              # 手動輸入就是最終金額，不需要對帳
        "source": "手動",
        "fingerprint": make_manual_fingerprint(day, total, shop, split_type),
    }


# ─────────────────────────────────────────────────────────
# 記帳 Quick Reply 的按鈕內容
#
# 兩段式：先跳品項、再跳該品項的金額。純邏輯，不碰 Notion 也不碰 LINE。
# ─────────────────────────────────────────────────────────

# LINE 一則最多 13 顆按鈕（flex_builder.QUICK_REPLY_MAX）。塞滿沒有壞處：
# 排序不變，最常用的仍在第一顆，後面的要橫滑才看得到。少一顆按鈕就多一次
# 手打的機會，多一顆的成本幾乎是零 —— 成本不對稱時往「寧可多」的方向倒。
BUTTON_LIMIT = 13


# 沒有記帳歷史時的預設按鈕。用一陣子後會被真實習慣取代。
_DEFAULT_ITEMS = ["午餐", "晚餐", "早餐", "咖啡", "飲料", "點心"]

# 距今多久算幾次。近期的算比較多次，物價漲了按鈕會自己跟上，
# 不必手動維護一份「現在午餐多少錢」的清單。
_WEIGHT_WINDOWS = ((30, 3), (60, 2), (90, 1))


def _recency_weight(day, today):
    """這筆記錄在統計裡算幾次。超過 90 天回 0（不計）。

    日期壞掉的列回 0 而不是丟例外 —— Notion 上手改過的列會長出各種
    格式，一列壞掉不該讓整排按鈕消失。
    """
    if not day:
        return 0
    try:
        d = date.fromisoformat(str(day)[:10])
    except ValueError:
        return 0
    delta = max((today - d).days, 0)
    for limit, weight in _WEIGHT_WINDOWS:
        if delta <= limit:
            return weight
    return 0


def frequent_expense_items(txns, limit=BUTTON_LIMIT, pad=True, today=None):
    """常記品項：手動記過越多次的排前面，近期的算比較多次。

    只看 source == "手動"。交易明細裡混著信用卡自動同步的資料，商店名
    長這樣「全聯福利中心－板橋板新」—— 放到按鈕上沒有意義，而且 LINE 的
    label 上限 20 字會把它截成半截店名。

    同次數保持第一次出現的順序：每次跳出來的按鈕位置都在動，比排序不準
    更難用。（與 kitchen.frequent_items 同一套理由，過濾條件不同故各自實作。）

    pad=True 時用 _DEFAULT_ITEMS 補到 limit：沒歷史就給空按鈕列，
    等於這個功能第一天不存在。

    today 可注入：沒有這個參數，測試就得依賴系統時鐘，跑起來時好時壞。
    """
    today = today or date.today()
    counts = {}
    order = []
    for t in txns or []:
        if (t.get("source") or "") != "手動":
            continue
        name = (t.get("shop") or "").strip()
        if not name:
            continue
        weight = _recency_weight(t.get("date"), today)
        if not weight:
            continue
        if name not in counts:
            order.append(name)
        counts[name] = counts.get(name, 0) + weight

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


def _split_of(txn):
    """這筆的分攤型態。舊資料讀不到就當個人。"""
    return txn.get("split_type") or "個人"


def _usual_split_type(rows):
    """這個品項慣用哪種分攤方式。rows 是 [(txn, weight)]。

    晚餐九成是共同的就跳共同價位，咖啡都是個人的就跳個人價位 ——
    「個人 / 共同」問在最後一段，跳金額按鈕的當下還不知道這筆屬於哪種。

    推錯了也不會讓選項消失：另一型態排在下一層，只是位置往後。
    """
    weights = defaultdict(int)
    for t, w in rows:
        weights[_split_of(t)] += w
    if not weights:
        return "個人"
    return max(weights, key=lambda k: weights[k])


def _rank_amounts(rows):
    """[(txn, weight)] → 依加權次數排序的金額 list（多的排前面）。

    統計「原始總額」而不是「金額」：按鈕上的數字是使用者要打進去的錢
    （整桌 600），不是分攤額（300）。用金額欄統計的話，共同消費的按鈕
    每次砍半，愈跳愈小，最後每筆都得手打。

    整數金額回 int，否則按鈕上會出現「120.0」。
    同次數保持第一次出現的順序：按鈕位置每次都在動，比排序不準更難用。
    """
    counts = {}
    order = []
    for t, weight in rows:
        amount = t.get("total")
        if amount is None:
            amount = t.get("amount")
        if amount is None:
            continue
        amount = int(amount) if float(amount) == int(amount) else amount
        if amount not in counts:
            order.append(amount)
        counts[amount] = counts.get(amount, 0) + weight
    return sorted(order, key=lambda a: (-counts[a], order.index(a)))


def frequent_amounts(txns, item, limit=BUTTON_LIMIT, pad=True, today=None):
    """某個品項的常用金額。依品項分別統計 —— 共用一份全域清單會讓咖啡的
    按鈕上出現 200 元。只看 source == "手動"。

    按鈕依「可信度」分四層疊上去，每層都不重複已經放進去的金額：

      1. 90 天內、這個品項慣用型態的金額   ← 最可能命中
      2. 90 天內、另一種型態的金額         ← 真的花過，只是分攤方式不同
      3. 90 天以前、這個品項的金額         ← 舊了，但仍是真實花過的錢
      4. 種子金額                          ← 猜的，只拿來墊底補滿

    分層而不是過濾：90 天窗原本的用意是「別讓兩年前的價位卡在前面」，
    那個目的靠排序就達成了。丟掉它們反而浪費了按鈕的位置 —— 空著的位置
    最後會拿內建猜測值去填，而舊的真實金額比猜的可信。
    """
    key = (item or "").strip()
    if not key:
        return []
    today = today or date.today()

    recent, older = [], []
    for t in txns or []:
        if (t.get("source") or "") != "手動":
            continue
        if (t.get("shop") or "").strip() != key:
            continue
        weight = _recency_weight(t.get("date"), today)
        if weight:
            recent.append((t, weight))
        else:
            # 90 天外不分遠近，一律 1：它們只是用來墊底的，排序意義不大
            older.append((t, 1))

    usual = _usual_split_type(recent)
    layers = [
        [r for r in recent if _split_of(r[0]) == usual],
        [r for r in recent if _split_of(r[0]) != usual],
        older,
    ]

    out = []
    for layer in layers:
        for amount in _rank_amounts(layer):
            if len(out) >= limit:
                return out
            if amount not in out:
                out.append(amount)

    if pad:
        for amount in _SEED_AMOUNTS.get(key, []):
            if len(out) >= limit:
                break
            if amount not in out:
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
