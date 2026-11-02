"""山屋日期：哪些晚上值得搶（隔天放假）、未來幾天有哪些抽籤／開放日。

規則來源：~/.claude/skills/hut-apply/SKILL.md（2026-10-25 讀過各機關須知）。
排雲的實際抽籤日會因假日順延，這裡只算開放申請日，抽籤日請查 bed_8.aspx。
"""

from datetime import date, timedelta

import holidays

WEEKDAY = "一二三四五六日"


def is_day_off(d):
    return d.weekday() >= 5 or d in holidays.Taiwan(years=d.year)


def is_good_night(d):
    """住宿日 d 那晚隔天放假（週五、週六、連假前一晚）。"""
    return is_day_off(d + timedelta(days=1))


def fmt_day(d):
    return f"{d.month}/{d.day}({WEEKDAY[d.weekday()]})"


def minus_months(d, n):
    """同日往前推 n 個月；那天不存在就取下個月 1 號（2/29 → 3/1）。"""
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    try:
        return date(y, m, d.day)
    except ValueError:
        return date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)


# (地點, 事件, 住宿日 → 事件日)
RULES = (
    ("嘉明湖／向陽", "15:00 抽籤（16:00 公布）", lambda d: d - timedelta(days=30)),
    ("檜谷", "15:00 抽籤（15:30 公布）", lambda d: d - timedelta(days=30)),
    ("天池", "開放申請（先到先得）", lambda d: d - timedelta(days=60)),
    ("排雲", "開放申請（抽籤制，抽籤日查 bed_8.aspx）", lambda d: minus_months(d, 2)),
    ("九九／雪霸／太魯閣", "07:00 開放申請（先到先得）", lambda d: minus_months(d, 2)),
    ("眠月線", "開放申請", lambda d: d - timedelta(days=60)),
)


def upcoming_events(today, days=7):
    """[(事件日, 地點, 住宿日, 事件)]，只列隔天放假的住宿日，依日期排序。"""
    end = today + timedelta(days=days)
    out = []
    for offset in range(1, 80):
        night = today + timedelta(days=offset)
        if not is_good_night(night):
            continue
        for place, what, when in RULES:
            e = when(night)
            if today <= e < end:
                out.append((e, place, night, what))
    return sorted(out)
