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


# 某些 ticker 用 ETF / proxy 抓回來, 顯示時需乘倍率對齊 underlying 量級。
# GLD 上市時 1 share = 1/10 oz gold, 但 0.40%/yr expense ratio 累積 22 年後
# 每股已縮到 ~0.0923 oz, 所以「回推 oz 量級」的正確倍率是 ~10.84, 不是 10.0。
# 實測 2026-07-06: GLD 收 $382.13, 黃金現貨 ~$4,143/oz → 4143/382.13 ≈ 10.84。
# 舊值 10.0 會讓黃金每天少報 ~8% (顯示 ~$3,821 vs 實際 ~$4,143), 方向對但數值偏低。
# 注意: 此倍率仍是靜態近似, expense ratio 會讓每股 oz 每年再縮 ~0.4%,
# 故每隔幾年需依當時 spot/GLD 比值重新校準 (或改成依上市日+費率動態衰減)。
# (替代 GC=F 因 Yahoo 對期貨日線常回 close=None, 見 2026-06-19 黃金反向 bug)
PRICE_MULTIPLIERS = {
    "GLD": 10.84,  # gold ETF -> ~oz equivalent (label 顯示「黃金」)
}


def get_index_quote(symbol):
    """回 (price, change, change_pct) 或 None。
    若 symbol 在 PRICE_MULTIPLIERS, price 與 change 會乘倍率 (pct 不變)。"""
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
        # meta.previousClose 對 niche index / 期貨常回 None, fallback 抓 close 序列。
        # 重要: closes[-2] 必須非 None 才接受 — 過濾 None 後再取 -2 會誤抓更早的日子,
        # 對 24h 交易市場 (GC=F 期貨) 特別會撞:Thu close=None → filtered -2 = Wed,
        # 結果「跟前天比」算出 -3.65% 但真實 today 是 -0.68%。
        # 寧可整個 quote return None 顯示 N/A, 不要錯方向。
        # (見 2026-06-17 ^SOX 反向 bug 起因 1 + 2026-06-19 GC=F 反向 bug 起因 2)
        prev = meta.get("previousClose")
        if prev is None:
            closes = (
                result[0].get("indicators", {}).get("quote", [{}])[0].get("close")
                or []
            )
            if len(closes) >= 2 and closes[-2] is not None:
                prev = closes[-2]
        if price is None or prev is None:
            return None
        change = price - prev
        pct = (change / prev * 100) if prev else 0
        multiplier = PRICE_MULTIPLIERS.get(symbol, 1.0)
        return price * multiplier, change * multiplier, pct
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
