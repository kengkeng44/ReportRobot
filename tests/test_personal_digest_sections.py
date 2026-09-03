"""個人版每日信的區塊組裝與順序。

使用者指定順序：**待辦 → 財務 → 買菜**（2026-08-26），跟
digest_preview.html 範本原本的順序不同。範本的持股概況 / LINE 餘額 /
載具品項這次不接：持股要抓 Gmail 會讓每日信變慢又多一個失敗點，
載具品項還沒有電子發票 AppID，接了也是空的。

天氣範本裡沒有，但現有信件有 —— 保留並排在最後，移除功能不在這次要求裡。
"""

import daily_report as dr


def _titles(sections):
    return [title for title, _ in sections]


def test_order_is_todo_finance_grocery():
    sections = dr._build_personal_sections(
        todos="⬜ [1] 繳健保費",
        reminders="⏰ 08/27 09:30 → 牙醫回診",
        monthly_detail="■ 08/26\n　・全家　NT$85",
        spending="最新消費 NT$85",
        kitchen="高麗菜　今天到期",
        weather="板橋 28°C",
        phrases=None,
    )
    titles = _titles(sections)

    todo_at = min(i for i, t in enumerate(titles) if "待辦" in t or "提醒" in t)
    finance_at = min(i for i, t in enumerate(titles) if "消費" in t)
    grocery_at = min(i for i, t in enumerate(titles) if "冰箱" in t)

    assert todo_at < finance_at < grocery_at


def test_weather_goes_last():
    sections = dr._build_personal_sections(
        todos="x", reminders=None, monthly_detail="y",
        spending=None, kitchen="z", weather="板橋 28°C",
        phrases=None,
    )

    assert "天氣" in _titles(sections)[-1]


def test_empty_sections_are_dropped():
    sections = dr._build_personal_sections(
        todos=None, reminders=None, monthly_detail="y",
        spending=None, kitchen=None, weather=None,
        phrases=None,
    )

    assert len(sections) == 1
    assert "消費" in sections[0][0]


def test_monthly_detail_comes_before_recent_spending():
    """使用者要的是「整個月的花銷」，那是主角；最新消費是補充。"""
    sections = dr._build_personal_sections(
        todos=None, reminders=None,
        monthly_detail="本月明細", spending="最新消費",
        kitchen=None, weather=None,
        phrases=None,
    )
    bodies = [body for _, body in sections]

    assert bodies.index("本月明細") < bodies.index("最新消費")


def test_nothing_at_all_returns_empty_list():
    """全空時回空 list，呼叫端據此不寄信 —— 不要寄一封只有標題的信。"""
    assert dr._build_personal_sections(
        todos=None, reminders=None, monthly_detail=None,
        spending=None, kitchen=None, weather=None,
        phrases=None,
    ) == []


# ── 今日三句(2026-09-01)────────────────────────────────

def test_phrases_go_right_after_todos():
    """學習內容放信尾容易被滑過去,但待辦仍然排最前 —— 那是當天要做的事。"""
    sections = dr._build_personal_sections(
        phrases="[EN] Play it by ear.",
        todos="⬜ [1] 繳健保費",
        reminders="⏰ 08/27 09:30 → 牙醫回診",
        monthly_detail="■ 08/26　・全家　NT$85",
        spending="最新消費 NT$85",
        kitchen="高麗菜　今天到期",
        weather="板橋 28°C",
    )
    titles = _titles(sections)

    assert "待辦" in titles[0]
    phrase_at = min(i for i, t in enumerate(titles) if "三句" in t)
    finance_at = min(i for i, t in enumerate(titles) if "消費" in t)
    assert phrase_at < finance_at


def test_phrases_section_dropped_when_empty():
    sections = dr._build_personal_sections(
        phrases=None, todos="x", reminders=None, monthly_detail=None,
        spending=None, kitchen=None, weather=None,
    )

    assert not any("三句" in t for t in _titles(sections))
