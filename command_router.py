"""
解析使用者輸入文字，dispatch 到對應的查詢函式。
支援多種觸發：/2330、2330、查2330、AAPL、查AAPL、仁和持股、我的持股、持股 等。
"""

import re


_PORTFOLIO_KEYWORDS = {
    "仁和持股", "我的持股", "持股", "持倉", "我的股票",
    "portfolio", "Portfolio", "PORTFOLIO",
}

_HELP_KEYWORDS = {
    "help", "Help", "HELP", "說明", "指令", "幫助", "教學", "?", "？",
}

HELP_TEXT = (
    "🤖 喵管家指令清單\n"
    "\n"
    "📈 查股票（直接打代號）\n"
    "  • 台股：2330 / /2330 / 查2330\n"
    "  • 美股：AAPL / /aapl / 查TSLA\n"
    "  • ETF：00631L / 0050 / SPY / QQQ\n"
    "  • 中文公司名（要加 / 或 查）：/鼎天 / 查台積\n"
    "\n"
    "📊 比較兩檔績效（要加 /）\n"
    "  • /比較 0050 0056 1y\n"
    "  • /比較 加權 櫃買 ytd\n"
    "  • /SPY vs QQQ 3m\n"
    "  • /台積 跟 鴻海 比較 6m\n"
    "  區間：1m / 3m / 6m / 1y / ytd / 5y / max\n"
    "\n"
    "💼 查仁和持倉\n"
    "  • 仁和持股 / 我的持股 / 持股\n"
    "\n"
    "🤖 自由問答（要加 /）\n"
    "  • /Fed 最新利率動向\n"
    "  • /護國神山近期新聞\n"
    "  • 不認得的中文指令會丟給 AI 上網查\n"
    "\n"
    "🆘 顯示這個說明\n"
    "  • help / 說明 / ?\n"
    "\n"
    "📅 每天 08:00 自動推送\n"
    "  • 🌤️ 淡水區天氣 + 近期活動\n"
    "  • 📊 盤前報告（週末略過）\n"
    "\n"
    "ℹ️ 一般聊天不會被當指令，家人聊天不會被打擾。"
)

# 偵測前綴：開頭是 / 或「查」
_HAS_PREFIX_RE = re.compile(r"^\s*[/查]")
# 真正去掉前綴 + 內外空白
_STRIP_PREFIX_RE = re.compile(r"^\s*[/查]?\s*")

_TW_RE = re.compile(r"^(\d{4,6}[A-Z]?)$")               # 台股 4-6 位數字（可選一個英文）
_US_LOOSE_RE = re.compile(r"^([A-Za-z]{1,5})$")         # 帶前綴時：放寬大小寫
_US_STRICT_RE = re.compile(r"^([A-Z]{2,5})$")           # 不帶前綴：全大寫且 ≥ 2 字
                                                          # 避免 'hi'/'ok' 等日常字觸發
_CJK_RE = re.compile(r"[一-鿿]")                # 中日韓統一漢字


def _strip_prefix(text):
    if not text:
        return ""
    return _STRIP_PREFIX_RE.sub("", text).strip()


# 比較指令的 regex（支援多種寫法）
_COMPARE_PATTERNS = [
    re.compile(r"^比較\s*[:：]?\s*(\S+)\s+(\S+)(?:\s+(\S+))?$"),         # 比較 X Y [period]
    re.compile(r"^(\S+)\s+vs\s+(\S+)(?:\s+(\S+))?$", re.IGNORECASE),      # X vs Y [period]
    re.compile(r"^(\S+)\s*(?:跟|和|對)\s*(\S+)\s*比較?(?:\s+(\S+))?$"),    # X 跟 Y 比較 [period]
    re.compile(r"^(\S+)\s*(?:跟|和|對)\s*(\S+)\s+(\S+)\s*比較?$"),         # X 跟 Y period 比較
]


def _try_parse_compare(cleaned):
    """試 parse 比較指令；成功回 (sym1, sym2, period_or_None)。"""
    for pat in _COMPARE_PATTERNS:
        m = pat.match(cleaned)
        if m:
            groups = m.groups()
            sym1, sym2 = groups[0], groups[1]
            period = groups[2] if len(groups) >= 3 else None
            return (sym1, sym2, period)
    return None


def _find_tw_ticker_by_name(query):
    """從 twstock 對照表反查包含 query 的 ticker；多個 match 取最短代號（通常是主要的）。
    找不到時印 log 方便 debug typo（如永崴/永葳/永威），但不主動回應使用者。"""
    if not query or not _CJK_RE.search(query):
        return None
    try:
        import twstock
        candidates = [code for code, info in twstock.codes.items()
                      if info.name and query in info.name]
        if not candidates:
            print(f"中文反查無 match: {query!r}（可能是 typo 或 twstock 對照表沒收錄）")
            return None
        # 過濾掉超過 6 位的（權證、特殊金融商品代號通常 6 位以上）
        normal = [c for c in candidates if len(c) <= 6]
        pool = normal or candidates
        return min(pool, key=len)
    except Exception as e:
        print(f"twstock 中文名查詢失敗 ({query}): {e}")
        return None


def parse(text):
    """回 (kind, arg) 或 None。kind ∈ {'help', 'portfolio', 'stock', 'compare', 'free_query'}。"""
    if not text:
        return None
    has_prefix = bool(_HAS_PREFIX_RE.match(text))
    cleaned = _strip_prefix(text)
    if not cleaned:
        return None

    if cleaned in _HELP_KEYWORDS:
        return ("help", None)

    if cleaned in _PORTFOLIO_KEYWORDS:
        return ("portfolio", None)

    # 比較指令（必須要前綴，避免「台積跟鴻海比較」之類聊天誤觸發）
    if has_prefix:
        compare = _try_parse_compare(cleaned)
        if compare:
            return ("compare", compare)

    if _TW_RE.match(cleaned):
        return ("stock", cleaned)

    # 美股：帶前綴接受任意大小寫；不帶前綴必須全大寫且 ≥ 2 字
    if has_prefix:
        m = _US_LOOSE_RE.match(cleaned)
        if m:
            return ("stock", cleaned.upper())
    else:
        m = _US_STRICT_RE.match(cleaned)
        if m:
            return ("stock", cleaned)

    # 中文公司名 → 反查 twstock 拿 ticker（例：/鼎天 → 3306、台積 → 2330）
    # 必須帶前綴 / 或 查 才接受，避免家人講「我有買台積」誤觸發
    if has_prefix and _CJK_RE.search(cleaned):
        ticker = _find_tw_ticker_by_name(cleaned)
        if ticker:
            return ("stock", ticker)
        # 既然有 / 前綴 + 中文 + 找不到對應股票/指令，就丟給 AI 自由發揮
        return ("free_query", cleaned)

    return None  # 不認得就靜默不回應，避免騷擾家人聊天


def handle(text):
    """parse + dispatch；回字串（給 reply_message 直接送）或 None。"""
    parsed = parse(text)
    if not parsed:
        return None

    kind, arg = parsed
    try:
        if kind == "help":
            return HELP_TEXT

        if kind == "portfolio":
            from gmail_reader import get_portfolio_from_gmail
            from portfolio import build_portfolio_summary
            portfolio = get_portfolio_from_gmail()
            summary = build_portfolio_summary(portfolio)
            return summary or "目前無持倉資料"

        if kind == "stock":
            from stock_news import get_stock_report
            return get_stock_report(arg)

        if kind == "compare":
            from compare import compare_returns
            return compare_returns(*arg)

        if kind == "free_query":
            from free_query import answer
            return answer(arg)
    except Exception as e:
        print(f"指令處理失敗 ({kind}/{arg})：{e}")
        import traceback; traceback.print_exc()
        return f"查詢失敗：{e}"

    return None
