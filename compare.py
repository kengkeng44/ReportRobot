"""
標的績效比較模組：兩檔股票 / 指數的時間區間漲跌對比。
純 Python 計算（用 yfinance 抓歷史 close），不依賴 AI。
"""

import re

# 常見指數對照（IX 代號、中文簡寫 → Yahoo Finance symbol）
_INDEX_ALIASES = {
    # 台股加權
    "IX0001": "^TWII", "加權": "^TWII", "加權指數": "^TWII",
    "台股": "^TWII", "TWII": "^TWII",
    # 櫃買（Yahoo 實際 symbol 是 ^TWOII，^TWO / ^TPEX 等都查不到）
    "IX0043": "^TWOII", "櫃買": "^TWOII", "櫃買指數": "^TWOII",
    "OTC": "^TWOII", "TWO": "^TWOII", "TWOII": "^TWOII",
    # 美股
    "DOW": "^DJI", "道瓊": "^DJI", "DJI": "^DJI",
    "標普": "^GSPC", "標普500": "^GSPC", "SP500": "^GSPC", "GSPC": "^GSPC",
    "那斯達克": "^IXIC", "那指": "^IXIC", "Nasdaq": "^IXIC", "IXIC": "^IXIC",
    "費半": "^SOX", "SOX": "^SOX",
    "VIX": "^VIX", "恐慌": "^VIX",
}

# 期間關鍵字 → yfinance period
_PERIOD_MAP = {
    "1m": "1mo", "1mo": "1mo", "一個月": "1mo", "近一月": "1mo", "近一個月": "1mo",
    "3m": "3mo", "3mo": "3mo", "三個月": "3mo", "近三月": "3mo", "近三個月": "3mo", "近季": "3mo",
    "6m": "6mo", "6mo": "6mo", "半年": "6mo", "近半年": "6mo",
    "1y": "1y", "一年": "1y", "近一年": "1y",
    "ytd": "ytd", "YTD": "ytd", "今年": "ytd", "今年迄今": "ytd",
    "5y": "5y", "五年": "5y", "近五年": "5y",
    "max": "max", "全部": "max", "歷史": "max",
}


_TW_TICKER_RE = re.compile(r"^\d{4,6}[A-Z]?$")
_US_TICKER_RE = re.compile(r"^[A-Za-z]{1,5}$")
_CJK_RE = re.compile(r"[一-鿿]")


def _resolve_symbol(s):
    """input → (yfinance_symbol, 顯示用 label)；無法辨識回 (None, s)。"""
    if not s:
        return None, s
    raw = s.strip()
    upper = raw.upper()

    # 1. 指數別名
    if raw in _INDEX_ALIASES:
        return _INDEX_ALIASES[raw], raw
    if upper in _INDEX_ALIASES:
        return _INDEX_ALIASES[upper], upper

    # 2. 台股代號 4-6 位數字
    if _TW_TICKER_RE.match(raw):
        try:
            import twstock
            info = twstock.codes.get(raw)
            label = info.name if info and info.name else raw
            if info and info.market and "上櫃" in info.market:
                return f"{raw}.TWO", f"{raw} {label}"
            return f"{raw}.TW", f"{raw} {label}"
        except Exception:
            return f"{raw}.TW", raw

    # 3. 美股代號 1-5 字母
    if _US_TICKER_RE.match(raw):
        return upper, upper

    # 4. 中文公司名 → twstock 反查
    if _CJK_RE.search(raw):
        try:
            import twstock
            for code, info in twstock.codes.items():
                if info.name and raw in info.name and len(code) <= 6:
                    sym, _ = _resolve_symbol(code)
                    return sym, f"{code} {info.name}"
        except Exception:
            pass

    return None, raw


def _resolve_period(p):
    if not p:
        return "1y", "近 1 年"
    p_clean = p.strip().lower()
    period = _PERIOD_MAP.get(p_clean) or _PERIOD_MAP.get(p.strip())
    if not period:
        return None, None
    label_map = {
        "1mo": "近 1 個月", "3mo": "近 3 個月", "6mo": "近 6 個月",
        "1y": "近 1 年", "ytd": "今年迄今", "5y": "近 5 年", "max": "全部歷史",
    }
    return period, label_map.get(period, period)


def compare_returns(sym1_input, sym2_input, period_input=None):
    """組裝比較分析 HTML 字串；任一邊解析失敗就回明確錯誤訊息。"""
    sym1, label1 = _resolve_symbol(sym1_input)
    sym2, label2 = _resolve_symbol(sym2_input)
    if not sym1:
        return f"無法辨識「{sym1_input}」，請確認代號或名稱"
    if not sym2:
        return f"無法辨識「{sym2_input}」，請確認代號或名稱"

    period, period_label = _resolve_period(period_input)
    if not period:
        return f"無法辨識區間「{period_input}」，可用：1m/3m/6m/1y/ytd/5y/max 或中文「近三月/今年/近一年」等"

    try:
        import yfinance as yf
        h1 = yf.Ticker(sym1).history(period=period)
        h2 = yf.Ticker(sym2).history(period=period)
    except Exception as e:
        return f"歷史資料抓取失敗：{e}"

    if h1.empty:
        return f"無法取得 {label1}（{sym1}）的歷史資料"
    if h2.empty:
        return f"無法取得 {label2}（{sym2}）的歷史資料"

    c1 = h1["Close"].dropna()
    c2 = h2["Close"].dropna()
    if c1.empty or c2.empty:
        return "歷史資料區間內無有效收盤價"

    start1, end1 = c1.iloc[0], c1.iloc[-1]
    start2, end2 = c2.iloc[0], c2.iloc[-1]
    ret1 = (end1 - start1) / start1 * 100
    ret2 = (end2 - start2) / start2 * 100
    diff = ret1 - ret2

    def _emoji(v):
        return "🟢" if v >= 0 else "🔴"

    def _sign(v):
        return "+" if v >= 0 else ""

    def _fmt(p):
        return f"{p:,.2f}" if abs(p) < 1000 else f"{p:,.0f}"

    out = [f"<b>📊 比較分析（{period_label}）</b>", ""]
    out.append(f"{_emoji(ret1)} <b>{label1}</b>")
    out.append(f"  起點 {_fmt(start1)} → 終點 {_fmt(end1)}")
    out.append(f"  漲跌：{_sign(ret1)}{ret1:.2f}%")
    out.append("")
    out.append(f"{_emoji(ret2)} <b>{label2}</b>")
    out.append(f"  起點 {_fmt(start2)} → 終點 {_fmt(end2)}")
    out.append(f"  漲跌：{_sign(ret2)}{ret2:.2f}%")
    out.append("")
    out.append(f"📐 差距：{label1} 比 {label2} {_sign(diff)}{diff:.2f}%")
    return "\n".join(out)
