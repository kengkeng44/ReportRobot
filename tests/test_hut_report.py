from datetime import date, datetime, timedelta, timezone

import hut_report as hr
from hut_sources import Slot

TODAY = date(2026, 11, 2)
SAT = date(2026, 12, 5)   # 週六晚 → 隔天週日
WED = date(2026, 12, 2)


def _c(unit, day, remaining, before, kind="bed", site="jmlnt", applicants=10):
    return hr.Change(Slot(site, unit, kind, day, remaining, applicants), before)


def test_highlight_needs_good_night_and_enough_space():
    assert hr.is_highlight(_c("嘉明湖山屋", SAT, 2, 0))
    assert not hr.is_highlight(_c("嘉明湖山屋", SAT, 1, 0))          # 床位要 ≥ 2
    assert hr.is_highlight(_c("嘉明湖營地", SAT, 1, 0, kind="tent"))  # 一頂帳 2 人
    assert not hr.is_highlight(_c("嘉明湖山屋", WED, 5, 0))           # 平日


def test_email_with_highlight():
    changes = [_c("嘉明湖山屋", SAT, 4, 0), _c("向陽山屋", WED, 3, None)]
    events = [(TODAY, "九九／雪霸／太魯閣", date(2027, 1, 2), "07:00 開放申請（先到先得）")]
    subject, text = hr.build_email(changes, events, {"onestop_99": "本次沒查到（電腦沒開）"}, TODAY)
    assert subject == "山屋：嘉明湖山屋 12/5(六) 釋出 4床"
    assert "嘉明湖山屋 12/5(六) 剩 0→4床（報名 10）" in text
    assert "向陽山屋 12/2(三) 剩 3床（新）（報名 10）" in text
    assert "用 hut-apply 申請 嘉明湖山屋 2026-12-05 2人" in text
    assert "11/2(一) 九九／雪霸／太魯閣：1/2(六) 住宿 07:00 開放申請（先到先得）" in text
    assert "嘉明湖／向陽：" in text            # 重點地點附交通卡
    assert "九九：本次沒查到（電腦沒開）" in text
    assert text.index("<b>重點") < text.index("<b>全部變化")


def test_email_subject_without_highlight_or_changes():
    subject, _ = hr.build_email([_c("向陽山屋", WED, 3, None)], [], {}, TODAY)
    assert subject == "山屋：1 筆空位變化"
    events = [(TODAY, "天池", date(2026, 12, 31), "開放申請（先到先得）")]
    subject, text = hr.build_email([], events, {}, TODAY)
    assert subject == "山屋：今天 1 個抽籤／開放日"
    assert "<b>重點" not in text


def test_line_summary():
    checked = datetime(2026, 11, 2, 16, 30, tzinfo=timezone(timedelta(hours=8)))
    slots = [Slot("jmlnt", "嘉明湖山屋", "bed", SAT, 4, 9),
             Slot("jmlnt", "嘉明湖山屋", "bed", WED, 9, 9),       # 平日不列
             Slot("tconline", "天池山莊", "bed", SAT, 1, 9)]      # 不夠 2 人
    text = hr.line_summary(slots, checked, {"kgonline": "HTTP 500"})
    assert text.startswith("山屋（11/02 16:30 查）")
    assert "嘉明湖山屋：12/5(六) 4床" in text
    assert "天池" not in text.split("沒查到")[0]
    assert "檜谷" in text
    assert text.endswith("完整內容看 Gmail")


def test_line_summary_none():
    checked = datetime(2026, 11, 2, 7, 0, tzinfo=timezone(timedelta(hours=8)))
    assert "目前沒有週五六日夠 2 人的空位" in hr.line_summary([], checked, {})
