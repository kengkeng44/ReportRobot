"""待辦清單只列一個月內的（沒日期、P0 例外）。"""

from datetime import date

import flex_builder
import personal
import todo_widget

TODAY = date(2026, 11, 7)


def _t(id, start=None, priority=None, end=None):
    return {"id": id, "text": f"事{id}", "start": start, "end": end, "priority": priority}


def test_in_horizon_rules():
    assert personal.in_horizon(_t(1), TODAY)                                # 沒日期
    assert personal.in_horizon(_t(2, date(2026, 12, 7)), TODAY)             # 剛好 30 天
    assert not personal.in_horizon(_t(3, date(2026, 12, 8)), TODAY)         # 31 天
    assert personal.in_horizon(_t(4, date(2028, 1, 1), "P0"), TODAY)        # P0 例外
    assert personal.in_horizon(_t(5, date(2026, 10, 1)), TODAY)             # 逾期
    assert personal.in_horizon(_t(6, None, end=date(2026, 11, 20)), TODAY)  # 只有截止日


def _texts(node):
    if isinstance(node, dict):
        out = [node["text"]] if isinstance(node.get("text"), str) else []
        for v in node.values():
            out += _texts(v)
        return out
    if isinstance(node, list):
        return [x for v in node for x in _texts(v)]
    return []


def test_flex_hides_far_and_notes_count(monkeypatch):
    monkeypatch.setattr("tz_utils.today_tpe", lambda: TODAY)
    msg = flex_builder.todo_list_flex([_t(1), _t(2, date(2028, 1, 1)), _t(3, date(2029, 1, 1))])
    texts = _texts(msg)
    assert "⬜ 事1" in texts and "⬜ 事2" not in texts
    assert any("還有 2 件一個月後" in s for s in texts)


def test_flex_all_far_shows_empty_with_note(monkeypatch):
    monkeypatch.setattr("tz_utils.today_tpe", lambda: TODAY)
    texts = _texts(flex_builder.todo_list_flex([_t(2, date(2028, 1, 1))]))
    assert "目前沒有待辦事項" in texts and any("還有 1 件" in s for s in texts)


def test_widget_ignores_far_items():
    t = _t(9, date(2028, 1, 1))
    t["project"] = "喵管家"
    out = todo_widget.build([t], [], TODAY)
    assert not out.get("projects")


def test_flex_hides_my_project_category(monkeypatch):
    monkeypatch.setattr("tz_utils.today_tpe", lambda: TODAY)
    items = [dict(_t(1), category="生活"), dict(_t(2), category="我的專案"),
             dict(_t(3), category="我的專案")]
    msg = flex_builder.todo_list_flex(items)
    texts = _texts(msg)
    assert "⬜ 事1" in texts and "⬜ 事2" not in texts and "⬜ 事3" not in texts
    assert any("另有 2 件「我的專案」" in t for t in texts)
    assert any("共 1 筆待完成" in t for t in texts)
