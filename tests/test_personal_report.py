"""個人版每日報(食材 + 板橋天氣 + 最新消費,寄給自己)。

群組版與個人版的分工是刻意的:群組不該看到別人的冰箱和帳單,
但自己每天要看是另一回事。

2026-08-20 個人版從 LINE 1 對 1 推播改成 Gmail(見 mailer.py)——
push 每月只有 200 則,不該有一半花在自己身上。
下面「卡片組裝」那段測的 flex_builder.personal_report_carousel 目前
沒有呼叫端,刻意留著:要改回 LINE 推播時直接接回去就好。
"""

import json

import pytest

import daily_report
import flex_builder
import mailer
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


def test_group_carousel_cannot_carry_private_stuff():
    """財務與食材不進群組 —— 這是分兩個目標的主要理由。"""
    import inspect

    params = inspect.signature(flex_builder.daily_report_carousel).parameters
    assert not [p for p in params if "spend" in p.lower() or "kitchen" in p.lower()]


# ── 寄信流程 ──────────────────────────────────────────────

@pytest.fixture
def _mailed(monkeypatch):
    box = []
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email",
                        lambda subject, body: box.append((subject, body)) or True)
    monkeypatch.setattr(daily_report, "get_weather_report",
                        lambda locations=None: ("板橋天氣", None))
    monkeypatch.setattr(daily_report, "_spending_recent", lambda: "最新消費內容")
    monkeypatch.setattr(daily_report, "_kitchen_for_personal",
                        lambda: {"items": [], "more": 0, "recipe_text": "",
                                 "text": "高麗菜快過期"})
    return box


def test_personal_report_is_emailed(_mailed):
    daily_report._email_personal_report("2026-08-19")
    assert len(_mailed) == 1
    subject, body = _mailed[0]
    assert "2026-08-19" in subject
    assert "板橋天氣" in body
    assert "最新消費內容" in body
    assert "高麗菜快過期" in body


def test_personal_report_never_touches_push_quota():
    """改成 email 的理由就是這個 —— 個人版不能再吃 LINE push 配額。

    群組版照舊走 push_message;這裡守的是「不要哪天又被接回 1 對 1 推播」。
    """
    assert not hasattr(daily_report, "_push_personal_report")
    assert not hasattr(daily_report, "push_to_user_sync")


def test_personal_report_uses_banqiao_locations(monkeypatch, _mailed):
    """個人版天氣必須用板橋,不能沿用群組版的淡水金山。"""
    got = {}
    monkeypatch.setattr(daily_report, "get_weather_report",
                        lambda locations=None: got.update(loc=locations) or ("天氣", None))

    daily_report._email_personal_report("2026-08-19")
    assert got["loc"] == weather.PERSONAL_WEATHER_LOCATIONS
    assert "板橋區" in got["loc"]


def test_personal_report_skipped_without_app_password(monkeypatch, _mailed):
    """少一個 env var 就安靜跳過,不要讓整個排程 job 進 error listener。"""
    monkeypatch.setattr(mailer, "is_configured", lambda: False)
    daily_report._email_personal_report("2026-08-19")
    assert _mailed == []


def test_personal_report_skipped_when_nothing_to_say(monkeypatch, _mailed):
    """三段都沒內容就不要寄一封空信。"""
    monkeypatch.setattr(daily_report, "get_weather_report",
                        lambda locations=None: (None, None))
    monkeypatch.setattr(daily_report, "_spending_recent", lambda: None)
    monkeypatch.setattr(daily_report, "_kitchen_for_personal", lambda: None)
    daily_report._email_personal_report("2026-08-19")
    assert _mailed == []


def test_personal_weather_failure_does_not_kill_the_rest(monkeypatch, _mailed):
    """天氣炸了還是要把食材和消費寄出去。"""
    def boom(locations=None):
        raise RuntimeError("CWA 掛了")
    monkeypatch.setattr(daily_report, "get_weather_report", boom)
    monkeypatch.setattr(daily_report, "notify_admin", lambda *a, **k: None)

    daily_report._email_personal_report("2026-08-19")

    assert len(_mailed) == 1
    assert "最新消費內容" in _mailed[0][1]


def test_kitchen_full_text_used_because_email_has_no_buttons(monkeypatch, _mailed):
    """LINE 推播版有 page_id 時清單交給「已用掉」按鈕、文字只留菜色建議。

    email 沒有 quick reply —— 清單必須留在文字裡,不然快過期的東西
    整個看不到,只剩一句沒頭沒尾的菜色建議。
    """
    monkeypatch.setattr(daily_report, "_kitchen_for_personal",
                        lambda: {"items": [{"name": "高麗菜", "page_id": "p1",
                                            "days": 2}],
                                 "more": 0, "recipe_text": "建議煮高麗菜炒肉",
                                 "text": "高麗菜 2 天\n\n建議煮高麗菜炒肉"})

    daily_report._email_personal_report("2026-08-19")

    body = _mailed[0][1]
    assert "高麗菜 2 天" in body
    assert "建議煮高麗菜炒肉" in body


# ── 食材:沒事就不出現 ────────────────────────────────────

def test_kitchen_returns_none_when_nothing_expiring(monkeypatch):
    """沒有快過期的就回 None —— 每天跳一則「今天沒事」會讓人略過整包。"""
    import sys

    class FakeNotion:
        def is_configured(self):
            return True

        def pantry_load(self, status="在庫"):
            return [{"name": "醬油", "days_left": 300}]

        def recipes_load(self, pantry=None):
            return []

    monkeypatch.setitem(sys.modules, "notion_db", FakeNotion())
    monkeypatch.setattr("kitchen.expiring_soon", lambda pantry, days=3: [])

    assert daily_report._kitchen_for_personal() is None


def test_kitchen_returns_none_without_notion(monkeypatch):
    import sys

    class NoNotion:
        def is_configured(self):
            return False

    monkeypatch.setitem(sys.modules, "notion_db", NoNotion())
    assert daily_report._kitchen_for_personal() is None


# ── 天氣模組可指定地點 ────────────────────────────────────

class _FakeResp:
    def json(self):
        return {"records": {"Locations": [{"Location": []}]}}


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
    assert "板橋區" in inspect.getsource(weather.get_owm_weather)
