"""每日「今日一則」內容:小知識/笑話(依日期輪流)+ 節日祝福 + 每日新鮮事。

對外單一入口 get_daily_extra() -> str | None,回傳可直接餵給
flex_builder.text_bubble 的 body(段落以 \n\n 分隔,每段首行為次標)。
任一子區塊失敗只略過該段,不整卡失敗;全部失敗回 None。

不重複的三道防線(缺一都還是會開始重複):
1. 主題輪替 —— humor_topics 每天給不同主題,prompt 每天長得不一樣
2. avoid 清單 —— 把最近講過的塞進 prompt 叫模型避開
3. 相似度比對 —— 生出來還是像舊的就換主題重試(見 _generate_fresh)

Notion 不可用時只有第 1 道還在,產出仍然正常,只是防重複變弱。
"""

import difflib
import os
import re

import anthropic
import holidays

import humor_topics
import joke_sources
import usage_tracker
from prompts import (
    AVOID_REPEAT_BLOCK, DAILY_TRIVIA_PROMPT, DAILY_JOKE_PROMPT,
    FESTIVAL_GREETING_PROMPT, FUN_NEWS_PROMPT, JOKE_PICK_PROMPT,
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

# 從 Notion 撈幾則歷史來比對
HISTORY_LIMIT = 30
# 來源連結要記得更久:內容比對擋不掉「同一篇 PTT 文換句話整理」
LINK_HISTORY_LIMIT = 100
# 每次翻幾頁 PTT、挑幾則候選送給 AI 篩
FORUM_PAGES = 10
FORUM_CANDIDATES = 12
# 其中前幾則才塞進 prompt(全塞會灌爆 token,更早的交給本地比對擋)
AVOID_IN_PROMPT = 12
# 撞到重複時最多換幾次主題重生
MAX_ATTEMPTS = 3
# difflib 相似度多少算「同一則」。0.62 是抓「換句話說的同一個梗」,
# 調更高會放過改寫版,調更低會誤殺同主題但不同梗的內容。
SIMILARITY_THRESHOLD = 0.62

# \W 在 Python3 預設吃 Unicode,中文字算 \w,所以這只會清掉標點與空白
_PUNCT = re.compile(r"\W+")


def _ai(prompt, max_tokens=300):
    # 金鑰在此處才讀(lazy):讓 import humor 在無金鑰環境也不炸,方便測試
    client = anthropic.Anthropic(api_key=_env("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=AI_MODEL,
        max_tokens=max_tokens,
        temperature=1.0,  # 明示要多樣性,不要之後有人手癢調低
        messages=[{"role": "user", "content": prompt}],
    )
    usage_tracker.track(AI_MODEL, message)
    return message.content[0].text.strip()


# ── 去重工具 ──────────────────────────────────────────────

def _normalize(text):
    """比對前把標點、空白、大小寫差異抹掉,只留字。"""
    return _PUNCT.sub("", text or "").lower()


def _too_similar(text, history):
    """text 是否和歷史裡任何一則實質相同。空字串一律視為無效。"""
    a = _normalize(text)
    if not a:
        return True
    for old in history:
        b = _normalize(old)
        if not b:
            continue
        if a == b:
            return True
        if difflib.SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD:
            return True
    return False


def _recent(kind):
    """撈某類型最近講過的內容。Notion 沒設定或失敗都回 []。"""
    try:
        import notion_db
        if not notion_db.is_configured():
            return []
        return notion_db.daily_extra_recent(kind, HISTORY_LIMIT)
    except Exception as e:
        print(f"[humor] 讀歷史失敗:{e}")
        return []


def _recent_links(kind):
    """撈某類型最近用過的來源連結。同一篇論壇文章不要挑第二次。"""
    try:
        import notion_db
        if not notion_db.is_configured():
            return []
        return notion_db.daily_extra_recent_links(kind, LINK_HISTORY_LIMIT)
    except Exception as e:
        print(f"[humor] 讀來源歷史失敗:{e}")
        return []


def _remember(kind, text, topic, day, source=None):
    """記下今天講了什麼。寫失敗不影響已經生出來的內容。"""
    try:
        import notion_db
        if not notion_db.is_configured():
            return
        notion_db.daily_extra_add(kind, text, topic, day, source)
    except Exception as e:
        print(f"[humor] 寫歷史失敗:{e}")


def _avoid_block(history, limit=AVOID_IN_PROMPT):
    """組 prompt 裡的「別再講這些」。沒有歷史就回空字串(整段不附)。"""
    items = [h for h in history[:limit] if h]
    if not items:
        return ""
    listed = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(items))
    return AVOID_REPEAT_BLOCK.format(recent=listed)


def _generate_fresh(prompt_tmpl, kind, topic_fn, day):
    """生一則和歷史不重複的內容。撞到就換主題重試,回字串或 None。"""
    history = _recent(kind)
    last = None

    for attempt in range(MAX_ATTEMPTS):
        topic = topic_fn(day, attempt)
        text = _ai(prompt_tmpl.format(topic=topic, avoid=_avoid_block(history)))
        if not text:
            continue
        last = (text, topic)
        if not _too_similar(text, history):
            _remember(kind, text, topic, day)
            return text
        print(f"[humor] {kind}「{topic}」與歷史重複,換主題重試({attempt + 1}/{MAX_ATTEMPTS})")
        # 把這次的也算進歷史,避免下一輪又生同一則
        history = [text] + list(history)

    # 全部撞牆:推一則舊的還是比整段消失好,但一樣記錄下來讓下次能避開
    if last:
        text, topic = last
        print(f"[humor] {kind} 連續 {MAX_ATTEMPTS} 次重複,仍採用最後一次")
        _remember(kind, text, topic, day)
        return text
    return None


# ── 各區塊 ────────────────────────────────────────────────

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


def _parse_pick(text):
    """拆 AI 回覆的「#編號 + 笑話」。沒有編號就當作全文都是笑話。

    容錯而不是 raise:編號只是用來記來源連結,解析失敗頂多少記一筆,
    不值得為此丟掉一則已經挑好的笑話。
    """
    m = re.match(r"\s*#?\s*(\d+)\s*[\.\)、]?\s*\n", text)
    if not m:
        return None, text.strip()
    return int(m.group(1)) - 1, text[m.end():].strip()


def _joke_from_forum(today):
    """從 PTT joke 板挑一則。撈不到或 AI 認為全都不合格就回 None。"""
    try:
        jokes = joke_sources.fetch_ptt_jokes(
            pages=FORUM_PAGES, limit=FORUM_CANDIDATES,
            exclude_links=_recent_links("笑話"))
    except Exception as e:
        print(f"[humor] PTT 笑話抓取失敗:{e}")
        return None
    if not jokes:
        print("[humor] PTT 沒撈到可用的笑話")
        return None

    listed = "\n\n".join(
        f"{i + 1}. ({j['heat']} 推) {j['title']}\n{j['body']}"
        for i, j in enumerate(jokes))
    raw = _ai(JOKE_PICK_PROMPT.format(candidates=listed), max_tokens=500)
    if not raw or raw.strip().upper().startswith("NONE"):
        print("[humor] PTT 候選全被 AI 判定不合格")
        return None

    idx, body = _parse_pick(raw)
    if not body or body.upper() == "NONE":
        return None

    history = _recent("笑話")
    if _too_similar(body, history):
        print("[humor] 論壇笑話與歷史重複,退回 AI 生成")
        return None

    link = jokes[idx]["link"] if idx is not None and 0 <= idx < len(jokes) else None
    _remember("笑話", body, "PTT joke", today, source=link)
    return body


def _trivia_or_joke(today):
    """依當年第幾天單雙數決定小知識/笑話。回 (header, body) 或 None。

    笑話優先從 PTT 撈真人寫的梗(諧音梗多、有推文數背書),
    撈不到或全被守門擋掉才退回 AI 生成。
    """
    if today.timetuple().tm_yday % 2 == 0:
        header, kind = "💡 今日小知識", "小知識"
        prompt, topic_fn = DAILY_TRIVIA_PROMPT, humor_topics.trivia_topic
    else:
        header, kind = "😄 今日一笑", "笑話"
        prompt, topic_fn = DAILY_JOKE_PROMPT, humor_topics.joke_topic

    try:
        if kind == "笑話":
            body = _joke_from_forum(today) or _generate_fresh(
                prompt, kind, topic_fn, today)
        else:
            body = _generate_fresh(prompt, kind, topic_fn, today)
        return (header, body) if body else None
    except Exception as e:
        print(f"小知識/笑話失敗:{e}")
        return None


def _fun_news(today):
    """依當日搜尋詞抓新聞,AI 挑一則講重點。無新聞回 None。

    搜尋詞每天輪替 + 濾掉講過的標題:原本固定搜「颱風 天氣」,
    沒颱風的日子會反覆挑到同一批新聞。
    """
    query = humor_topics.news_query(today)
    items = _google_news_rss(query, limit=8)
    if not items:
        query = humor_topics.NEWS_FALLBACK_QUERY
        items = _google_news_rss(query, limit=8)
    if not items:
        return None

    history = _recent("新鮮事")
    titled = [it for it in items if it.get("title")]
    # 先把和講過的內容重疊的標題挑掉,讓 AI 只在沒講過的裡面選
    fresh = [it for it in titled if not _too_similar(it["title"], history)]
    picked = fresh or titled  # 全被濾掉就退回全部,寧可重複也不要開天窗
    if not picked:
        return None

    titles = "\n".join(f"{i + 1}. {it['title']}" for i, it in enumerate(picked))
    try:
        text = _ai(FUN_NEWS_PROMPT.format(
            titles=titles, avoid=_avoid_block(history, limit=10)))
    except Exception as e:
        print(f"每日新鮮事失敗:{e}")
        return None

    if text:
        _remember("新鮮事", text, query, today)
    return text


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

    news = _fun_news(today)
    if news:
        sections.append(f"📰 今日新鮮事\n{news}")

    if not sections:
        return None
    return "\n\n".join(sections)
