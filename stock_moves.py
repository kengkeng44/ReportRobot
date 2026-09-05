"""每日信的「持倉今日漲跌」。

刻意只回答一件事：**今天漲跌幾 %**。

成本、總報酬、市值那些走 LINE 的「持股」指令
（portfolio.build_portfolio_summary 已經很完整）。信裡塞全部會太長 ——
使用者 2026-09-05 指定只要漲跌。

持倉從 Notion 讀（notion_db.holdings_load），不從 Gmail 重算：
重算要解析對帳單信件，那是每日信裡最貴的一段，而漲跌只需要代號。

價格與前一日收盤來自**同一個** Yahoo 回應 —— meta 裡本來就同時有
regularMarketPrice 與 previousClose，不必為了算漲跌多打一次 API。
"""

# 信裡最多列幾檔。持倉多的時候這一塊會把整封信灌爆，
# 而且看第 9 名漲 0.3% 沒有意義。
LIMIT = 8

_UP, _DOWN, _FLAT = "▲", "▼", "－"


def pct_change(price, prev):
    """漲跌幅（%）。算不出來回 None。

    prev 是 None 或 0 都回 None：「沒有前一日收盤」跟「持平」是兩件事，
    而且除以 0 會炸。興櫃與停牌的資料真的會回 0。
    """
    if price is None or not prev:
        return None
    return (price - prev) / prev * 100


def _price_str(price):
    """1085.00 → 1,085；232.10 → 232.1。

    台股與美股的小數位數不同，一律補兩位只是雜訊。
    """
    out = f"{price:,.2f}"
    return out.rstrip("0").rstrip(".") if "." in out else out


def format_moves(items, limit=LIMIT):
    """items: [{'display', 'price', 'pct'}] → 文字。沒東西可列回 None。

    照漲跌幅**絕對值**排序：使用者要看的是「什麼在動」，不是
    「什麼最值錢」—— 跌 5% 該排在漲 1% 前面。
    """
    rows = list(items or [])
    if not rows:
        return None

    rows.sort(key=lambda r: abs(r.get("pct") or 0), reverse=True)

    lines = []
    for r in rows[:limit]:
        pct = r.get("pct") or 0
        # 0.0% 標成紅或綠都是誤導
        arrow = _FLAT if round(pct, 1) == 0 else (_UP if pct > 0 else _DOWN)
        lines.append(
            f"{arrow} {r['display']}　{_price_str(r['price'])}　{abs(pct):.1f}%"
        )

    if len(rows) > limit:
        lines.append(f"　（還有 {len(rows) - limit} 檔）")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# I/O 邊界：上面全是純邏輯，以下開始碰 Notion 與 Yahoo
#
# 兩個間接層（_store / _quote）存在的唯一理由是讓測試整個換掉它們 ——
# phrasebook 用同樣的手法。
# ─────────────────────────────────────────────────────────

def _store():
    import notion_db
    return notion_db


def _quote(ticker):
    import portfolio
    return portfolio.get_price_and_prev(ticker)


def daily_moves():
    """持倉今日漲跌的文字區塊。沒有任何抓得到價的持倉就回 None。"""
    rows = _store().holdings_load()

    items = []
    for r in rows or []:
        ticker = (r.get("ticker") or "").strip()
        if not ticker:
            continue
        try:
            price, prev = _quote(ticker)
        except Exception as e:
            # 一檔抓價炸掉不該讓整個區塊消失
            print(f"[stock_moves] {ticker} 報價失敗：{e}")
            continue
        pct = pct_change(price, prev)
        if pct is None:
            # 黃金存摺、興櫃抓不到價 —— 顯示成 0.0% 會讓人以為它今天沒動
            continue
        items.append({"display": r.get("display") or ticker,
                      "price": price, "pct": pct})

    return format_moves(items)
