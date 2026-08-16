from datetime import date, timedelta

import pytest

import humor
import humor_topics


@pytest.fixture(autouse=True)
def _no_notion(monkeypatch):
    """測試一律不碰 Notion:歷史空的、寫入吞掉。要驗歷史的個別測試自己覆蓋。"""
    monkeypatch.setattr(humor, "_recent", lambda kind: [])
    monkeypatch.setattr(humor, "_recent_links", lambda kind: [])
    monkeypatch.setattr(humor, "_remember",
                        lambda kind, text, topic, day, source=None: None)


@pytest.fixture(autouse=True)
def _no_ptt(monkeypatch):
    """預設不打 PTT。要驗論壇路徑的測試自己覆蓋 fetch_ptt_jokes。"""
    monkeypatch.setattr(humor.joke_sources, "fetch_ptt_jokes",
                        lambda **kw: [])


def test_trivia_on_even_yday(monkeypatch):
    # 2026-01-02 是第 2 天(偶數)→ 小知識
    monkeypatch.setattr(humor, "_ai", lambda prompt, max_tokens=300: "太陽很大")
    header, body = humor._trivia_or_joke(date(2026, 1, 2))
    assert header == "💡 今日小知識"
    assert body == "太陽很大"


def test_joke_on_odd_yday(monkeypatch):
    # 2026-01-01 是第 1 天(奇數)→ 笑話
    monkeypatch.setattr(humor, "_ai", lambda prompt, max_tokens=300: "哈哈")
    header, _ = humor._trivia_or_joke(date(2026, 1, 1))
    assert header == "😄 今日一笑"


def test_festival_greeting_none_on_ordinary_day(monkeypatch):
    # 用一個平常日(非國定假日)
    assert humor._festival_greeting(date(2026, 1, 6)) is None


def test_get_daily_extra_assembles_sections(monkeypatch):
    monkeypatch.setattr(humor, "today_tpe", lambda: date(2026, 1, 2))
    monkeypatch.setattr(humor, "_festival_greeting", lambda today: None)
    monkeypatch.setattr(humor, "_trivia_or_joke",
                        lambda today: ("💡 今日小知識", "冷知識內容"))
    monkeypatch.setattr(humor, "_fun_news", lambda today: "今天下雨")

    out = humor.get_daily_extra()

    assert "💡 今日小知識\n冷知識內容" in out
    assert "📰 今日新鮮事\n今天下雨" in out


def test_get_daily_extra_returns_none_when_all_empty(monkeypatch):
    monkeypatch.setattr(humor, "today_tpe", lambda: date(2026, 1, 2))
    monkeypatch.setattr(humor, "_festival_greeting", lambda today: None)
    monkeypatch.setattr(humor, "_trivia_or_joke", lambda today: None)
    monkeypatch.setattr(humor, "_fun_news", lambda today: None)
    assert humor.get_daily_extra() is None


# ── 主題輪替 ──────────────────────────────────────────────

def test_topics_differ_day_to_day():
    """連續兩天必須拿到不同主題 —— 這是不重複的第一道防線。"""
    d1, d2 = date(2026, 3, 1), date(2026, 3, 2)
    assert humor_topics.joke_topic(d1) != humor_topics.joke_topic(d2)
    assert humor_topics.trivia_topic(d1) != humor_topics.trivia_topic(d2)


def test_topics_cover_whole_pool_before_repeating():
    """池子跑完一輪前不該重覆(笑話每兩天才輪到,實際間隔是這裡的兩倍)。"""
    start = date(2026, 1, 1)
    n = len(humor_topics.JOKE_TOPICS)
    seen = {humor_topics.joke_topic(start + timedelta(days=i)) for i in range(n)}
    assert len(seen) == n


def test_same_day_same_topic():
    """同一天重跑要拿到同一個主題(補推、重試時行為才可預期)。"""
    d = date(2026, 5, 20)
    assert humor_topics.joke_topic(d) == humor_topics.joke_topic(d)


def test_offset_switches_topic():
    d = date(2026, 5, 20)
    assert humor_topics.joke_topic(d, 1) != humor_topics.joke_topic(d, 0)


def test_news_query_rotates():
    d1, d2 = date(2026, 4, 1), date(2026, 4, 2)
    assert humor_topics.news_query(d1) != humor_topics.news_query(d2)


# ── 相似度比對 ────────────────────────────────────────────

def test_identical_text_is_duplicate():
    assert humor._too_similar("為什麼企鵝不會飛?因為牠沒有機票。",
                              ["為什麼企鵝不會飛?因為牠沒有機票。"])


def test_punctuation_only_change_is_duplicate():
    """只改標點空白不算新的一則。"""
    assert humor._too_similar("為什麼企鵝不會飛?因為牠沒有機票",
                              ["為什麼企鵝不會飛?因為,牠沒有機票!"])


def test_different_joke_is_not_duplicate():
    assert not humor._too_similar(
        "老闆說要共體時艱,結果他共了體,我時艱。",
        ["為什麼企鵝不會飛?因為牠沒有機票。"])


def test_empty_text_counts_as_duplicate():
    """空字串當無效,免得推出空白段落。"""
    assert humor._too_similar("", ["隨便一則"])


def test_no_history_means_never_duplicate():
    assert not humor._too_similar("任何內容", [])


# ── avoid 區塊 ────────────────────────────────────────────

def test_avoid_block_empty_when_no_history():
    assert humor._avoid_block([]) == ""


def test_avoid_block_lists_history():
    block = humor._avoid_block(["笑話A", "笑話B"])
    assert "1. 笑話A" in block
    assert "2. 笑話B" in block


def test_avoid_block_caps_length():
    block = humor._avoid_block([f"第{i}則" for i in range(50)], limit=3)
    assert "第2則" in block
    assert "第3則" not in block


# ── 重試機制 ──────────────────────────────────────────────

def test_generate_fresh_retries_until_new(monkeypatch):
    """第一次生出舊笑話 → 換主題重試 → 第二次過關。"""
    old = "為什麼企鵝不會飛?因為牠沒有機票。"
    monkeypatch.setattr(humor, "_recent", lambda kind: [old])

    outputs = iter([old, "老闆說共體時艱,結果他共了體,我時艱。"])
    used_topics = []

    def fake_ai(prompt, max_tokens=300):
        return next(outputs)

    monkeypatch.setattr(humor, "_ai", fake_ai)

    def topic_fn(day, offset=0):
        used_topics.append(offset)
        return f"主題{offset}"

    got = humor._generate_fresh("{topic}{avoid}", "笑話", topic_fn,
                                date(2026, 1, 1))

    assert got == "老闆說共體時艱,結果他共了體,我時艱。"
    assert used_topics == [0, 1]  # 確實換了主題才重試


def test_generate_fresh_records_new_content(monkeypatch):
    saved = []
    monkeypatch.setattr(humor, "_recent", lambda kind: [])
    monkeypatch.setattr(humor, "_remember",
                        lambda kind, text, topic, day: saved.append((kind, text)))
    monkeypatch.setattr(humor, "_ai", lambda prompt, max_tokens=300: "新的一則")

    humor._generate_fresh("{topic}{avoid}", "笑話",
                          lambda day, offset=0: "主題", date(2026, 1, 1))

    assert saved == [("笑話", "新的一則")]


def test_generate_fresh_falls_back_after_max_attempts(monkeypatch):
    """三次都撞到重複 → 還是回內容(推舊的好過整段消失),而且只呼叫三次。"""
    old = "同一則老笑話"
    monkeypatch.setattr(humor, "_recent", lambda kind: [old])
    calls = []

    def fake_ai(prompt, max_tokens=300):
        calls.append(prompt)
        return old

    monkeypatch.setattr(humor, "_ai", fake_ai)

    got = humor._generate_fresh("{topic}{avoid}", "笑話",
                                lambda day, offset=0: f"主題{offset}",
                                date(2026, 1, 1))

    assert got == old
    assert len(calls) == humor.MAX_ATTEMPTS


def test_generate_fresh_passes_history_into_prompt(monkeypatch):
    """歷史必須真的出現在送出去的 prompt 裡,否則模型根本不知道要避開。"""
    monkeypatch.setattr(humor, "_recent", lambda kind: ["講過的舊笑話"])
    seen = {}

    def fake_ai(prompt, max_tokens=300):
        seen["prompt"] = prompt
        return "全新的內容"

    monkeypatch.setattr(humor, "_ai", fake_ai)
    humor._generate_fresh("題目:{topic}{avoid}", "笑話",
                          lambda day, offset=0: "主題", date(2026, 1, 1))

    assert "講過的舊笑話" in seen["prompt"]


# ── 今日新鮮事 ────────────────────────────────────────────

def test_fun_news_filters_seen_titles(monkeypatch):
    """講過的新聞不該再送進 AI 挑選。"""
    monkeypatch.setattr(humor, "_recent", lambda kind: ["颱風明天登陸台灣東部"])
    monkeypatch.setattr(humor, "_google_news_rss",
                        lambda q, limit=8: [{"title": "颱風明天登陸台灣東部"},
                                            {"title": "科學家發現新種深海魚"}])
    seen = {}

    def fake_ai(prompt, max_tokens=300):
        seen["prompt"] = prompt
        return "有人發現新魚"

    monkeypatch.setattr(humor, "_ai", fake_ai)
    humor._fun_news(date(2026, 1, 1))

    # prompt 前半是候選清單、後半是 avoid 區塊(講過的本來就該列在那)
    candidates = seen["prompt"].split("最近已經講過")[0]
    assert "科學家發現新種深海魚" in candidates
    assert "颱風明天登陸台灣東部" not in candidates


def test_fun_news_keeps_all_when_everything_seen(monkeypatch):
    """全部都講過時退回原清單,不要讓這段開天窗。"""
    monkeypatch.setattr(humor, "_recent", lambda kind: ["颱風明天登陸台灣東部"])
    monkeypatch.setattr(humor, "_google_news_rss",
                        lambda q, limit=8: [{"title": "颱風明天登陸台灣東部"}])
    monkeypatch.setattr(humor, "_ai", lambda prompt, max_tokens=300: "颱風要來了")

    assert humor._fun_news(date(2026, 1, 1)) == "颱風要來了"


def test_fun_news_none_when_no_items(monkeypatch):
    monkeypatch.setattr(humor, "_google_news_rss", lambda q, limit=8: [])
    assert humor._fun_news(date(2026, 1, 1)) is None


# ── 論壇笑話:挑選結果解析 ─────────────────────────────────

def test_parse_pick_reads_index():
    idx, body = humor._parse_pick("#3\n劉備怎麼死的\n備 害死的")
    assert idx == 2                      # #3 是第 3 則,轉成 0-based
    assert body == "劉備怎麼死的\n備 害死的"


def test_parse_pick_tolerates_missing_index():
    """沒有編號也要拿得到笑話 —— 編號只是用來記來源,不值得為它丟掉內容。"""
    idx, body = humor._parse_pick("劉備怎麼死的\n備 害死的")
    assert idx is None
    assert body == "劉備怎麼死的\n備 害死的"


def test_parse_pick_accepts_punctuation_after_number():
    idx, body = humor._parse_pick("3.\n笑話內容")
    assert idx == 2
    assert body == "笑話內容"


# ── 論壇笑話:整條路徑 ─────────────────────────────────────

def _fake_jokes():
    return [
        {"title": "[猜謎] 甲", "body": "甲的內文", "heat": 50,
         "link": "https://www.ptt.cc/a"},
        {"title": "[耍冷] 乙", "body": "乙的內文", "heat": 30,
         "link": "https://www.ptt.cc/b"},
    ]


def test_forum_joke_returns_picked_body(monkeypatch):
    monkeypatch.setattr(humor.joke_sources, "fetch_ptt_jokes",
                        lambda **kw: _fake_jokes())
    monkeypatch.setattr(humor, "_ai",
                        lambda prompt, max_tokens=300: "#2\n乙的笑話整理版")

    assert humor._joke_from_forum(date(2026, 1, 1)) == "乙的笑話整理版"


def test_forum_joke_records_source_link(monkeypatch):
    """要記下挑中的是哪一篇,否則明天可能又挑到同一篇。"""
    saved = []
    monkeypatch.setattr(humor.joke_sources, "fetch_ptt_jokes",
                        lambda **kw: _fake_jokes())
    monkeypatch.setattr(humor, "_ai",
                        lambda prompt, max_tokens=300: "#2\n乙的笑話整理版")
    monkeypatch.setattr(humor, "_remember",
                        lambda kind, text, topic, day, source=None: saved.append(source))

    humor._joke_from_forum(date(2026, 1, 1))
    assert saved == ["https://www.ptt.cc/b"]


def test_forum_joke_none_when_ai_rejects_all(monkeypatch):
    """AI 判定全部不合格(性暗示等)就回 NONE,不能硬推。"""
    monkeypatch.setattr(humor.joke_sources, "fetch_ptt_jokes",
                        lambda **kw: _fake_jokes())
    monkeypatch.setattr(humor, "_ai", lambda prompt, max_tokens=300: "NONE")

    assert humor._joke_from_forum(date(2026, 1, 1)) is None


def test_forum_joke_none_when_ptt_empty(monkeypatch):
    monkeypatch.setattr(humor.joke_sources, "fetch_ptt_jokes", lambda **kw: [])
    assert humor._joke_from_forum(date(2026, 1, 1)) is None


def test_forum_joke_none_when_ptt_raises(monkeypatch):
    """PTT 掛掉不能讓整段炸掉 —— 呼叫端要能 fallback 到 AI 生成。"""
    def boom(**kw):
        raise RuntimeError("PTT 連線失敗")
    monkeypatch.setattr(humor.joke_sources, "fetch_ptt_jokes", boom)
    assert humor._joke_from_forum(date(2026, 1, 1)) is None


def test_forum_joke_rejects_duplicate(monkeypatch):
    monkeypatch.setattr(humor.joke_sources, "fetch_ptt_jokes",
                        lambda **kw: _fake_jokes())
    monkeypatch.setattr(humor, "_ai",
                        lambda prompt, max_tokens=300: "#1\n講過的老笑話")
    monkeypatch.setattr(humor, "_recent", lambda kind: ["講過的老笑話"])

    assert humor._joke_from_forum(date(2026, 1, 1)) is None


def test_forum_joke_excludes_used_links(monkeypatch):
    """已推播過的文章連結要傳給抓取器排除。"""
    got = {}
    monkeypatch.setattr(humor, "_recent_links",
                        lambda kind: ["https://www.ptt.cc/old"])
    monkeypatch.setattr(humor.joke_sources, "fetch_ptt_jokes",
                        lambda **kw: got.update(kw) or _fake_jokes())
    monkeypatch.setattr(humor, "_ai",
                        lambda prompt, max_tokens=300: "#1\n甲的笑話")

    humor._joke_from_forum(date(2026, 1, 1))
    assert got["exclude_links"] == ["https://www.ptt.cc/old"]


# ── 笑話日:論壇優先,沒料才生成 ───────────────────────────

def test_joke_day_prefers_forum(monkeypatch):
    monkeypatch.setattr(humor, "_joke_from_forum", lambda today: "論壇的梗")
    monkeypatch.setattr(humor, "_generate_fresh",
                        lambda *a, **k: pytest.fail("論壇有料時不該呼叫 AI 生成"))

    header, body = humor._trivia_or_joke(date(2026, 1, 1))  # 奇數日 = 笑話
    assert header == "😄 今日一笑"
    assert body == "論壇的梗"


def test_joke_day_falls_back_to_generation(monkeypatch):
    monkeypatch.setattr(humor, "_joke_from_forum", lambda today: None)
    monkeypatch.setattr(humor, "_ai",
                        lambda prompt, max_tokens=300: "AI 生的笑話")

    header, body = humor._trivia_or_joke(date(2026, 1, 1))
    assert header == "😄 今日一笑"
    assert body == "AI 生的笑話"


def test_trivia_day_never_touches_forum(monkeypatch):
    """小知識不從笑話板撈,免得白花一次 PTT 抓取。"""
    monkeypatch.setattr(humor, "_joke_from_forum",
                        lambda today: pytest.fail("小知識日不該碰論壇"))
    monkeypatch.setattr(humor, "_ai", lambda prompt, max_tokens=300: "冷知識")

    header, _ = humor._trivia_or_joke(date(2026, 1, 2))  # 偶數日 = 小知識
    assert header == "💡 今日小知識"
