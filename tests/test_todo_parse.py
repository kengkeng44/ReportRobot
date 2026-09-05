"""一句話 → 待辦內容 + 起訖日 + 優先度。

零 I/O、零 mock：所有案例直接餵字串斷言結果。

基準日一律用固定的 date(2026, 9, 5)（**週五**）而不是 today() ——
會隨時間漂移的測試等於沒有測試。跨月、跨年的案例另外寫死自己的基準日。
"""

from datetime import date

import todo_parse

FRI = date(2026, 9, 5)          # 2026-09-05 是週五


def _d(text, today=FRI):
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
    _s, _e, rest = todo_parse.parse_dates("明天交資料", FRI)
    assert rest == "交資料"
