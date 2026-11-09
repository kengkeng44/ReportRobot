from datetime import date

import hut_remind as hr

# 2027-01-09 是週六；一站式前 2 個月 = 2026-11-09 07:00 開放，天池前 60 天 = 2026-11-10
SAT = date(2027, 1, 9)


def test_first_come_evening_before_open():
    text = hr.evening_text(date(2026, 11, 8))
    assert "九九山莊 1/9(六)" in text and "07:00 開放" in text
    assert "三六九臨時營地 1/9(六)" in text
    assert "幫我申請 九九山莊 1/9(六) 2人" in text
    assert "天池" not in text          # 天池隔天才開放


def test_tianchi_has_no_fake_time():
    text = hr.evening_text(date(2026, 11, 9))
    assert "天池山莊 1/9(六)" in text and "07:00" not in text


def test_lottery_reminds_day_before_draw():
    # 12/12(六) 嘉明湖、檜谷前 30 天抽 = 11/12；提醒在 11/11（前一晚 11/10 21:00 推）
    text = hr.evening_text(date(2026, 11, 10))
    assert "嘉明湖山屋 12/12(六)" in text and "11/12(四) 15:00 抽籤" in text
    assert "bed_8.aspx" in text         # 排雲要看實際抽籤日


def test_only_saturdays():
    for _, _, _, night, _, _ in hr.actions_on(date(2026, 11, 9)):
        assert night.weekday() == 5


def test_quiet_days_send_nothing():
    assert hr.evening_text(date(2026, 11, 6)) is None
    sent = []
    assert hr.push("evening", date(2026, 11, 6), send_func=lambda subj, t: sent.append(t) or True) is False
    assert sent == []


def test_morning_only_onestop_open_day():
    text = hr.morning_text(date(2026, 11, 9))
    assert "九九山莊 1/9(六)" in text and "apply_2_1.aspx" in text
    assert hr.morning_text(date(2026, 11, 10)) is None   # 天池那天不推 06:50


def test_push_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(hr.tempfile, "gettempdir", lambda: str(tmp_path))
    sent = []
    day = date(2026, 11, 8)
    assert hr.push("evening", day, send_func=lambda subj, t: sent.append(t) or True)
    assert not hr.push("evening", day, send_func=lambda subj, t: sent.append(t) or True)
    assert len(sent) == 1
