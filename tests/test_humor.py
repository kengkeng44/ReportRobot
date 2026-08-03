from datetime import date
import humor


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
    monkeypatch.setattr(humor, "_fun_news", lambda: "今天下雨")

    out = humor.get_daily_extra()

    assert "💡 今日小知識\n冷知識內容" in out
    assert "📰 今日新鮮事\n今天下雨" in out


def test_get_daily_extra_returns_none_when_all_empty(monkeypatch):
    monkeypatch.setattr(humor, "today_tpe", lambda: date(2026, 1, 2))
    monkeypatch.setattr(humor, "_festival_greeting", lambda today: None)
    monkeypatch.setattr(humor, "_trivia_or_joke", lambda today: None)
    monkeypatch.setattr(humor, "_fun_news", lambda: None)
    assert humor.get_daily_extra() is None
