"""煮飯模板：採購輸入解析、分類推測、保存期限推算、煮什麼推薦。

設計原則：看不懂就不猜。寧可回報「這幾樣看不懂」讓使用者補，
也不要塞一筆錯的數量或分類進庫存 —— 錯的庫存比沒有庫存更糟。
"""

from datetime import date

import pytest

import kitchen


# ── 採購輸入解析 ──────────────────────────────────────────

def test_parse_basic_items():
    items, unknown = kitchen.parse_purchase("買了 高麗菜1顆 番茄5顆 雞胸肉2片")

    assert unknown == []
    assert [(i["name"], i["qty"], i["unit"]) for i in items] == [
        ("高麗菜", 1, "顆"),
        ("番茄", 5, "顆"),
        ("雞胸肉", 2, "片"),
    ]


def test_parse_tolerates_spaces_and_separators():
    items, _ = kitchen.parse_purchase("買了 高麗菜 1 顆、番茄 5 顆, 雞蛋 10 顆")

    assert [i["name"] for i in items] == ["高麗菜", "番茄", "雞蛋"]
    assert [i["qty"] for i in items] == [1, 5, 10]


def test_parse_defaults_qty_to_one():
    """沒寫數量就當 1 —— 「買了 醬油」是很自然的講法。"""
    items, _ = kitchen.parse_purchase("買了 醬油")

    assert items[0]["name"] == "醬油"
    assert items[0]["qty"] == 1


def test_parse_handles_decimal_and_weight():
    items, _ = kitchen.parse_purchase("買了 豬絞肉300克 米2.5公斤")

    assert (items[0]["name"], items[0]["qty"], items[0]["unit"]) == ("豬絞肉", 300, "克")
    assert (items[1]["name"], items[1]["qty"], items[1]["unit"]) == ("米", 2.5, "公斤")


def test_parse_reports_unparseable_instead_of_guessing():
    """看不懂的詞要回報，不能默默吞掉或亂猜。"""
    items, unknown = kitchen.parse_purchase("買了 高麗菜1顆 ??? 番茄2顆")

    assert [i["name"] for i in items] == ["高麗菜", "番茄"]
    assert unknown == ["???"]


def test_parse_without_prefix_still_works():
    items, _ = kitchen.parse_purchase("高麗菜1顆")
    assert items[0]["name"] == "高麗菜"


def test_parse_empty_returns_nothing():
    items, unknown = kitchen.parse_purchase("買了")
    assert items == []
    assert unknown == []


# ── 分類推測 ──────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("高麗菜", "蔬菜"), ("菠菜", "蔬菜"), ("番茄", "蔬菜"),
    ("雞胸肉", "肉類"), ("豬絞肉", "肉類"), ("牛小排", "肉類"),
    ("鮭魚", "海鮮"), ("蝦仁", "海鮮"),
    ("雞蛋", "蛋奶"), ("鮮奶", "蛋奶"), ("板豆腐", "蛋奶"),
    ("白米", "主食"), ("義大利麵", "主食"),
    ("醬油", "調味料"), ("鹽", "調味料"),
    ("鮪魚罐頭", "罐頭乾貨"),
])
def test_guess_category(name, expected):
    assert kitchen.guess_category(name) == expected


def test_guess_category_unknown_falls_back():
    """認不出來歸「罐頭乾貨」會誤導保存期限，所以回 None 讓呼叫端決定。"""
    assert kitchen.guess_category("嘎嘎嘎") is None


# ── 保存期限 ──────────────────────────────────────────────

def test_expiry_uses_category_and_storage():
    bought = date(2026, 8, 11)

    # 葉菜冷藏短、冷凍長
    assert kitchen.estimate_expiry(bought, "蔬菜", "冷藏") == date(2026, 8, 14)
    assert kitchen.estimate_expiry(bought, "肉類", "冷藏") == date(2026, 8, 13)
    assert kitchen.estimate_expiry(bought, "肉類", "冷凍") == date(2026, 11, 9)


def test_expiry_unknown_category_returns_none():
    """不知道是什麼就不要編一個到期日出來。"""
    assert kitchen.estimate_expiry(date(2026, 8, 11), None, "冷藏") is None


def test_default_storage_by_category():
    assert kitchen.default_storage("蔬菜") == "冷藏"
    assert kitchen.default_storage("調味料") == "調味櫃"
    assert kitchen.default_storage("罐頭乾貨") == "常溫"


# ── 煮什麼推薦 ────────────────────────────────────────────

def _pantry(*rows):
    return [{"name": n, "days_left": d} for n, d in rows]


def test_recommend_prefers_recipe_using_most_expiring_items():
    pantry = _pantry(("菠菜", 1), ("板豆腐", 2), ("雞胸肉", 10))
    recipes = [
        {"name": "雞胸沙拉", "ingredients": ["雞胸肉"], "minutes": 15},
        {"name": "菠菜豆腐味噌湯", "ingredients": ["菠菜", "板豆腐"], "minutes": 20},
    ]

    best = kitchen.recommend(pantry, recipes, threshold_days=3)

    assert best[0]["name"] == "菠菜豆腐味噌湯"
    assert best[0]["uses_expiring"] == 2


def test_recommend_breaks_tie_by_cooking_time():
    pantry = _pantry(("菠菜", 1), ("雞蛋", 2))
    recipes = [
        {"name": "慢燉菠菜", "ingredients": ["菠菜"], "minutes": 60},
        {"name": "炒蛋", "ingredients": ["雞蛋"], "minutes": 5},
    ]

    best = kitchen.recommend(pantry, recipes, threshold_days=3)

    assert best[0]["name"] == "炒蛋"


def test_recommend_excludes_recipes_missing_ingredients():
    """缺料的食譜不該被推薦 —— 推了也煮不了。"""
    pantry = _pantry(("菠菜", 1))
    recipes = [{"name": "菠菜炒牛肉", "ingredients": ["菠菜", "牛肉"], "minutes": 15}]

    assert kitchen.recommend(pantry, recipes, threshold_days=3) == []


def test_recommend_returns_empty_when_nothing_expiring():
    pantry = _pantry(("菠菜", 30))
    recipes = [{"name": "炒菠菜", "ingredients": ["菠菜"], "minutes": 10}]

    assert kitchen.recommend(pantry, recipes, threshold_days=3) == []


def test_expiring_soon_filters_and_sorts():
    pantry = _pantry(("A", 5), ("B", 1), ("C", 2), ("D", -1))

    got = kitchen.expiring_soon(pantry, threshold_days=3)

    assert [p["name"] for p in got] == ["D", "B", "C"]
