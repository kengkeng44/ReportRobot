"""
大盤指數模組：用 Yahoo Finance chart API 抓主要指數即時報價。
"""

import http_utils


INDEX_LABELS = [
    ("^TWII", "台股加權"),
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq"),
    ("^DJI", "Dow Jones"),
    ("^VIX", "VIX"),
]


def get_index_quote(symbol):
    """回 (price, change, change_pct) 或 None。"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = http_utils.get(
            url,
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        data = r.json()
        result = (data.get("chart", {}) or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta", {}) or {}
        price = meta.get("regularMarketPrice")
        # niche index (^SOX/^VIX/原物料) 的 meta.previousClose 常回 None;
        # 舊 fallback meta.chartPreviousClose 是「range 起始日前一日」,在
        # range=5d 下 ≈ 5 個交易日前, 算出來的 % 變週對週而非日對日
        # (見 2026-06-17 ^SOX 真實 -5.71% 被報成 +5.03% 的反向 bug)。
        # 改抓 close 序列倒數第二筆 = 真正的上一個交易日收盤。
        prev = meta.get("previousClose")
        if prev is None:
            closes = (
                result[0].get("indicators", {}).get("quote", [{}])[0].get("close")
                or []
            )
            valid = [c for c in closes if c is not None]
            if len(valid) >= 2:
                prev = valid[-2]
        if price is None or prev is None:
            return None
        change = price - prev
        pct = (change / prev * 100) if prev else 0
        return price, change, pct
    except Exception as e:
        print(f"指數抓取失敗 {symbol}: {e}")
        return None


def _format_price(value):
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def build_market_summary():
    """回 HTML 字串（LINE 收到時 _strip_html 後變純文字）。"""
    lines = ["<b>📊 大盤指數</b>"]
    for symbol, label in INDEX_LABELS:
        q = get_index_quote(symbol)
        if not q:
            lines.append(f"{label}｜N/A")
            continue
        price, change, pct = q
        emoji = "🟢" if change >= 0 else "🔴"
        sign = "+" if change >= 0 else ""
        lines.append(
            f"{emoji} {label}｜{_format_price(price)}｜"
            f"{sign}{change:,.2f}｜{sign}{pct:.2f}%"
        )
    return "\n".join(lines)
