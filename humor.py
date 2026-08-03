"""每日「今日一則」內容:小知識/笑話(依日期輪流)+ 節日祝福 + 每日新鮮事。

對外單一入口 get_daily_extra() -> str | None,回傳可直接餵給
flex_builder.text_bubble 的 body(段落以 \n\n 分隔,每段首行為次標)。
任一子區塊失敗只略過該段,不整卡失敗;全部失敗回 None。
"""

import os

import anthropic
import holidays

import usage_tracker
from prompts import (
    DAILY_TRIVIA_PROMPT, DAILY_JOKE_PROMPT,
    FESTIVAL_GREETING_PROMPT, FUN_NEWS_PROMPT,
)
from stock_news import _google_news_rss
from tz_utils import today_tpe


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    import config
    return getattr(config, name)


AI_MODEL = "claude-sonnet-4-5"


def _ai(prompt, max_tokens=300):
    # 金鑰在此處才讀(lazy):讓 import humor 在無金鑰環境也不炸,方便測試
    client = anthropic.Anthropic(api_key=_env("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=AI_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    usage_tracker.track(AI_MODEL, message)
    return message.content[0].text.strip()


def _festival_greeting(today):
    """今天是台灣節日就回一句祝福,否則 None。"""
    try:
        tw = holidays.Taiwan(years=today.year)
        name = tw.get(today)
        if not name:
            return None
        return _ai(FESTIVAL_GREETING_PROMPT.format(festival=name))
    except Exception as e:
        print(f"節日祝福失敗:{e}")
        return None


def _trivia_or_joke(today):
    """依當年第幾天單雙數決定小知識/笑話。回 (header, body) 或 None。"""
    is_trivia = today.timetuple().tm_yday % 2 == 0
    prompt = DAILY_TRIVIA_PROMPT if is_trivia else DAILY_JOKE_PROMPT
    header = "💡 今日小知識" if is_trivia else "😄 今日一笑"
    try:
        return header, _ai(prompt)
    except Exception as e:
        print(f"小知識/笑話失敗:{e}")
        return None


def _fun_news():
    """搜天氣/颱風新聞,AI 挑一則講重點。無新聞回 None。"""
    items = _google_news_rss("颱風 天氣", limit=8)
    if not items:
        items = _google_news_rss("台灣 生活", limit=8)
    if not items:
        return None
    titles = "\n".join(f"{i+1}. {it['title']}"
                       for i, it in enumerate(items) if it.get("title"))
    if not titles:
        return None
    try:
        return _ai(FUN_NEWS_PROMPT.format(titles=titles))
    except Exception as e:
        print(f"每日新鮮事失敗:{e}")
        return None


def get_daily_extra():
    today = today_tpe()
    sections = []

    greeting = _festival_greeting(today)
    if greeting:
        sections.append(f"🎉 節日快樂\n{greeting}")

    tj = _trivia_or_joke(today)
    if tj:
        header, body = tj
        sections.append(f"{header}\n{body}")

    news = _fun_news()
    if news:
        sections.append(f"📰 今日新鮮事\n{news}")

    if not sections:
        return None
    return "\n\n".join(sections)
