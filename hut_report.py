"""山屋監控的輸出文字：Gmail 信（純文字＋<b>，給 mailer.to_html）與 LINE 回覆。"""

from dataclasses import dataclass

import hut_info
from hut_calendar import fmt_day, is_good_night
from hut_sources import Slot

BED_MIN = 2   # 2 人
TENT_MIN = 1  # 一頂帳 2 人
LINE_MAX_PER_UNIT = 5


@dataclass(frozen=True)
class Change:
    slot: Slot
    before: int | None  # 上次的剩餘；None = 上次沒有這筆


def _enough(slot):
    need = BED_MIN if slot.kind == "bed" else TENT_MIN
    return (slot.remaining or 0) >= need


def is_highlight(change):
    return is_good_night(change.slot.day) and _enough(change.slot)


def _word(slot):
    return "床" if slot.kind == "bed" else "帳"


def _change_line(c):
    s = c.slot
    amount = f"{s.remaining}{_word(s)}（新）" if c.before is None else f"{c.before}→{s.remaining}{_word(s)}"
    return f"{hut_info.display(s)} {fmt_day(s.day)} 剩 {amount}（報名 {s.applicants}）"


def build_email(changes, events, errors, today):
    """(subject, text)。text 用 <b> 標題，交給 mailer.to_html 轉 HTML。"""
    highlights = sorted((c for c in changes if is_highlight(c)), key=lambda c: c.slot.day)
    events_today = [e for e in events if e[0] == today]
    if highlights:
        h = highlights[0].slot
        subject = f"山屋：{hut_info.display(h)} {fmt_day(h.day)} 釋出 {h.remaining}{_word(h)}"
        if len(highlights) > 1:
            subject += f" 等 {len(highlights)} 筆"
    elif changes:
        subject = f"山屋：{len(changes)} 筆空位變化"
    else:
        subject = f"山屋：今天 {len(events_today)} 個抽籤／開放日"

    parts = []
    if highlights:
        lines = []
        for c in highlights:
            lines += [_change_line(c), f"  → {hut_info.apply_phrase(c.slot)}"]
        parts.append("<b>重點（週末／連假、夠 2 人）</b>\n" + "\n".join(lines))
    if changes:
        ordered = sorted(changes, key=lambda c: (c.slot.site, c.slot.unit, c.slot.day))
        parts.append("<b>全部變化</b>\n" + "\n".join(_change_line(c) for c in ordered))
    if events:
        parts.append("<b>未來 7 天抽籤／開放日</b>\n" + "\n".join(
            f"{fmt_day(e)} {place}：{fmt_day(night)} 住宿 {what}" for e, place, night, what in events))
    places = list(dict.fromkeys(hut_info.place(c.slot.site) for c in highlights))
    if places:
        parts.append("<b>交通</b>\n" + "\n".join(hut_info.transport_card(p) for p in places))
    if errors:
        parts.append("<b>沒查到</b>\n" + "\n".join(
            f"{hut_info.place(site)}：{msg}" for site, msg in sorted(errors.items())))
    sites = list(dict.fromkeys(c.slot.site for c in changes))
    if sites:
        parts.append("<b>查詢頁</b>\n" + "\n".join(
            f"{hut_info.place(s)}：{hut_info.QUERY_URL[s]}" for s in sites))
    return subject, "\n\n".join(parts)


def line_summary(slots, checked_at, errors):
    """slots 已由呼叫端篩過「在監控範圍內」。列週末／連假夠 2 人的日子。"""
    lines = [f"山屋（{checked_at:%m/%d %H:%M} 查）"]
    by_unit = {}
    for s in sorted(slots, key=lambda s: s.day):
        if is_good_night(s.day) and _enough(s):
            by_unit.setdefault(hut_info.display(s), []).append(s)
    if by_unit:
        for unit, ss in by_unit.items():
            days = "、".join(f"{fmt_day(s.day)} {s.remaining}{_word(s)}" for s in ss[:LINE_MAX_PER_UNIT])
            lines.append(f"{unit}：{days}")
    else:
        lines.append("目前沒有週末／連假夠 2 人的空位")
    if errors:
        lines.append("沒查到：" + "、".join(hut_info.place(s) for s in sorted(errors)))
    lines.append("完整內容看 Gmail")
    return "\n".join(lines)
