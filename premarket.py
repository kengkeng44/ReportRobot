"""
盤前報告（每日 08:00 推、週末略過）：
- 國際指數隔夜收盤（含費半 SOX）
- 重要 ADR 與盤後價（TSMC / NVIDIA）
- 匯率與原物料（USD/TWD、DXY、USD/JPY、油、金）
- 三大法人買賣超
- AI 根據 Google News 標題整理 Fed / 總經 / 地緣 / 類股 / 法說會
"""

import os
import re

import anthropic
import usage_tracker
import sonnet_client

from chips import get_institutional_trades
from markets import _format_price, get_index_quote
from tz_utils import today_tpe


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")


# 國際指數（瘦身：只留與台股相關性最高的）
INTL_INDICES = [
    ("^IXIC", "Nasdaq"),
    ("^SOX", "費半"),
]

# 重要 ADR
ADR_STOCKS = [
    ("TSM", "TSMC ADR"),
]

# 原物料（拿掉原油，只留黃金）
# 黃金用 GLD ETF 而非 GC=F 期貨: 期貨 24h 交易 Yahoo 日線常回 close=None,
# 觸發 fallback 抓到「兩天前」造成方向反向 (見 2026-06-19 bug)。GLD 是美股,
# close 永遠完整, *10 倍率 (見 markets.PRICE_MULTIPLIERS) 換算成黃金/oz 量級顯示。
COMMODITIES = [
    ("GLD", "黃金"),
]


def is_weekend():
    return today_tpe().weekday() >= 5  # Sat=5, Sun=6


def _format_pct(pct):
    """漲跌百分比格式化成固定寬度，並在百分比區段前墊半形空白讓視覺對齊。
    例： '+0.34%' / '-1.20%' / '+12.5%' / '-100%'。
    LINE 字型非等寬，無法完美對齊，但 % 永遠在第 6 字內。"""
    sign = "+" if pct >= 0 else "-"
    abs_pct = abs(pct)
    if abs_pct >= 100:
        body = f"{abs_pct:.0f}%"
    elif abs_pct >= 10:
        body = f"{abs_pct:.1f}%"
    else:
        body = f"{abs_pct:.2f}%"
    return f"{sign}{body}"


def _quote_line(symbol, label):
    q = get_index_quote(symbol)
    if not q:
        return f"⚪ ─────｜{label}｜N/A"
    price, change, pct = q
    emoji = "🟢" if change >= 0 else "🔴"
    pct_str = _format_pct(pct)
    return f"{emoji} {pct_str}｜{label}｜{_format_price(price)}"


def _format_chip(value):
    """三大法人金額：±NNN.NN 億，固定寬度方便視覺對齊。"""
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value):.2f} 億"


def _build_chip_block_from(chips):
    if not chips:
        return "N/A（資料尚未公布或抓取失敗）"
    lines = [f"📅 {chips['date']} 收盤"]
    for label, key in [("外資", "foreign"), ("投信", "investment_trust"), ("自營商", "dealer")]:
        v = chips.get(key)
        if v is None:
            continue
        emoji = "🟢" if v >= 0 else "🔴"
        lines.append(f"{emoji} {_format_chip(v)}｜{label}")
    return "\n".join(lines)


def _strip_to_bullets(text):
    """只留 • / ・ / - / * 開頭的行；沒 bullet 一律回空字串。"""
    if not text:
        return ""
    lines = [l.rstrip() for l in text.splitlines()]
    bullets = []
    started = False
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            # 空行跳過:Sonnet 5 習慣在 bullet 之間空一行,遇空行就停會只剩第一條
            continue
        if stripped.startswith(("•", "・", "-", "*")):
            bullets.append(stripped)
            started = True
        elif started:
            break
    return "\n".join(bullets)


# 盤前重點是這份報告裡唯一要付費的一段（Claude API）。
# 早上推播跑一次，使用者按「盤前」按鈕又跑一次，同一天的內容其實一樣，
# 沒理由付兩次錢。用當日快取讓一天只真的呼叫一次。
_AI_CACHE = {"day": None, "text": ""}


def _clear_ai_cache():
    _AI_CACHE.update(day=None, text="")


def _build_ai_summary(chip_data=None):
    """當日快取版。同一天重複呼叫直接回上次結果，不重複付費。

    失敗（回空字串）不快取 —— 把失敗記起來會讓那一整天都沒有盤前重點。
    """
    today = today_tpe()
    if _AI_CACHE["day"] == today and _AI_CACHE["text"]:
        return _AI_CACHE["text"]

    text = _ai_summary_uncached(chip_data)
    if text:
        _AI_CACHE.update(day=today, text=text)
    return text


# 盤前新聞改成自己抓 Google News RSS(免費),只把標題餵模型,不再讓模型 web_search。
# web_search 每次 $0.01,搜到的網頁全文還要算輸入 token(一次約 1.2 萬);
# 標題清單約 1-2 千 token,整段盤前重點的費用降一個數量級。
NEWS_QUERIES = [
    "台股 盤前 when:1d",
    "台股 類股 外資 when:1d",
    "Fed 聯準會 利率 when:2d",
    "CPI OR 非農 OR PMI 經濟數據 when:2d",
    "美股 科技股 收盤 when:1d",
    "關稅 OR 地緣 股市 when:2d",
    "法說會 when:2d",
]
NEWS_PER_QUERY = 4
NEWS_MAX_AGE_HOURS = 48
NEWS_MAX_ITEMS = 18


def _news_lines(now_ts=None):
    """抓各主題新聞標題,去重、濾掉太舊的,回 ['- [MM-DD] 標題（來源）', ...]。"""
    import time
    from datetime import datetime
    from stock_news import _google_news_rss

    now_ts = now_ts or time.time()
    seen, lines = set(), []
    for q in NEWS_QUERIES:
        for it in _google_news_rss(q, limit=NEWS_PER_QUERY):
            title = (it.get("title") or "").strip()
            key = re.sub(r"\W+", "", title)
            if not title or key in seen:
                continue
            ts = it.get("published") or 0
            if ts and now_ts - ts > NEWS_MAX_AGE_HOURS * 3600:
                continue
            seen.add(key)
            day = datetime.fromtimestamp(ts).strftime("%m-%d") if ts else "日期不明"
            src = it.get("source") or ""
            lines.append(f"- [{day}] {title}（{src}）")
            if len(lines) >= NEWS_MAX_ITEMS:
                return lines
    return lines


def _ai_summary_uncached(chip_data=None):
    """用 Claude 根據真實數字 + 新聞標題整理盤前重點。失敗回空字串。
    chip_data：來自 chips.get_institutional_trades()，把真實數字注入 prompt
    讓 AI 用準確基準寫昨日資金流向。
    另注入 markets.get_index_quote 抓的指數/ADR/原物料真實收盤,
    防 LLM 從新聞拿錯方向 (見 2026-06-17 ^SOX 反向 bug 起因之一)。"""
    today = today_tpe().strftime("%Y-%m-%d")
    chip_block = ""
    if chip_data:
        parts = [f"日期 {chip_data['date']}"]
        if chip_data.get('foreign') is not None:
            parts.append(f"外資 {chip_data['foreign']:+.2f} 億")
        if chip_data.get('investment_trust') is not None:
            parts.append(f"投信 {chip_data['investment_trust']:+.2f} 億")
        if chip_data.get('dealer') is not None:
            parts.append(f"自營 {chip_data['dealer']:+.2f} 億")
        if chip_data.get('total') is not None:
            parts.append(f"合計 {chip_data['total']:+.2f} 億")
        chip_block = (
            "\n\n[實際三大法人數字 — 請務必引用此真實數字]\n"
            + " / ".join(parts)
        )

    # Ground truth 報價注入: 拿 markets.get_index_quote 已驗證的指數/ADR/原物料,
    # 讓 LLM 以真實數字為準 (LLM 對方向/日期判斷不穩, 已知 +5% vs -5% 反向案例)。
    quote_lines = []
    for symbol, label in INTL_INDICES + ADR_STOCKS + COMMODITIES:
        q = get_index_quote(symbol)
        if not q:
            continue
        price, change, pct = q
        direction = "漲" if change >= 0 else "跌"
        quote_lines.append(
            f"{label} ({symbol}): 收盤 {price:,.2f}, {direction} {abs(pct):.2f}%"
        )
    quote_block = ""
    if quote_lines:
        quote_block = (
            "\n\n[實際昨夜美股/原物料收盤 — 請務必引用以下真實數字, "
            "新聞標題與此衝突時以此為準]\n"
            + "\n".join(quote_lines)
        )

    news = _news_lines()
    if not news:
        print("盤前新聞 RSS 抓不到任何標題，跳過 AI 整理")
        return ""
    news_block = "\n\n[近 48 小時新聞標題 — 只能根據這些標題與上方真實數字整理]\n" + "\n".join(news)

    prompt = (
        f"今天是 {today}（台北時間，嚴格依此判斷「最新」/「昨日」）。\n"
        f"請根據下方提供的真實數字與新聞標題，整理今日台股開盤前重點。\n"
        f"只輸出 3-4 條 bullet，每點 `• ` 開頭、一句話（40 字內），"
        f"純文字繁體中文，不要 Markdown。這是「重點」不是懶人包——寧缺勿濫，"
        f"沒有夠份量的內容就少寫一條，不要湊數、不要把一條塞成一整段。\n"
        f"**所有日期一律 YYYY-MM-DD 格式（例 2026-05-07）**，禁用 5/7、05/07、5月7日。"
        f"{chip_block}"
        f"{quote_block}"
        f"{news_block}\n\n"
        f"寫法：\n"
        f"1. 第 1 條必寫昨日台股資金流向（用上方真實數字）：外資 / 投信 / 自營買賣超，"
        f"最多再點一個當日最強或最弱類股，不要列 Top 3、不要逐檔權值股。\n"
        f"2. 其餘 2-3 條，從下列挑「今天最該知道」的講，每項最多一條：\n"
        f"   - 昨夜美股：Nasdaq / 費半 / TSMC / 黃金，**一律引用上方真實收盤數字**\n"
        f"   - Fed 動向、重要經濟數據（CPI/非農/PMI 等）\n"
        f"   - 地緣 / 關稅 / 央行政策的重大事件\n"
        f"   - 權值股法說 / 財報 / 併購（附具體數字、日期）\n\n"
        f"規則：\n"
        f"- **新聞標題與上方真實數字方向或數值衝突時，一律以真實數字為準**\n"
        f"- 只能使用上方提供的資訊，禁止補充標題裡沒有的數字或事件\n"
        f"- 直接列出 bullet，禁止開場白與結語"
    )
    try:
        # 不給 web_search:資料都在 prompt 裡。方向/日期判斷靠上方注入的真實數字兜底,
        # 只挑重點、輸出短,effort=low 就夠 —— 省思考 token(見 2026-09-18 瘦身)。
        message = sonnet_client.create(
            ANTHROPIC_API_KEY, prompt, max_tokens=1500, effort="low",
        )
        text = ""
        for block in message.content:
            if getattr(block, "type", None) == "text":
                text = block.text
        # 只留 bullet 行：AI 找不到資料時的開場白/結語/「無法找到」散文一律砍掉
        return _strip_to_bullets(text.strip())
    except Exception as e:
        print(f"AI 盤前整理失敗：{e}")
        return ""


def build_premarket_report(force=False):
    """組成盤前報告 HTML 字串；週末回 None（呼叫端會 skip）。force=True 強跑。"""
    if is_weekend() and not force:
        print("週末，盤前報告 skip")
        return None

    intl_lines = [_quote_line(s, l) for s, l in INTL_INDICES + ADR_STOCKS + COMMODITIES]
    chip_data = get_institutional_trades()
    chip_block = _build_chip_block_from(chip_data)
    ai_block = _build_ai_summary(chip_data=chip_data)

    sections = [
        "<b>📊 盤前報告</b>",
        "<b>🌍 國際市場（隔夜）</b>\n" + "\n".join(intl_lines),
        "<b>🏛️ 三大法人買賣超</b>\n" + chip_block,
    ]
    if ai_block:
        sections.append(f"<b>🧠 盤前重點</b>\n{ai_block}")
    return "\n\n".join(sections)
