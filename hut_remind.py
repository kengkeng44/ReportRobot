"""山屋申請提醒：每個週六晚上的「最後送件日」與「07:00 開放日」，前一晚 21:00 寄 Gmail 給你。

紅線（~/.claude/skills/hut-apply/SKILL.md）：驗證碼與送出一律你自己按，
這裡只負責「哪天該做什麼」＋給一句可以貼給 Claude 的話，讓它在 Chrome 填到驗證碼前。

- 抽籤制：開放期間哪天送都一樣，不用搶。提醒在抽籤前一天（最後一個安心送件日）。
- 先到先得：開放那天 07:00 是關鍵。前一晚 21:00 提醒存草稿，當天 06:50 再推一次。
"""

import os
import tempfile
from datetime import timedelta

from hut_calendar import fmt_day, is_notify_night, minus_months

ONESTOP_APPLY = "https://hike.taiwan.gov.tw/apply_1.aspx"
PARTY = 2  # 使用者 2026-11-06：通常 2 人

# (地點, 申請的單位, 抽籤 or 先到先得, 住宿日 → 關鍵日, 申請頁)
# 抽籤制的關鍵日 = 抽籤日；提醒排在前一天。先到先得的關鍵日 = 07:00 開放那天。
RULES = (
    ("嘉明湖／向陽", "嘉明湖山屋", "lottery", lambda d: d - timedelta(days=30),
     "https://jmlnt.forest.gov.tw/"),
    ("檜谷", "檜谷山莊", "lottery", lambda d: d - timedelta(days=30), ONESTOP_APPLY),
    # 排雲實際抽籤日會因假日順延，以 bed_8.aspx 為準；這裡用「前 1 個月」估
    ("排雲", "排雲山莊", "lottery", lambda d: minus_months(d, 1), ONESTOP_APPLY),
    ("天池", "天池山莊", "first", lambda d: d - timedelta(days=60),
     "https://tconline.forest.gov.tw/"),
    ("九九", "九九山莊", "first", lambda d: minus_months(d, 2), ONESTOP_APPLY),
    ("雪霸", "三六九臨時營地", "first", lambda d: minus_months(d, 2), ONESTOP_APPLY),
    ("奇萊", "奇萊山屋", "first", lambda d: minus_months(d, 2), ONESTOP_APPLY),
    ("南湖", "南湖山屋", "first", lambda d: minus_months(d, 2), ONESTOP_APPLY),
)


def action_day(kind, key_day):
    """要動手的那天：抽籤制是抽籤前一天，先到先得是開放當天。"""
    return key_day - timedelta(days=1) if kind == "lottery" else key_day


def actions_on(day):
    """[(地點, 單位, kind, 住宿日, 關鍵日, 申請頁)]：day 這天要動手的週六申請。"""
    out = []
    for offset in range(1, 90):
        night = day + timedelta(days=offset)
        if not is_notify_night(night):
            continue
        for place, unit, kind, rule, url in RULES:
            key = rule(night)
            if action_day(kind, key) == day:
                out.append((place, unit, kind, night, key, url))
    return sorted(out, key=lambda a: (a[3], a[0]))


def _ask(unit, night):
    return f"幫我申請 {unit} {fmt_day(night)} {PARTY}人"


def evening_text(today):
    """21:00 預告明天要做的事；沒有就回 None。"""
    tomorrow = today + timedelta(days=1)
    acts = actions_on(tomorrow)
    if not acts:
        return None
    lines = [f"⛰️ 明天 {fmt_day(tomorrow)} 山屋申請"]
    for place, unit, kind, night, key, url in acts:
        if kind == "lottery":
            draw = f"{fmt_day(key)} 15:00 抽籤"
            if place == "排雲":
                draw += "（實際日期看 https://hike.taiwan.gov.tw/bed_8.aspx）"
            lines.append(f"\n🎲 {unit} {fmt_day(night)}：明天前送出，{draw}")
        else:
            onestop = url == ONESTOP_APPLY
            when = "07:00 開放" if onestop else "開放申請（時間以官網為準）"
            tip = "今晚先存草稿" if onestop else "一早上網申請"
            lines.append(f"\n⏰ {unit} {fmt_day(night)}：明天 {when}，先到先得。{tip}")
        lines.append(f"對 Claude 說：{_ask(unit, night)}")
        lines.append(url)
    lines.append("\n驗證碼和送出要你自己按")
    return "\n".join(lines)


def morning_text(today):
    """06:50 只提醒今天 07:00 開放的一站式先到先得（能存草稿的）；沒有就回 None。"""
    acts = [a for a in actions_on(today) if a[2] == "first" and a[5] == ONESTOP_APPLY]
    if not acts:
        return None
    lines = ["⏰ 07:00 開放，打開草稿按驗證碼送出"]
    lines.extend(f"・{unit} {fmt_day(night)}" for _, unit, _, night, _, _ in acts)
    lines.append("草稿編輯：https://hike.taiwan.gov.tw/apply_2_1.aspx")
    return "\n".join(lines)


def _flag(kind, day):
    return os.path.join(tempfile.gettempdir(), f"hut_remind_{kind}_{day:%Y%m%d}.flag")


def push(kind, today, send_func=None):
    """kind = "evening" | "morning"。寄 Gmail（使用者 2026-11-09：山屋提醒不走 LINE）。

    同一天只寄一次（重啟補跑靠 flag 擋）。回 True = 有寄。
    send_func(subject, body) 預設 mailer.send_email。
    """
    text = (evening_text if kind == "evening" else morning_text)(today)
    if not text:
        return False
    flag = _flag(kind, today)
    if os.path.exists(flag):
        return False
    if send_func is None:
        from mailer import send_email as send_func
    subject = text.splitlines()[0]
    if not send_func(subject, text):
        return False
    try:
        with open(flag, "w") as f:
            f.write(kind)
    except OSError as e:
        print(f"[hut_remind] flag 寫入失敗（非致命）：{e}")
    return True
