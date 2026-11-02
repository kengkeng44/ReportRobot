from datetime import date

import hut_info as hi
import hut_onestop
import hut_sources
from hut_sources import Slot


def test_every_site_has_place_and_query_url():
    for site in list(hut_sources.SITES) + list(hut_onestop.ONESTOP):
        assert hi.place(site)
        assert hi.QUERY_URL[site].startswith("https://")


def test_every_place_has_transport_card():
    for site in list(hut_sources.SITES) + list(hut_onestop.ONESTOP):
        assert hi.transport_card(hi.place(site))


def test_unverified_card_warns(monkeypatch):
    monkeypatch.setitem(hi.TRANSPORT, "測試", {"text": "登山口：X", "source": "https://example.gov.tw/", "checked": None})
    card = hi.transport_card("測試")
    assert "尚未核對官方資料" in card
    assert "https://example.gov.tw/" in card


def test_every_card_has_full_lines():
    for name, t in hi.TRANSPORT.items():
        for head in ("登山口：", "開車：", "火車：", "公車："):
            assert head in t["text"], (name, head)
        assert t["source"].startswith("https://")


def test_mianyue_card_exists():
    card = hi.transport_card("眠月線")
    assert "台灣好行阿里山線" in card
    assert "沼平" in card


def test_verified_card_shows_date():
    card = hi.transport_card("嘉明湖／向陽")
    assert "核對）" in card
    assert "尚未核對" not in card


def test_display_and_apply_phrase():
    s = Slot("tconline", "周圍營地大營地4*4", "tent", date(2026, 12, 5), 3, 0)
    assert hi.display(s) == "天池 周圍營地大營地4*4"
    b = Slot("jmlnt", "嘉明湖山屋", "bed", date(2026, 12, 5), 4, 9)
    assert hi.display(b) == "嘉明湖山屋"
    assert hi.apply_phrase(b) == "用 hut-apply 申請 嘉明湖山屋 2026-12-05 2人"
