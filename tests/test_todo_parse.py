"""一句話 → 待辦內容 + 起訖日 + 優先度。

零 I/O、零 mock：所有案例直接餵字串斷言結果。

基準日一律用固定的 date(2026, 9, 5)（**週五**）而不是 today() ——
會隨時間漂移的測試等於沒有測試。跨月、跨年的案例另外寫死自己的基準日。
"""

from datetime import date

import todo_parse

SAT = date(2026, 9, 5)          # 2026-09-05 是**週六**（本週一 = 8/31）


def _d(text, today=SAT):
    """只取日期，讓斷言短一點。"""
    start, end, _rest = todo_parse.parse_dates(text, today)
    return start, end


# ── 相對日 ────────────────────────────────────────────────

def test_today():
    assert _d("今天交資料") == (date(2026, 9, 5), None)


def test_tomorrow():
    assert _d("明天交資料") == (date(2026, 9, 6), None)


def test_day_after_tomorrow():
    assert _d("後天交資料") == (date(2026, 9, 7), None)


def test_two_days_after_tomorrow():
    """大後天必須排在「後天」前面比對，否則「後天」會先吃掉尾巴。"""
    assert _d("大後天交資料") == (date(2026, 9, 8), None)


def test_n_days_later():
    assert _d("三天後交資料") == (date(2026, 9, 8), None)
    assert _d("3天後交資料") == (date(2026, 9, 8), None)


def test_n_weeks_later():
    assert _d("一週後交資料") == (date(2026, 9, 12), None)
    assert _d("2禮拜後交資料") == (date(2026, 9, 19), None)


def test_no_date_at_all():
    assert _d("交社宅資料") == (None, None)


def test_date_token_is_removed_from_the_text():
    """留著「明天」兩個字會讓待辦顯示成「明天交資料」——
    隔天再看就是錯的。"""
    _s, _e, rest = todo_parse.parse_dates("明天交資料", SAT)
    assert rest == "交資料"


# ── 星期 ──────────────────────────────────────────────────
#
# 這是整支模組最容易寫錯的地方，所以每個 case 都有測試。
# 演算法釘死：先算「本週一」= today - today.weekday()，
# 下週一 = 本週一 + 7，再加 (X-1) 天。跨月跨年由 date 型別自己處理。

def test_this_week_monday_can_be_in_the_past():
    """週六講「這週一」指的是已經過去的 8/31，不是下週。"""
    assert _d("這週一交資料") == (date(2026, 8, 31), None)
    assert _d("本週一交資料") == (date(2026, 8, 31), None)


def test_next_week_monday():
    assert _d("下週一交資料") == (date(2026, 9, 7), None)
    assert _d("下禮拜一交資料") == (date(2026, 9, 7), None)
    assert _d("下星期一交資料") == (date(2026, 9, 7), None)


def test_bare_weekday_takes_the_next_occurrence():
    """沒講這週下週時取「下一次」——週六講「週一」是下週一。"""
    assert _d("週一交資料") == (date(2026, 9, 7), None)


def test_bare_weekday_today_means_today():
    """週六講「週六」就是今天，不是下週六。
    「今天要交」是最常見的說法，推到七天後等於漏掉。"""
    assert _d("週六交資料") == (date(2026, 9, 5), None)
    assert _d("禮拜六交資料") == (date(2026, 9, 5), None)


def test_weekday_seven_is_sunday():
    """週日 / 週天 都是同一天。"""
    assert _d("週日交資料") == (date(2026, 9, 6), None)
    assert _d("週天交資料") == (date(2026, 9, 6), None)


def test_next_week_across_month_boundary():
    """9/29（週二）講「下週一」→ 10/05。"""
    assert _d("下週一交資料", today=date(2026, 9, 29)) == (date(2026, 10, 5), None)


def test_next_week_across_year_boundary():
    """12/29（週二）講「下週三」→ 隔年 1/06。"""
    assert _d("下週三交資料", today=date(2026, 12, 29)) == (date(2027, 1, 6), None)


def test_weekday_token_is_removed():
    _s, _e, rest = todo_parse.parse_dates("下週一交社宅資料", SAT)
    assert rest == "交社宅資料"


# ── 明確日期與區間 ────────────────────────────────────────

def test_slash_date():
    assert _d("9/15 交資料") == (date(2026, 9, 15), None)


def test_chinese_date():
    assert _d("9月15日交資料") == (date(2026, 9, 15), None)
    assert _d("9月15號交資料") == (date(2026, 9, 15), None)


def test_date_with_year():
    assert _d("2027/1/5 交資料") == (date(2027, 1, 5), None)


def test_recent_past_date_stays_in_this_year():
    """9/05 講「9/01」是四天前，不是明年 —— 補登昨天忘了記的事很常見。"""
    assert _d("9/1 交資料") == (date(2026, 9, 1), None)


def test_long_past_date_rolls_to_next_year():
    """12/20 講「1/5」指的是明年一月，不是十一個月前。

    分界線是 30 天：超過就往後滾一年。任何分界線都會有錯的個案，
    但「補登上個月」比「回到去年」常見得多。
    """
    assert _d("1/5 交資料", today=date(2026, 12, 20)) == (date(2027, 1, 5), None)


def test_bare_digits_are_never_a_date():
    """「買915號的東西」不該變成 9/15 到期。

    裸數字沒有任何分隔符，猜錯的代價（憑空長出一個截止日）
    比猜不到（跳防呆按鈕讓使用者按一下）大得多。
    """
    assert _d("買915號的東西") == (None, None)
    assert _d("繳 3000 元") == (None, None)


def test_range_with_dash():
    assert _d("9/1-9/10 出差") == (date(2026, 9, 1), date(2026, 9, 10))


def test_range_with_chinese_to():
    assert _d("9/1到9/10 出差") == (date(2026, 9, 1), date(2026, 9, 10))
    assert _d("9/1~9/10 出差") == (date(2026, 9, 1), date(2026, 9, 10))


def test_range_across_year():
    """12/28-1/5：結束比開始早就把結束滾到下一年。"""
    assert _d("12/28-1/5 出差", today=date(2026, 12, 1)) == (
        date(2026, 12, 28), date(2027, 1, 5))


def test_range_token_is_removed():
    _s, _e, rest = todo_parse.parse_dates("9/1-9/10 出差", SAT)
    assert rest == "出差"

# ── 優先度 ────────────────────────────────────────────────

def test_priority_token():
    assert todo_parse.parse_priority("P0 交社宅資料") == ("P0", "交社宅資料")


def test_priority_is_case_insensitive():
    assert todo_parse.parse_priority("p2 買菜") == ("P2", "買菜")


def test_priority_can_be_at_the_end():
    assert todo_parse.parse_priority("交社宅資料 P1") == ("P1", "交社宅資料")


def test_no_priority():
    assert todo_parse.parse_priority("交社宅資料") == (None, "交社宅資料")


def test_p4_is_not_a_priority():
    """只認 P0-P3。P4 留在內容裡，不要自作主張收斂。"""
    assert todo_parse.parse_priority("P4 交資料") == (None, "P4 交資料")


def test_natural_language_urgency_is_not_a_priority():
    """「很急」「重要」是主觀詞，猜錯會讓使用者對整個功能失去信任，
    而防呆按鈕點一下就解決了。"""
    assert todo_parse.parse_priority("很急 交社宅資料") == (None, "很急 交社宅資料")


def test_priority_inside_a_word_is_not_matched():
    """「買P3手機殼」的 P3 是產品型號，不是優先度。"""
    assert todo_parse.parse_priority("買P3手機殼") == (None, "買P3手機殼")


# ── parse()：組合器 + AI 補位 ─────────────────────────────

def test_parse_combines_everything():
    out = todo_parse.parse("P0 下週一交社宅資料", SAT)

    assert out == {
        "text": "交社宅資料",
        "start": date(2026, 9, 7),
        "end": None,
        "priority": "P0",
    }


def test_parse_without_date_or_priority():
    out = todo_parse.parse("交社宅資料", SAT)

    assert out["text"] == "交社宅資料"
    assert out["start"] is None
    assert out["priority"] is None


def test_rules_win_and_ai_is_never_called(monkeypatch):
    """規則認得的說法不該花錢。每加一筆待辦就打一次 API 是不可接受的。"""
    called = []
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: called.append(prompt))

    todo_parse.parse("明天交資料", SAT)

    assert called == []


def test_ai_fills_in_what_rules_cannot(monkeypatch):
    """「中秋前」這種規則吃不下來的說法才呼叫 AI。"""
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "2026-09-25")

    out = todo_parse.parse("中秋前交資料", SAT)

    assert out["start"] == date(2026, 9, 25)


def test_ai_can_return_a_range(monkeypatch):
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "2026-09-25~2026-09-28")

    out = todo_parse.parse("中秋連假出遊", SAT)

    assert out["start"] == date(2026, 9, 25)
    assert out["end"] == date(2026, 9, 28)


def test_ai_saying_none_means_no_date(monkeypatch):
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "NONE")

    out = todo_parse.parse("交社宅資料", SAT)

    assert out["start"] is None


def test_ai_failure_does_not_lose_the_todo(monkeypatch):
    """AI 掛掉時照樣回內容，讓呼叫端把事情記下來再跳防呆按鈕。
    因為解析日期失敗就整件事不記，是待辦最不能發生的事。"""
    def _boom(prompt):
        raise RuntimeError("API 掛了")
    monkeypatch.setattr(todo_parse, "_ai", _boom)

    out = todo_parse.parse("中秋前交資料", SAT)

    assert out["text"] == "中秋前交資料"
    assert out["start"] is None


def test_ai_garbage_is_ignored(monkeypatch):
    """AI 回了不是日期的東西時當作沒解析到，不要讓它污染資料。"""
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: "我覺得是下週吧")

    out = todo_parse.parse("中秋前交資料", SAT)

    assert out["start"] is None


def test_ai_is_not_called_for_empty_text(monkeypatch):
    called = []
    monkeypatch.setattr(todo_parse, "_ai", lambda prompt: called.append(prompt))

    todo_parse.parse("   ", SAT)

    assert called == []


def test_prompt_carries_todays_date(monkeypatch):
    """AI 得知道今天幾號才算得出「中秋前」。"""
    seen = []
    monkeypatch.setattr(todo_parse, "_ai",
                        lambda prompt: seen.append(prompt) or "NONE")

    todo_parse.parse("中秋前交資料", SAT)

    assert "2026-09-05" in seen[0]
