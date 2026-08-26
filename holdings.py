"""持倉重建：以月對帳單的庫存欄位為起點，只套用快照日之後的成交。

為什麼需要這個模組（HANDOFF 4.1）：
`gmail_reader.get_portfolio_from_gmail()` 只抓得到近 3 個月的信，然後從
成交紀錄「累加」出持倉。買很久、之後就沒再交易的部位（實測：台積電）
在抓取範圍內沒有任何成交，於是整檔消失 —— 淨值少算，Notion 持倉表留下
孤兒資料列。

正解不是把抓取範圍拉長（信終究會過期），是換一個起點：月對帳單的
**庫存**欄位本身就是那個時點的完整快照，之後只要補上快照日之後的成交。

這裡全是純邏輯，不碰 Gmail 也不碰 Notion —— snapshots 由呼叫端餵進來。

⚠️ 這個模組不會刪任何東西。「不在 portfolio 裡的就清掉」在抓取範圍
受限時會刪掉真實持倉，HANDOFF 4.1 明文禁止。
"""

import calendar
import re

_MARKETS = ("TW", "US")

# 台股代號：4-6 位數字，槓桿/反向 ETF 結尾會多一個英文字母（00632R）
_TW_TICKER_RE = re.compile(r"^\d{4,6}[A-Z]?$")

# 月對帳單的年月兩種寫法：
#   有價證券（台股）：【富邦證券】有價證券月對帳單-2026年6月
#   複委託（美股）：  富邦證券2026~7複委託月對帳單
_PERIOD_RES = (
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月"),
    re.compile(r"(\d{4})\s*~\s*(\d{1,2})"),
)

# 成交回報 / 日對帳單的主旨也含年月，被當成月對帳單的話就會拿「當日成交」
# 當庫存快照，整份持倉直接錯掉。
_NOT_MONTHLY = ("日對帳單", "成交回報")


def monthly_statement_period(subject):
    """月對帳單主旨 → (年, 月)。不是月對帳單回 None。"""
    s = subject or ""
    if "月對帳單" not in s:
        return None
    if any(bad in s for bad in _NOT_MONTHLY):
        return None
    for rx in _PERIOD_RES:
        m = rx.search(s)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12:
                return (year, month)
    return None


def statement_market(subject):
    """主旨 → 市場。複委託是美股，其餘（有價證券 / 興櫃）是台股。"""
    return "US" if "複委託" in (subject or "") else "TW"


def pick_latest_monthly(subjects):
    """一堆主旨 → {市場: 最新一期 (年, 月)}。沒有月對帳單的市場不會出現。

    兩個市場各挑各的：實測 2026-08 時複委託已出到 7 月、有價證券只到 6 月，
    共用一個 cutoff 會讓其中一邊算錯。
    """
    latest = {}
    for subject in subjects or []:
        period = monthly_statement_period(subject)
        if not period:
            continue
        market = statement_market(subject)
        if period > latest.get(market, (0, 0)):
            latest[market] = period
    return latest


def month_end(year, month):
    """該月最後一天 → (年, 月, 日)。跟 trade['date'] 同樣是 tuple 好直接比大小。"""
    return (year, month, calendar.monthrange(year, month)[1])


def guess_market(ticker):
    """沒標 market 的舊資料用代號形狀猜。"""
    t = str(ticker or "").strip()
    # AU9901（臺銀金／黃金現貨）在證交所掛牌、台幣計價，但代號帶字母。
    # 分錯市場會讓它的成交套到美股快照的 cutoff 上。
    if t.upper().startswith("AU"):
        return "TW"
    return "TW" if _TW_TICKER_RE.match(t) else "US"


def aggregate_trades(trades, book=None):
    """依時間順序累計成交。行為與 gmail_reader._aggregate_portfolio 一致。

    買入加股數加成本；賣出按當下均價扣成本；最終 shares<=0 的剔除。
    book 可以先放快照當起點（{ticker: {shares, cost_basis}}）。
    """
    # 依日期排序，避免 dict 順序影響成本累計（None 日期排最前）
    trades_sorted = sorted(trades or [], key=lambda t: t.get("date") or (0, 0, 0))

    book = dict(book or {})
    for t in trades_sorted:
        p = book.setdefault(t["ticker"], {"shares": 0, "cost_basis": 0.0})
        if t["action"] == "buy":
            p["shares"] += t["shares"]
            # cost_basis 是 None 代表「成本未知」（快照沒帶成本），不是 0。
            # 未知加上已知還是未知 —— 補一段已知成本不會讓整體變成已知。
            if p["cost_basis"] is not None:
                p["cost_basis"] += t["shares"] * t["price"]
        else:  # sell
            if p["shares"] > 0:
                sold = min(t["shares"], p["shares"])
                # 成本未知時只扣股數。拿 0 當均價去扣會扣出一個假成本，
                # 之後的損益率看起來正常但完全是編的。
                if p["cost_basis"] is not None:
                    avg = p["cost_basis"] / p["shares"]
                    p["cost_basis"] -= avg * sold
                p["shares"] -= sold

    return {
        ticker: {
            "shares": p["shares"],
            "avg_cost": (None if p["cost_basis"] is None
                         else p["cost_basis"] / p["shares"]),
        }
        for ticker, p in book.items()
        if p["shares"] > 0
    }


_NO_SNAPSHOT_REASON = (
    "沒有月對帳單庫存快照，只能從抓得到的成交紀錄累加；"
    "買很久之後就沒再交易的部位會整檔漏掉"
)
_EMPTY_SNAPSHOT_REASON = (
    "月對帳單庫存解析為空（可能是格式變了），不當成真的沒持倉，"
    "退回從成交紀錄累加"
)


def build_portfolio(trades, snapshots=None):
    """回 (portfolio, sources)。

    snapshots: [{"market", "period": (年,月), "holdings": {ticker: {shares, avg_cost}}}]
    每個市場各自用自己那期的月底當 cutoff：快照日以前的成交已經反映在庫存裡，
    再加一次就是雙重計算。

    sources 記下每個市場的資料是怎麼來的、幾筆成交被套用/跳過 ——
    數字對不上時要看得出原因，不能只給一個結果。
    """
    by_market = {m: [] for m in _MARKETS}
    for t in trades or []:
        market = t.get("market") or guess_market(t.get("ticker"))
        by_market.setdefault(market, []).append(t)

    snap_by_market = {s.get("market"): s for s in snapshots or []}

    portfolio, sources = {}, {}
    for market in set(by_market) | set(snap_by_market):
        market_trades = by_market.get(market) or []
        snap = snap_by_market.get(market)
        holdings = (snap or {}).get("holdings") or {}

        if not snap:
            if not market_trades:
                continue
            portfolio.update(aggregate_trades(market_trades))
            sources[market] = {
                "source": "trades_only",
                "reason": _NO_SNAPSHOT_REASON,
                "period": None,
                "trades_applied": len(market_trades),
                "trades_before_cutoff": 0,
                "trades_undated": 0,
            }
            continue

        if not holdings:
            portfolio.update(aggregate_trades(market_trades))
            sources[market] = {
                "source": "trades_only",
                "reason": _EMPTY_SNAPSHOT_REASON,
                "period": snap.get("period"),
                "trades_applied": len(market_trades),
                "trades_before_cutoff": 0,
                "trades_undated": 0,
            }
            continue

        # 月對帳單庫存是月底狀態,沒帶 cutoff 就用月底;手動快照帶精確日期
        cutoff = snap.get("cutoff") or month_end(*snap["period"])
        after, before, undated = [], 0, 0
        for t in market_trades:
            day = t.get("date")
            if not day:
                # 分不出在快照前還是後就跳過，不猜 —— 猜錯是雙重計算或漏算
                undated += 1
            elif day > cutoff:
                after.append(t)
            else:
                before += 1

        # 庫存表沒有成本欄（實測），avg_cost 是 None —— cost_basis 也保持 None，
        # 一路傳到顯示端標「成本未知」，不要用收盤價或 0 充數。
        book = {
            ticker: {
                "shares": h["shares"],
                "cost_basis": (None if h.get("avg_cost") is None
                               else h["shares"] * h["avg_cost"]),
            }
            for ticker, h in holdings.items()
        }
        portfolio.update(aggregate_trades(after, book=book))
        sources[market] = {
            "source": "snapshot",
            # 快照哪來的要講實話：台股沒有月對帳單庫存表，說成月對帳單
            # 會害下一個人去查一份不存在的東西
            "origin": snap.get("origin", "statement"),
            "reason": "",
            "period": snap["period"],
            "trades_applied": len(after),
            "trades_before_cutoff": before,
            "trades_undated": undated,
        }

    return portfolio, sources


def describe_sources(sources):
    """把 sources 講成人看得懂的一段話，掛在持倉卡片下面。

    重點是「這份數字可不可信」：用快照的講期別，沒快照的講為什麼可能少算。
    """
    lines = []
    for market in sorted(sources or {}):
        s = sources[market]
        if s["source"] == "snapshot":
            year, month = s["period"]
            origin = {
                "manual": "手動設定的持倉",
                "notion": "Notion 起始庫存",
            }.get(s.get("origin"), "月對帳單庫存")
            line = f"{market}：{year}/{month:02d} {origin}為起點"
            if s["trades_applied"]:
                line += f"，加上之後 {s['trades_applied']} 筆成交"
        else:
            line = f"⚠️ {market}：{s['reason']}"
        if s["trades_undated"]:
            line += f"（{s['trades_undated']} 筆成交無日期已跳過）"
        lines.append(line)
    return "\n".join(lines)


# ── 複委託月對帳單「股票庫存明細表」 ──────────────────────
# 真實版面 2026-08-25 由 /admin/statement-dump 撈出（見 tests/test_holdings_inventory.py）。
# pdfplumber 把表格攤平後欄位位置會飄：證券名稱可能留在行內、被推到上一行、
# 或拆成上下兩行。所以不靠欄位序號，改拿幣別欄 `USD TWD USD` 當錨點回推 ——
# 那三個 token 在所有實測變形裡都緊跟在「庫存股數 圈存股數」後面。
_INVENTORY_HEADING = "股票庫存明細表"
_CURRENCY_ANCHOR = ("USD", "TWD", "USD")


def _to_int(token):
    try:
        return int(token.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def parse_us_inventory(text):
    """複委託月對帳單文字 → {ticker: {"shares": int, "avg_cost": None}}。

    庫存表只有參考收盤價與參考市值，**沒有成本均價**，所以 avg_cost 一律 None。
    拿收盤價充數會讓未實現損益永遠是 0 —— 看起來正常的假數字最難發現。

    沒有庫存表（台股月對帳單實測就沒有）回 {}，不猜也不炸。
    """
    if not text or _INVENTORY_HEADING not in text:
        return {}

    inventory = {}
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 5:
            continue
        for i in range(2, len(tokens) - 2):
            if tuple(tokens[i:i + 3]) != _CURRENCY_ANCHOR:
                continue
            shares = _to_int(tokens[i - 2])
            earmarked = _to_int(tokens[i - 1])
            ticker = tokens[1]
            if shares is None or earmarked is None or shares <= 0:
                break
            inventory[ticker] = {"shares": shares, "avg_cost": None}
            break
    return inventory


# ── 手動持倉快照（台股用） ────────────────────────────────
# 台股月對帳單沒有庫存表（2026-08-25 /admin/statement-dump 實測），
# 所以起始庫存只能由使用者從富邦 e01 查一次填進來。持倉數字走環境變數，
# 不進 repo —— repo 是公開的。
_MANUAL_ENTRY_RE = re.compile(r"^([A-Za-z0-9]+)\s*:\s*([\d,]+)\s*(?:@\s*([\d.]+))?$")


def manual_snapshot(market, spec, asof):
    """把 "2317:1000@95.5, 0050:500" + "2026-06-30" 轉成 build_portfolio 吃的快照。

    asof 是必填。build_portfolio 會跳過基準日以前的成交（已含在庫存裡），
    基準日猜錯就會少算或雙重計算 —— 而且錯得無聲無息。所以寧可回 None
    讓系統退回成交累加，也不要拿今天當預設。

    全部解析失敗時也回 None，不回空持倉 —— 空快照會被當成
    「基準日時真的沒持股」，反而把之前的成交全部跳過。
    """
    if not spec or not asof:
        return None
    try:
        year, month, day = (int(p) for p in str(asof).split("-"))
    except (ValueError, TypeError):
        return None

    positions, skipped = {}, []
    for raw in str(spec).split(","):
        entry = raw.strip()
        if not entry:
            continue
        m = _MANUAL_ENTRY_RE.match(entry)
        if not m:
            skipped.append(entry)
            continue
        shares = _to_int(m.group(2))
        if not shares or shares <= 0:
            skipped.append(entry)
            continue
        positions[m.group(1)] = {
            "shares": shares,
            "avg_cost": float(m.group(3)) if m.group(3) else None,
        }

    if not positions:
        return None
    return {
        "market": market,
        "period": (year, month),
        "holdings": positions,
        "skipped": skipped,
        "origin": "manual",
        # 精確到日。手動基準日常常是月中,沿用 month_end 會把基準日之後、
        # 月底之前的成交當成「已含在庫存裡」而跳過 —— 今天填今天看不出來,
        # 過幾天才開始悄悄漏算。
        "cutoff": (year, month, day),
    }
