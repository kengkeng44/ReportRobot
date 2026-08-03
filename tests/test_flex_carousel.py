import flex_builder


def _titles(msg):
    """從 carousel/bubble message 抓所有 bubble 的 header 標題文字。"""
    contents = msg["contents"]
    bubbles = contents["contents"] if contents.get("type") == "carousel" else [contents]
    out = []
    for b in bubbles:
        for el in b["header"]["contents"]:
            if el.get("type") == "text":
                out.append(el["text"])
                break
    return out


def test_extra_bubble_is_first(monkeypatch):
    msg = flex_builder.daily_report_carousel(
        extra_text="💡 今日小知識\n冷知識",
        weather_text="晴天",
        premarket_text="盤前內容",
        today_str="2026-08-03",
    )
    titles = _titles(msg)
    assert titles[0] == "💫 今日一則"
    assert "🌤️ 天氣報告" in titles
    assert "📊 盤前報告" in titles


def test_no_extra_still_works(monkeypatch):
    msg = flex_builder.daily_report_carousel(
        extra_text=None,
        weather_text="晴天",
        premarket_text=None,
        today_str="2026-08-03",
    )
    assert "🌤️ 天氣報告" in _titles(msg)
