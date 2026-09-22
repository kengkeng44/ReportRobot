"""
盤前報告（每日 08:00 推、週末略過）：
※ 整份報告目前用 PREMARKET_ENABLED 總開關暫停（預設關閉）；設成 1 才會推。

- 國際指數隔夜收盤（含費半 SOX）
- 重要 ADR 與盤後價（TSMC / NVIDIA）
- 匯率與原物料（USD/TWD、DXY、USD/JPY、油、金）
- 三大法人買賣超
- AI 根據 Google News 標題整理 Fed / 總經 / 地緣 / 類股 / 法說會
  （唯一花 token 的段，用 PREMARKET_AI_ENABLED 開關；預設關閉省 token）
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


def _flag_on(name):
    return _env(name).strip().lower() in ("1", "true", "yes", "on")


def _report_enabled():
    """整份盤前報告的總開關（PREMARKET_ENABLED，預設關閉暫停）。
    關閉時 build_premarket_report 直接回 None：每日排程不推、按「盤前」按鈕也不出。
    要開回來把它設成 1 / true / yes / on 即可（免改程式、免重部署）。"""
    return _flag_on("PREMARKET_ENABLED")


def _ai_summary_enabled():
    """「🧠 盤前重點」是這份報告裡唯一花 token 的段（Claude API）。
    用 PREMARKET_AI_ENABLED 開關暫時停掉省錢，預設關閉；
    要開回來把它設成 1 / true / yes / on 即可（免改程式、免重部署）。"""
    return _flag_on("PREMARKET_AI_ENABLED")


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
NEWS_PER_QUERY = 6
NEWS_MAX_AGE_HOURS = 48
NEWS_MAX_ITEMS = 30


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
        f"輸出 6-8 條 bullet，每點 `• ` 開頭，純文字繁體中文，不要 Markdown。\n"
        f"**所有日期一律 YYYY-MM-DD 格式（例 2026-05-07）**，禁用 5/7、05/07、5月7日。"
        f"{chip_block}"
        f"{quote_block}"
        f"{news_block}\n\n"
        f"請涵蓋以下面向（標題裡沒有就跳過，不要編造；數據都要附日期）：\n"
        f"1. **昨日台股資金流向**（必寫，使用上方真實數字）：外資 / 投信 / 自營買賣超、"
        f"強勢類股 Top 3 與弱勢類股 Top 3，每個類股要附漲跌幅、帶動的權值股名稱與該股漲跌\n"
        f"2. 美聯準會（Fed）動向：近期談話、會議紀要、利率機率變化\n"
        f"3. 重要經濟數據：近期已公布或本週將公布的 CPI/PPI/非農/PMI/GDP/零售銷售\n"
        f"4. 地緣政治與重大事件：貿易戰、關稅、戰爭、央行政策對股市的影響\n"
        f"5. 重要個股動態：權值股法說、財報、併購、減資（要有具體數字、日期）\n"
        f"6. 今日台股召開法說會的重要公司（如有）\n"
        f"7. 美股盤後/盤前重要科技股漲跌：**Nasdaq/費半/TSMC/黃金等一律引用上方[實際昨夜美股/"
        f"原物料收盤]的真實數字**；其他個股只寫新聞標題裡明確出現的內容。\n\n"
        f"規則：\n"
        f"- 每點 1-2 句話，盡量附具體數字 / 公司名 / 日期（標題裡有才寫）\n"
        f"- 「昨日資金流向」放第一條，用上方提供的真實數字（重要！）\n"
        f"- **新聞標題與上方真實數字方向或數值衝突時, 一律以真實數字為準**\n"
        f"- 只能使用上方提供的資訊，禁止補充標題裡沒有的數字或事件\n"
        f"- 直接列出 bullet，禁止開場白與結語"
    )
    try:
        # 不給 web_search:資料都在 prompt 裡。要判斷方向、交叉比對數字,留 Sonnet 5 + medium
        message = sonnet_client.create(
            ANTHROPIC_API_KEY, prompt, max_tokens=6000, effort="medium",
            label="盤前重點",
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
    """組成盤前報告 HTML 字串；週末回 None（呼叫端會 skip）。force=True 強跑。

    整份報告用 PREMARKET_ENABLED 總開關暫停中（預設關閉）：關閉時一律回 None，
    連 force=True（按「盤前」按鈕）也不出，要開回來設 PREMARKET_ENABLED=1。"""
    if not _report_enabled():
        print("盤前報告總開關關閉（PREMARKET_ENABLED 未開），skip")
        return None
    if is_weekend() and not force:
        print("週末，盤前報告 skip")
        return None

    intl_lines = [_quote_line(s, l) for s, l in INTL_INDICES + ADR_STOCKS + COMMODITIES]
    chip_data = get_institutional_trades()
    chip_block = _build_chip_block_from(chip_data)
    # AI 盤前重點暫時停掉省 token（PREMARKET_AI_ENABLED 開關，預設關）。
    # 關閉時完全不呼叫 Claude API，只留免費的國際市場 / 三大法人兩段。
    ai_block = _build_ai_summary(chip_data=chip_data) if _ai_summary_enabled() else ""

    sections = [
        "<b>📊 盤前報告</b>",
        "<b>🌍 國際市場（隔夜）</b>\n" + "\n".join(intl_lines),
        "<b>🏛️ 三大法人買賣超</b>\n" + chip_block,
    ]
    if ai_block:
        sections.append(f"<b>🧠 盤前重點</b>\n{ai_block}")
    return "\n\n".join(sections)
