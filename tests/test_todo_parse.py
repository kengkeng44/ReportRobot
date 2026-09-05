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
