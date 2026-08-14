"""投資分頁三顆按鈕：比較 / 盤前 / 大盤。

實際事故：這三格送的是裸指令（`/比較`、`/盤前`、`/大盤`），command_router
都不認得，於是掉進「不認得的中文指令丟給 AI 上網查」那條 fallback ——
按鈕不會壞、不會有紅字，只會安靜地付 Anthropic 的錢買一段跟股票無關的
通用解釋。盤前與大盤兩個 kind 從頭到尾就沒實作過。

所以這裡的測試分兩層：
1. 選單那三格送出的東西，parse 後**不可以**是 free_query
2. 三個 kind 真的有人接
"""

from datetime import date

import pytest

import command_router as cr
import premarket
import setup_richmenu as rm


def _cell(menu_key, label):
    return next(c[3] for c in rm.MENUS[menu_key]["cells"] if c[0] == label)


def _ctx():
    return {"source_type": "user", "user_id": "U1"}


# ── 沒有一顆按鈕該掉進 free_query ─────────────────────────

@pytest.mark.parametrize("menu_key", sorted(rm.MENUS))
def test_no_menu_cell_falls_through_to_paid_ai(menu_key):
    """free_query 走 Anthropic API 要付費。按鈕是「按了就送出」，
    使用者沒有機會反悔，所以一格都不能漏到那裡去。"""
    for label, _sub, _color, (kind, param) in rm.MENUS[menu_key]["cells"]:
        if kind != "message":
            continue
        parsed = cr.parse(param)
        assert parsed is not None, f"「{label}」送出 {param!r} 沒人認得"
        assert parsed[0] != "free_query", (
            f"「{label}」送出 {param!r} 會掉進付費的 AI 自由問答"
        )


def test_compare_cell_prefills_keyboard():
    """比較要帶兩個代號才有意義，裸指令沒東西可比。"""
    kind, param = _cell("invest", "比較")

    assert kind == "prompt"
    assert cr.parse(param + "0050 0056 1y") == ("compare", ("0050", "0056", "1y"))


@pytest.mark.parametrize("text", ["/比較", "/比較 0050"])
def test_incomplete_compare_returns_usage_not_paid_ai(text):
    """預填鍵盤之後只補一檔就送出是很自然的失誤，不該因此付一次 AI 的錢。"""
    assert cr.parse(text) == ("compare", None)
    assert "0050 0056" in cr.handle(text, _ctx())


@pytest.mark.parametrize("text,expected", [
    ("/比較 0050 0056", ("0050", "0056", None)),
    ("/比較 台積電 鴻海", ("台積電", "鴻海", None)),
    ("/比較 AAPL TSLA 1y", ("AAPL", "TSLA", "1y")),
])
def test_compare_still_accepts_the_normal_forms(text, expected):
    assert cr.parse(text) == ("compare", expected)


# ── 大盤 ───────────────────────────────────────────────────

def test_market_command_parses():
    for text in ("/大盤", "大盤", "/指數", "/market"):
        assert cr.parse(text) == ("market", None), f"{text} 不認得"


def test_market_cell_sends_a_real_command():
    kind, param = _cell("invest", "大盤")

    assert kind == "message"
    assert cr.parse(param) == ("market", None)


def test_market_handler_returns_summary(monkeypatch):
    import markets
    monkeypatch.setattr(markets, "build_market_summary", lambda: "📊 大盤指數\n台股加權｜1")

    assert "大盤指數" in cr.handle("/大盤", _ctx())


def test_market_is_not_personal_only():
    """大盤是公開資訊，家人在群組問也該回 —— 不像庫存跟財務。"""
    assert "market" not in cr._PERSONAL_KINDS


# ── 盤前 ───────────────────────────────────────────────────

def test_premarket_command_parses():
    for text in ("/盤前", "盤前", "/盤前報告"):
        assert cr.parse(text) == ("premarket", None), f"{text} 不認得"


def test_premarket_cell_sends_a_real_command():
    kind, param = _cell("invest", "盤前")

    assert kind == "message"
    assert cr.parse(param) == ("premarket", None)


def test_premarket_handler_forces_even_on_weekend(monkeypatch):
    """週末排程會 skip，但使用者主動按就是想看 —— 回「週末沒有」很沒用。"""
    seen = {}

    def fake(force=False):
        seen["force"] = force
        return "📊 盤前報告"

    monkeypatch.setattr(premarket, "build_premarket_report", fake)

    assert "盤前報告" in cr.handle("/盤前", _ctx())
    assert seen["force"] is True


def test_premarket_handler_survives_none(monkeypatch):
    monkeypatch.setattr(premarket, "build_premarket_report", lambda force=False: None)

    reply = cr.handle("/盤前", _ctx())

    assert isinstance(reply, str) and reply


# ── 當日快取：AI 那段一天只付一次 ─────────────────────────

@pytest.fixture(autouse=True)
def clear_cache():
    premarket._clear_ai_cache()
    yield
    premarket._clear_ai_cache()


def test_ai_summary_is_cached_within_the_day(monkeypatch):
    """早上推播已經付過一次錢。按鈕再叫一次不該重付。"""
    calls = []
    monkeypatch.setattr(premarket, "_ai_summary_uncached",
                        lambda chip_data=None: calls.append(1) or "重點")
    monkeypatch.setattr(premarket, "today_tpe", lambda: date(2026, 8, 15))

    assert premarket._build_ai_summary() == "重點"
    assert premarket._build_ai_summary() == "重點"
    assert len(calls) == 1, "同一天只該真的呼叫一次 AI"


def test_ai_cache_expires_next_day(monkeypatch):
    calls = []
    monkeypatch.setattr(premarket, "_ai_summary_uncached",
                        lambda chip_data=None: calls.append(1) or "重點")

    day = {"d": date(2026, 8, 15)}
    monkeypatch.setattr(premarket, "today_tpe", lambda: day["d"])

    premarket._build_ai_summary()
    day["d"] = date(2026, 8, 16)
    premarket._build_ai_summary()

    assert len(calls) == 2, "隔天要重抓，不然拿到昨天的盤前"


def test_ai_failure_is_not_cached(monkeypatch):
    """失敗回空字串。把空字串記起來會讓那一整天都沒有盤前重點。"""
    calls = []

    def flaky(chip_data=None):
        calls.append(1)
        return "" if len(calls) == 1 else "重點"

    monkeypatch.setattr(premarket, "_ai_summary_uncached", flaky)
    monkeypatch.setattr(premarket, "today_tpe", lambda: date(2026, 8, 15))

    assert premarket._build_ai_summary() == ""
    assert premarket._build_ai_summary() == "重點"
