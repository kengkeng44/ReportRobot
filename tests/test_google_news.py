import stock_news


class _FakeEntry(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


def test_google_news_rss_strips_source_suffix(monkeypatch):
    fake_feed = type("F", (), {"entries": [
        {"title": "颱風逼近北台灣 - 中央社", "link": "http://a",
         "published_parsed": None, "updated_parsed": None},
        {"title": "純標題無來源", "link": "http://b",
         "published_parsed": None, "updated_parsed": None},
    ]})()
    monkeypatch.setattr(stock_news.feedparser, "parse", lambda url: fake_feed)

    out = stock_news._google_news_rss("颱風 天氣", limit=5)

    assert [n["title"] for n in out] == ["颱風逼近北台灣", "純標題無來源"]
    assert out[0]["source"] == "Google News"


def test_get_google_news_delegates_to_rss(monkeypatch):
    captured = {}
    monkeypatch.setattr(stock_news, "_google_news_rss",
                        lambda query, limit=10: captured.setdefault("query", query) or [])
    stock_news.get_google_news("2330", "台積電", limit=3)
    assert "台積電" in captured["query"]
