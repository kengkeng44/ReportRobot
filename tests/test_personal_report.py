"""個人版每日報(待辦 + 今日三句 + 財務 + 板橋天氣,寄給自己)。

群組版與個人版的分工是刻意的:群組不該看到別人的帳單,
但自己每天要看是另一回事。

2026-08-20 個人版從 LINE 1 對 1 推播改成 Gmail(見 mailer.py)——
push 每月只有 200 則,不該有一半花在自己身上。
下面「卡片組裝」那段測的 flex_builder.personal_report_carousel 目前
沒有呼叫端,刻意留著:要改回 LINE 推播時直接接回去就好(它的
kitchen_text 參數跟這裡拿掉的 daily_report._kitchen_for_personal
是兩回事,沒有一起動)。

2026-09-04 個人版信本身拿掉「冰箱快過期・煮什麼」——太吵,記錄還在,
通知沒了。見本檔「冰箱快過期拿掉了」那段。
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
    # 簽章要跟真的 mailer.send_email 對齊。少一個 images 參數，
    # daily_report 傳 images= 就會炸在替身上 —— 那是替身過期，
    # 不是被測程式有問題（2026-09-04 就是這樣紅了三個測試）。
    monkeypatch.setattr(mailer, "send_email",
                        lambda subject, body, html=None, images=None:
                            box.append((subject, body)) or True)
    monkeypatch.setattr(daily_report, "get_weather_report",
                        lambda locations=None: ("板橋天氣", None))
    monkeypatch.setattr(daily_report, "_spending_recent", lambda: "最新消費內容")
    return box


def test_personal_report_is_emailed(_mailed):
    daily_report._email_personal_report("2026-08-19")
    assert len(_mailed) == 1
    subject, body = _mailed[0]
    assert "2026-08-19" in subject
    assert "板橋天氣" in body
    assert "最新消費內容" in body


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
    daily_report._email_personal_report("2026-08-19")
    assert _mailed == []


def test_personal_weather_failure_does_not_kill_the_rest(monkeypatch, _mailed):
    """天氣炸了還是要把消費寄出去。"""
    def boom(locations=None):
        raise RuntimeError("CWA 掛了")
    monkeypatch.setattr(daily_report, "get_weather_report", boom)
    monkeypatch.setattr(daily_report, "notify_admin", lambda *a, **k: None)

    daily_report._email_personal_report("2026-08-19")

    assert len(_mailed) == 1
    assert "最新消費內容" in _mailed[0][1]


# ── 冰箱快過期拿掉了(2026-09-04)──────────────────────────
#
# 使用者覺得每天跳這張太吵：買了什麼還是要記,但過期不用天天被通知。
# 記錄路徑(notion_db.pantry_add / pantry_load、LINE「買了」、電子發票
# → pantry 的橋接)完全沒動;動的只是「每天被動通知」這件事——
# 要看自己在 LINE 打「快過期」問 command_router 的 pantry_expiring。

def test_daily_report_no_longer_has_kitchen_for_personal():
    """跟 test_daily_kitchen.py 的 test_daily_report_no_longer_fetches_kitchen
    同一個精神:函式沒了就要真的沒了,不是留著沒人叫。"""
    assert not hasattr(daily_report, "_kitchen_for_personal")


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


# ── 圓餅圖真的進得了信 (2026-09-04) ────────────────────────

@pytest.fixture
def _mailed_full(monkeypatch):
    """跟 _mailed 一樣，但連 html 與 images 都收下來。

    _mailed 只留 (subject, body)，接線層（產圖 → images= → <img>）
    整條路徑沒有任何斷言守著。分開一個 fixture 而不是改 _mailed，
    是因為既有測試都在 unpack 兩元組。
    """
    box = []
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(
        mailer, "send_email",
        lambda subject, body, html=None, images=None:
            box.append({"subject": subject, "body": body,
                        "html": html, "images": images}) or True)
    monkeypatch.setattr(daily_report, "get_weather_report",
                        lambda locations=None: ("板橋天氣", None))
    monkeypatch.setattr(daily_report, "_spending_recent", lambda: "最新消費內容")
    return box


def test_pie_chart_reaches_the_email(monkeypatch, _mailed_full):
    """圖檔路徑要掛上 images，HTML 要用同一個 cid 引用它。

    這兩件事在不同模組（daily_report / digest）各寫一次 cid，
    對不上的話信裡就是一個破圖，而且沒有任何既有測試會紅。
    """
    import spending_chart
    monkeypatch.setattr(spending_chart, "build_pie",
                        lambda txns, month: ("C:/tmp/pie.png", "本月合計 NT$9,142"))

    daily_report._email_personal_report("2026-09-04")

    sent = _mailed_full[0]
    assert sent["images"] == {daily_report.CHART_CID: "C:/tmp/pie.png"}
    assert f'src="cid:{daily_report.CHART_CID}"' in sent["html"]
    # 純文字版看不到圖，金額必須在文字裡自己活著
    assert "本月合計 NT$9,142" in sent["body"]


def test_no_chart_means_no_images(monkeypatch, _mailed_full):
    """月初還沒消費 → build_pie 回 (None, None)，不要掛一個空的 images。"""
    import spending_chart
    monkeypatch.setattr(spending_chart, "build_pie",
                        lambda txns, month: (None, None))

    daily_report._email_personal_report("2026-09-04")

    assert _mailed_full[0]["images"] is None
    assert "<img" not in _mailed_full[0]["html"]


# ── 標題只放日期（2026-09-05）─────────────
#
# 2026-09-04 試過「標題帶本月金額」，使用者看了實際效果之後要求拿掉。
# 標題只要日期。

def test_subject_is_date_only():
    """標題就是「每日個人報 + 日期」，不接任何東西。"""
    import inspect

    src = inspect.getsource(daily_report._email_personal_report)
    assert 'f"📮 每日個人報 {today}"' in src


def test_no_subject_helper_left_behind():
    """拿掉就要真的拿掉 —— 同 test_daily_kitchen.py 那一套精神。
    留著沒人叫的 helper 會讓下一個人以為還能用。
    """
    assert not hasattr(daily_report, "_subject")
    assert not hasattr(daily_report, "_SUBJECT_AMOUNT_RE")


def test_sent_subject_carries_no_amount(monkeypatch, _mailed_full):
    """端到端：就算 build_pie 有回摘要，標題也不得帶上。
    """
    import spending_chart
    monkeypatch.setattr(spending_chart, "build_pie",
                        lambda txns, month: ("C:/tmp/pie.png", "SUMMARY"))

    daily_report._email_personal_report("2026-09-05")

    assert _mailed_full[0]["subject"] == "📮 每日個人報 2026-09-05"


# ── 待辦要抓誰的（2026-09-06）──────────────────
#
# 2026-09-05 使用者在 LINE 新增了待辦，信裡却沒出現。原因不是待辦沒存
# 進 Notion，而是這兩個區塊要看 PERSONAL_USER_ID 才知道要抓誰的，
# 而那個變數從來沒設過。

def test_personal_user_id_falls_back_to_admin_id(monkeypatch):
    """PERSONAL_USER_ID 沒設時用 ADMIN_LINE_USER_ID。

    這兩個變數在這台 bot 裡永遠是同一個人 —— 個人版每日信
    就是寄給 admin 自己。要使用者把同一串 U... 貼兩次，只會有一次
    忘了貼，然後待辦區塊靜悄悄消失。
    """
    monkeypatch.delenv("PERSONAL_USER_ID", raising=False)
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")

    assert daily_report._personal_user_id() == "Uadmin"


def test_explicit_personal_user_id_wins(monkeypatch):
    """明設了就以明設為準 —— fallback 只是補位，不是接管。"""
    monkeypatch.setenv("PERSONAL_USER_ID", "Uother")
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")

    assert daily_report._personal_user_id() == "Uother"


def test_blank_personal_user_id_is_treated_as_unset(monkeypatch):
    """Infisical 裡建了變數但值是空白是常見的手滑。"""
    monkeypatch.setenv("PERSONAL_USER_ID", "   ")
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")

    assert daily_report._personal_user_id() == "Uadmin"


def test_no_ids_at_all_means_no_todo_section(monkeypatch):
    """兩個都沒設就跳過待辦 / 提醒，其他區塊照寄。"""
    monkeypatch.delenv("PERSONAL_USER_ID", raising=False)
    monkeypatch.delenv("ADMIN_LINE_USER_ID", raising=False)

    assert daily_report._personal_user_id() is None
