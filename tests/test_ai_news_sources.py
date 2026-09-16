"""盤前重點、天氣活動改成自己抓 RSS:不再帶 web_search,且只把標題餵模型。"""
from datetime import date
from types import SimpleNamespace

import pytest

import premarket
import sonnet_client
import stock_news
import weather

NOW = 1_800_000_000


def _item(title, hours_ago, source="測試媒體"):
    return {"title": title, "link": "https://x", "published": NOW - hours_ago * 3600, "source": source}


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_create(api_key, prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="• 重點一\n• 重點二")])

    monkeypatch.setattr(sonnet_client, "create", fake_create)
    return calls


def test_news_lines_dedupes_filters_old_and_caps(monkeypatch):
    feed = [_item("Fed 升息", 1), _item("Fed升息！", 2), _item("三天前的舊聞", 72)]
    feed += [_item(f"新聞{i}", 3) for i in range(50)]
    monkeypatch.setattr(stock_news, "_google_news_rss", lambda q, limit=10: feed)
    lines = premarket._news_lines(now_ts=NOW)
    assert len(lines) == premarket.NEWS_MAX_ITEMS
    assert sum("Fed" in l for l in lines) == 1          # 標點不同也算同一則
    assert not any("舊聞" in l for l in lines)
    assert lines[0].endswith("Fed 升息（測試媒體）")


def test_premarket_uses_titles_and_no_tools(monkeypatch, captured):
    monkeypatch.setattr(premarket, "_news_lines", lambda: ["- [09-16] 外資賣超台積電（某報）"])
    monkeypatch.setattr(premarket, "get_index_quote", lambda s: None)
    text = premarket._ai_summary_uncached(chip_data=None)
    assert text == "• 重點一\n• 重點二"
    call = captured[0]
    assert "tools" not in call
    assert "外資賣超台積電" in call["prompt"]
    assert "web_search" not in call["prompt"]


def test_premarket_skips_ai_when_no_news(monkeypatch, captured):
    monkeypatch.setattr(premarket, "_news_lines", lambda: [])
    monkeypatch.setattr(premarket, "get_index_quote", lambda s: None)
    assert premarket._ai_summary_uncached(chip_data=None) == ""
    assert captured == []


def test_weather_events_use_haiku_without_tools(monkeypatch, captured):
    queries = []

    def fake_rss(q, limit=10):
        queries.append(q)
        return [_item("淡水藝術節 9/20 登場", 5), _item("淡水藝術節 9/20 登場", 5)]

    monkeypatch.setattr(stock_news, "_google_news_rss", fake_rss)
    out = weather._fetch_local_events(["淡水區"], date(2026, 9, 16))
    assert out == "• 重點一\n• 重點二"
    call = captured[0]
    assert call["model"] == sonnet_client.HAIKU
    assert "tools" not in call
    assert call["prompt"].count("淡水藝術節") == 1       # 重複標題只給一次
    assert all(q.startswith("淡水 ") for q in queries)  # 「淡水區」去掉「區」再搜


def test_weather_events_empty_feed_returns_empty_without_ai(monkeypatch, captured):
    monkeypatch.setattr(stock_news, "_google_news_rss", lambda q, limit=10: [])
    assert weather._fetch_local_events(["淡水區"], date(2026, 9, 16)) == ""
    assert captured == []


def test_clean_event_lines_normalizes_filters_and_dedupes():
    raw = ("• 淡水古蹟日｜09/19|淡水\n"
           "• 淡水古蹟日｜09/19｜淡水老街\n"
           "• 綠光市集｜09/16|金山灣區\n"
           "• 風箏節｜日期見新聞|金山")
    assert weather._clean_event_lines(raw) == "• 淡水古蹟日｜09/19｜淡水\n• 風箏節｜日期見新聞｜金山"
