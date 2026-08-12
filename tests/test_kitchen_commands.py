"""煮飯指令的解析與權限。

Rich Menu 的煮飯分頁會送出 /庫存 /快過期 /煮什麼 /買了 /採購，
所以這幾個字串一定要解析得到，否則按了沒反應。
"""

import pytest

import command_router as cr


@pytest.mark.parametrize("text,expected", [
    ("/庫存", ("pantry_list", None)),
    ("庫存", ("pantry_list", None)),
    ("/快過期", ("pantry_expiring", None)),
    ("/煮什麼", ("cook_what", None)),
    ("煮什麼", ("cook_what", None)),
    ("/採購", ("shopping_list", None)),
])
def test_menu_buttons_all_parse(text, expected):
    """Rich Menu 每一格送出的字串都必須解析得到。"""
    assert cr.parse(text) == expected


def test_buy_with_items():
    kind, arg = cr.parse("/買了 高麗菜1顆 番茄5顆")
    assert kind == "pantry_add"
    assert arg == "高麗菜1顆 番茄5顆"


def test_buy_without_items_still_parses():
    """Rich Menu 的「買了」格子送出的是不帶參數的 /買了，
    要能解析並回提示，不能靜默無反應。"""
    assert cr.parse("/買了") == ("pantry_add", None)


def test_consume():
    assert cr.parse("/用掉 高麗菜") == ("pantry_consume", "高麗菜")


def test_consume_without_name_is_prompt():
    assert cr.parse("/用掉") == ("pantry_consume", None)


def test_kitchen_kinds_are_personal_only():
    """庫存是個人資料，不該在群組裡被查。"""
    for kind in ("pantry_list", "pantry_expiring", "cook_what",
                 "pantry_add", "pantry_consume", "shopping_list"):
        assert kind in cr._PERSONAL_KINDS


def test_stock_lookup_still_wins_for_tickers():
    """加了中文指令後，原本的股票查詢不能被吃掉。"""
    assert cr.parse("2330") == ("stock", "2330")
    assert cr.parse("/2330") == ("stock", "2330")


def test_unknown_chinese_still_falls_through_to_free_query():
    kind, _arg = cr.parse("/今天天氣如何")
    assert kind in ("free_query", "stock")


def test_bare_chinese_without_prefix_is_ignored():
    """沒前綴的閒聊不該觸發任何指令（家人群組會誤觸）。"""
    assert cr.parse("我今天買了菜") is None


# ── 端對端：按下按鈕後實際回什麼 ──────────────────────────

PERSONAL = {"source_type": "user", "user_id": "U1"}


@pytest.fixture
def fake_notion(monkeypatch):
    """把 notion_db 的讀寫換成記憶體版，測整條指令流程。"""
    import notion_db

    store = {"rows": [], "recipes": []}

    def _add(item):
        row = {
            "page_id": f"p{len(store['rows'])}",
            "name": item["name"], "qty": item.get("qty"),
            "unit": item.get("unit"), "category": item.get("category"),
            "grams": item.get("grams"), "days_left": 2,
        }
        store["rows"].append(row)
        return row["page_id"]

    monkeypatch.setattr(notion_db, "pantry_add", _add)
    monkeypatch.setattr(notion_db, "pantry_load", lambda status="在庫": store["rows"])
    monkeypatch.setattr(notion_db, "recipes_load", lambda p=None: store["recipes"])
    monkeypatch.setattr(
        notion_db, "pantry_set_status",
        lambda pid, s: store["rows"].__setitem__(
            next(i for i, r in enumerate(store["rows"]) if r["page_id"] == pid),
            {**next(r for r in store["rows"] if r["page_id"] == pid), "status": s},
        ) or True,
    )
    return store


def test_buy_flow_writes_and_reports(fake_notion):
    reply = cr.handle("/買了 高麗菜1顆 雞胸肉2片", PERSONAL)

    assert "已加入 2 樣" in reply
    assert "高麗菜" in reply and "雞胸肉" in reply
    assert [r["name"] for r in fake_notion["rows"]] == ["高麗菜", "雞胸肉"]


def test_buy_flow_reports_unparseable_items(fake_notion):
    reply = cr.handle("/買了 高麗菜1顆 ???", PERSONAL)

    assert "已加入 1 樣" in reply
    assert "看不懂" in reply and "???" in reply


def test_buy_without_args_returns_usage(fake_notion):
    """Rich Menu 的『買了』格子按下去會送不帶參數的 /買了。"""
    reply = cr.handle("/買了", PERSONAL)

    assert "高麗菜1顆" in reply, "要給使用者範例，不能只說格式錯誤"
    assert fake_notion["rows"] == []


def test_pantry_list_flow(fake_notion):
    cr.handle("/買了 高麗菜1顆", PERSONAL)

    reply = cr.handle("/庫存", PERSONAL)

    assert "高麗菜" in reply and "剩 2 天" in reply


def test_empty_pantry_tells_user_how_to_start(fake_notion):
    reply = cr.handle("/庫存", PERSONAL)
    assert "買了" in reply


def test_cook_what_without_recipes_explains(fake_notion):
    cr.handle("/買了 菠菜1把", PERSONAL)

    reply = cr.handle("/煮什麼", PERSONAL)

    assert "食譜庫是空的" in reply


def test_cook_what_recommends(fake_notion):
    cr.handle("/買了 菠菜1把 板豆腐1盒", PERSONAL)
    fake_notion["recipes"] = [
        {"name": "菠菜豆腐湯", "ingredients": ["菠菜", "板豆腐"], "minutes": 20},
    ]

    reply = cr.handle("/煮什麼", PERSONAL)

    assert "菠菜豆腐湯" in reply
    assert "用掉 2 樣" in reply


def test_consume_flow(fake_notion):
    cr.handle("/買了 高麗菜1顆", PERSONAL)

    reply = cr.handle("/用掉 高麗菜", PERSONAL)

    assert "已把" in reply and "高麗菜" in reply


def test_consume_missing_item(fake_notion):
    reply = cr.handle("/用掉 不存在的菜", PERSONAL)
    assert "找不到" in reply


@pytest.mark.parametrize("text,expected", [
    ("/本月支出", ("fin_spending", None)),
    ("/最近交易", ("fin_recent", None)),
    ("/卡費", ("fin_card", None)),
    ("/淨值", ("fin_networth", None)),
])
def test_finance_menu_buttons_all_parse(text, expected):
    """財務分頁每一格送出的字串都必須解析得到，否則按了沒反應。"""
    assert cr.parse(text) == expected


def test_manual_entry_parses_with_args():
    assert cr.parse("記一筆 午餐 120") == ("fin_manual", "午餐 120")


def test_manual_entry_without_args_is_prompt():
    assert cr.parse("記一筆") == ("fin_manual", None)


def test_finance_kinds_are_personal_only():
    for kind in ("fin_spending", "fin_recent", "fin_card",
                 "fin_networth", "fin_manual"):
        assert kind in cr._PERSONAL_KINDS


def test_prompt_postback_is_silent():
    """Rich Menu 的「買了」是 postback(prompt=買了)，只為了打開鍵盤預填文字。
    機器人不該回任何訊息，不然聊天室會多一則廢話。"""
    assert cr.handle_postback("prompt=買了", "U1") is None
    assert cr.handle_postback("prompt=記一筆", "U1") is None


def test_kitchen_blocked_in_group(fake_notion):
    """群組裡查庫存要被擋掉。"""
    reply = cr.handle("/庫存", {"source_type": "group", "user_id": "U1"})
    assert reply == cr.PERSONAL_ONLY_MSG
