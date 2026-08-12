"""分頁式 Rich Menu 的定義正確性。

HTTP 呼叫不測（那是 LINE 的事），只測會讓使用者卡住的結構問題：
格數不對、切換到不存在的分頁、子選單沒有返回鍵。
"""

import pytest

import setup_richmenu as rm


def test_every_menu_has_exactly_six_cells():
    """版型是 3×2，多一個少一個都會讓圖跟熱區對不上。"""
    for key, menu in rm.MENUS.items():
        assert len(menu["cells"]) == rm.COLS * rm.ROWS, f"{key} 格數不對"


def test_main_menu_exists():
    assert "main" in rm.MENUS


def test_every_switch_target_exists():
    """切到不存在的分頁 = 使用者按了沒反應。"""
    for key, menu in rm.MENUS.items():
        for label, _sub, _color, action in menu["cells"]:
            kind, target = action
            if kind == "switch":
                assert target in rm.MENUS, f"{key} 的「{label}」切到不存在的 {target}"


def test_sub_menus_can_go_back_to_main():
    """沒有返回鍵的話，使用者會被困在子選單裡。"""
    for key, menu in rm.MENUS.items():
        if key == "main":
            continue
        targets = [a[1] for _l, _s, _c, a in menu["cells"] if a[0] == "switch"]
        assert "main" in targets, f"{key} 沒有回主選單的格子"


def test_main_menu_reaches_every_sub_menu():
    """每個子選單都要進得去，不然做了也沒人看得到。"""
    reachable = {t for _l, _s, _c, (k, t) in rm.MENUS["main"]["cells"] if k == "switch"}
    assert reachable == set(rm.MENUS) - {"main"}


def _is_pictograph(ch):
    """emoji / 箭頭 / 雜項符號 —— CJK 字型畫不出來，會變豆腐字。"""
    cp = ord(ch)
    return cp >= 0x1F000 or 0x2190 <= cp <= 0x2BFF or cp == 0xFE0F


def test_labels_have_no_emoji():
    """PNG 是用 CJK 字型畫的，沒有彩色 emoji 字型，放了就是豆腐字。"""
    for key, menu in rm.MENUS.items():
        for label, sub, _c, _a in menu["cells"]:
            bad = [ch for ch in label + sub if _is_pictograph(ch)]
            assert not bad, f"{key} 的「{label}」含畫不出來的符號 {bad}"


# ── 熱區 ──────────────────────────────────────────────────

def test_build_areas_covers_whole_image():
    areas = rm.build_areas(rm.MENUS["main"]["cells"])

    assert len(areas) == 6
    total = sum(a["bounds"]["width"] * a["bounds"]["height"] for a in areas)
    assert total == rm.W // rm.COLS * rm.COLS * (rm.H // rm.ROWS) * rm.ROWS


def test_build_areas_maps_message_action():
    cells = [("待辦", "TODO", "#000000", ("message", "/待辦"))] * 6
    areas = rm.build_areas(cells)

    assert areas[0]["action"] == {"type": "message", "text": "/待辦"}


def test_build_areas_maps_switch_action():
    cells = [("財務", "FINANCE", "#000000", ("switch", "finance"))] * 6
    areas = rm.build_areas(cells)

    action = areas[0]["action"]
    assert action["type"] == "richmenuswitch"
    assert action["richMenuAliasId"] == "finance"


def test_build_areas_maps_prompt_to_keyboard_with_prefill():
    """按下去要打開鍵盤並預填開頭，使用者只補後半段。"""
    cells = [("買了", "BUY", "#000000", ("prompt", "買了 "))] * 6
    areas = rm.build_areas(cells)

    action = areas[0]["action"]
    assert action["type"] == "postback"
    assert action["inputOption"] == "openKeyboard"
    assert action["fillInText"] == "買了 "


def test_prompt_has_no_display_text():
    """帶 displayText 會在聊天室多出一則沒意義的回音。"""
    cells = [("買了", "BUY", "#000000", ("prompt", "買了 "))] * 6
    assert "displayText" not in rm.build_areas(cells)[0]["action"]


def test_prompt_prefill_within_line_limit():
    """LINE 對 fillInText 的上限是 300 字。"""
    for key, menu in rm.MENUS.items():
        for label, _s, _c, (kind, param) in menu["cells"]:
            if kind == "prompt":
                assert len(param) <= 300, f"{key} 的「{label}」預填字串過長"


def test_input_needing_cells_use_prompt_not_message():
    """需要接著打字的格子，送死指令沒有意義 —— 要用 prompt。"""
    needs_input = {"買了", "記一筆", "查個股"}
    for menu in rm.MENUS.values():
        for label, _s, _c, (kind, _p) in menu["cells"]:
            if label in needs_input:
                assert kind == "prompt", f"「{label}」應該用 prompt"


# ── 字型 ──────────────────────────────────────────────────

def test_missing_font_raises_instead_of_silent_blank_menu(monkeypatch):
    """找不到 CJK 字型時要大聲失敗。

    舊版會 fallback 到 ImageFont.load_default()（固定小點陣字型），
    結果默默上傳一張只有色塊、沒有字的選單 —— 這是實際發生過的事故。
    """
    monkeypatch.setattr(rm, "_CHINESE_FONT_CANDIDATES", ["/nope/none.ttc"])

    with pytest.raises(rm.NoChineseFontError):
        rm.find_font(280)


def test_generate_image_propagates_font_error(monkeypatch, tmp_path):
    monkeypatch.setattr(rm, "_CHINESE_FONT_CANDIDATES", ["/nope/none.ttc"])

    with pytest.raises(rm.NoChineseFontError):
        rm.generate_image(rm.MENUS["main"]["cells"], str(tmp_path / "x.png"))


def test_build_areas_rejects_unknown_action_kind():
    cells = [("壞掉", "BAD", "#000000", ("teleport", "x"))] * 6

    with pytest.raises(ValueError):
        rm.build_areas(cells)


def test_grid_positions_are_row_major():
    cells = [(str(i), "X", "#000000", ("message", str(i))) for i in range(6)]
    areas = rm.build_areas(cells)

    assert (areas[0]["bounds"]["x"], areas[0]["bounds"]["y"]) == (0, 0)
    assert (areas[2]["bounds"]["x"], areas[2]["bounds"]["y"]) == (2 * rm.CELL_W, 0)
    assert (areas[3]["bounds"]["x"], areas[3]["bounds"]["y"]) == (0, rm.CELL_H)
