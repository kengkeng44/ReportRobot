"""
盤前報告（每日 08:00 推、週末略過）：
- 國際指數隔夜收盤（含費半 SOX）
- 重要 ADR 與盤後價（TSMC / NVIDIA）
- 匯率與原物料（USD/TWD、DXY、USD/JPY、油、金）
- 三大法人買賣超
- AI web_search 整理 Fed / 總經 / 地緣 / 類股 / 法說會
"""

import os

import anthropic
import usage_tracker

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
COMMODITIES = [
    ("GC=F", "黃金"),
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
        if stripped.startswith(("•", "・", "-", "*")):
            bullets.append(stripped)
            started = True
        elif started:
            break
    return "\n".join(bullets)


def _build_ai_summary(chip_data=None):
    """用 Claude web_search 整理盤前重點。失敗回空字串。
    chip_data：來自 chips.get_institutional_trades()，把真實數字注入 prompt
    讓 AI 用準確基準寫昨日資金流向。"""
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
            "\n\n[實際三大法人數字 — 請務必引用此真實數字，不要 web_search 拿舊的]\n"
            + " / ".join(parts)
        )

    prompt = (
        f"今天是 {today}（台北時間，嚴格依此判斷「最新」/「昨日」）。\n"
        f"請用網路搜尋整理今日台股開盤前重點。\n"
        f"輸出 6-8 條 bullet，每點 `• ` 開頭，純文字繁體中文，不要 Markdown。\n"
        f"**所有日期一律 YYYY-MM-DD 格式（例 2026-05-07）**，禁用 5/7、05/07、5月7日。"
        f"{chip_block}\n\n"
        f"請涵蓋以下面向（找不到就跳過，不要編造；數據都要附日期）：\n"
        f"1. **昨日台股資金流向**（必寫，使用上方真實數字）：外資 / 投信 / 自營買賣超、"
        f"強勢類股 Top 3 與弱勢類股 Top 3，每個類股要附漲跌幅、帶動的權值股名稱與該股漲跌\n"
        f"2. 美聯準會（Fed）動向：近期談話、會議紀要、利率機率變化\n"
        f"3. 重要經濟數據：近期已公布或本週將公布的 CPI/PPI/非農/PMI/GDP/零售銷售\n"
        f"4. 地緣政治與重大事件：貿易戰、關稅、戰爭、央行政策對股市的影響\n"
        f"5. 重要個股動態：權值股法說、財報、併購、減資（要有具體數字、日期）\n"
        f"6. 今日台股召開法說會的重要公司（如有）\n"
        f"7. 美股盤後/盤前重要科技股漲跌（NVDA/TSM/AAPL/MSFT/AMD 任一有 ±3% 以上才寫，"
        f"並使用最新隔夜收盤數據；務必先 web_search 確認當天實際數字）\n\n"
        f"規則：\n"
        f"- 每點 1-2 句話，要有具體數字 / 公司名 / 日期\n"
        f"- 「昨日資金流向」放第一條，用上方提供的真實數字（重要！）\n"
        f"- 直接列出 bullet，禁止開場白與結語"
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 5,
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        usage_tracker.track("claude-sonnet-4-5", message)
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
