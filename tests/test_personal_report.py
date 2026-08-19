"""個人版每日推播(食材 + 板橋天氣 + 最新消費,推到本人 1 對 1)。

群組版與個人版的分工是刻意的:財務只走個人版,不進群組。
"""

import json

import pytest

import daily_report
import flex_builder
import weather


def _dump(msg):
    return json.dumps(msg, ensure_ascii=False)


def _bubbles(msg):
    body = msg["contents"]
    return body["contents"] if body.get("type") == "carousel" else [body]


# ── 卡片組裝 ──────────────────────────────────────────────

def test_carousel_has_all_three_sections():
    msg = flex_builder.personal_report_carousel(
        "2026-08-19",
        weather_text="板橋今天多雲",
        kitchen_text="高麗菜再兩天到期",
        spending_text="8/18 全聯 NT$361",
    )
    text = _dump(msg)
    assert len(_bubbles(msg)) == 3
    assert "🥬 食材提醒" in text
    assert "🌤️ 天氣報告" in text
    assert "💳 最新消費" in text


def test_kitchen_comes_first():
    """有時效、今天就要動手的事排最前面。"""
    msg = flex_builder.personal_report_carousel(
        "2026-08-19", weather_text="晴", kitchen_text="快過期", spending_text="花了錢")
    assert "🥬" in _dump(_bubbles(msg)[0])


def test_sections_are_optional():
    """沒食材要過期的日子就少一張卡,不放「今天沒事」的空卡。"""
    msg = flex_builder.personal_report_carousel(
        "2026-08-19", weather_text="晴", spending_text="花了錢")
    assert len(_bubbles(msg)) == 2
    assert "🥬 食材提醒" not in _dump(msg)


def test_returns_none_when_everything_empty():
    assert flex_builder.personal_report_carousel("2026-08-19") is None


def test_no_placeholder_when_weather_fails():
    """群組版會放「天氣暫時無法取得」佔位,個人版寧可安靜。"""
    msg = flex_builder.personal_report_carousel(
        "2026-08-19", weather_text=None, spending_text="花了錢")
    assert "無法取得" not in _dump(msg)


def test_group_version_never_carries_spending():
    """財務不進群組 —— 這是分兩個目標的主要理由。"""
    msg = flex_builder.daily_report_carousel(
        "今日一則", "天氣", "盤前", "2026-08-19", kitchen_text="食材")
    assert "💳" not in _dump(msg)


# ── 推播流程 ──────────────────────────────────────────────

@pytest.fixture
def _sent(monkeypatch):
    box = []
    monkeypatch.setattr(daily_report, "push_to_user_sync",
                        lambda uid, msg: box.append((uid, msg)))
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "U-me")
    monkeypatch.setattr(daily_report, "get_weather_report",
                        lambda locations=None: ("板橋天氣", None))
    monkeypatch.setattr(daily_report, "_spending_recent", lambda: "最新消費內容")
    return box


def test_personal_push_goes_to_admin(_sent):
    daily_report._push_personal_report("2026-08-19", "食材文字", [], 0)
    assert len(_sent) == 1
    uid, msg = _sent[0]
    assert uid == "U-me"
    assert "板橋天氣" in _dump(msg)
    assert "最新消費內容" in _dump(msg)


def test_personal_push_uses_banqiao_locations(monkeypatch, _sent):
    """個人版天氣必須用板橋,不能沿用群組版的淡水金山。"""
    got = {}
    monkeypatch.setattr(daily_report, "get_weather_report",
                        lambda locations=None: got.update(loc=locations) or ("天氣", None))

    daily_report._push_personal_report("2026-08-19", None, [], 0)
    assert got["loc"] == weather.PERSONAL_WEATHER_LOCATIONS
    assert "板橋區" in got["loc"]


def test_personal_push_skipped_without_admin_id(monkeypatch, _sent):
    monkeypatch.delenv("ADMIN_LINE_USER_ID", raising=False)
    daily_report._push_personal_report("2026-08-19", "食材", [], 0)
    assert _sent == []


def test_personal_push_skipped_when_nothing_to_say(monkeypatch, _sent):
    """三段都沒內容就不要推一則空卡浪費配額。"""
    monkeypatch.setattr(daily_report, "get_weather_report",
                        lambda locations=None: (None, None))
    monkeypatch.setattr(daily_report, "_spending_recent", lambda: None)
    daily_report._push_personal_report("2026-08-19", None, [], 0)
    assert _sent == []


def test_personal_weather_failure_does_not_kill_the_rest(monkeypatch, _sent):
    """天氣炸了還是要把食材和消費推出去。"""
    def boom(locations=None):
        raise RuntimeError("CWA 掛了")
    monkeypatch.setattr(daily_report, "get_weather_report", boom)
    monkeypatch.setattr(daily_report, "notify_admin", lambda *a, **k: None)

    daily_report._push_personal_report("2026-08-19", "食材文字", [], 0)

    assert len(_sent) == 1
    assert "最新消費內容" in _dump(_sent[0][1])


# ── 天氣模組可指定地點 ────────────────────────────────────

def test_weather_defaults_to_group_locations(monkeypatch):
    got = {}
    monkeypatch.setattr(weather.http_utils, "get",
                        lambda url, **kw: got.update(kw.get("params", {})) or _FakeResp())
    weather.get_cwa_weather()
    assert got["LocationName"] == ",".join(weather.WEATHER_LOCATIONS)


def test_weather_accepts_explicit_locations(monkeypatch):
    """這裡曾經有個陷阱:函式內部把 API 回傳的清單也叫 locations,
    參數同名會在中途被蓋掉,變成傳了板橋卻拿到淡水金山。"""
    got = {}
    monkeypatch.setattr(weather.http_utils, "get",
                        lambda url, **kw: got.update(kw.get("params", {})) or _FakeResp())
    weather.get_cwa_weather(["板橋區"])
    assert got["LocationName"] == "板橋區"


def test_owm_knows_banqiao():
    """板橋要有座標,不然個人版只剩中央氣象署單一來源。"""
    import inspect
    src = inspect.getsource(weather.get_owm_weather)
    assert "板橋區" in src


class _FakeResp:
    def json(self):
        return {"records": {"Locations": [{"Location": []}]}}
