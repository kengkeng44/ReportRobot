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


# ── 克數換算 ──────────────────────────────────────────────

@pytest.mark.parametrize("name,qty,unit,expected", [
    ("豬絞肉", 300, "克", 300),
    ("豬絞肉", 300, "g", 300),
    ("米", 2.5, "公斤", 2500),
    ("米", 1, "kg", 1000),
    ("雞蛋", 10, "顆", 500),          # 一顆約 50g
    ("高麗菜", 1, "顆", 1000),
    ("雞胸肉", 2, "片", 300),
])
def test_estimate_grams(name, qty, unit, expected):
    assert kitchen.estimate_grams(name, qty, unit) == expected


def test_estimate_grams_falls_back_to_category_typical():
    """沒個別登記的蔬菜，用該分類的典型重量估。"""
    grams = kitchen.estimate_grams("青江菜", 2, "把")
    assert grams is not None and grams > 0


def test_estimate_grams_unknown_returns_none():
    """猜不出重量就回 None —— 編一個克數出來會讓營養全錯。"""
    assert kitchen.estimate_grams("嘎嘎嘎", 1, "顆") is None


def test_estimate_grams_without_unit_returns_none():
    assert kitchen.estimate_grams("嘎嘎嘎", 1, "") is None


# ── 營養粗估 ──────────────────────────────────────────────

def test_lookup_nutrition_returns_per_100g():
    n = kitchen.lookup_nutrition("雞胸肉")

    assert n is not None
    assert 100 < n["kcal"] < 250
    assert n["protein"] > 15          # 雞胸主要是蛋白質


def test_lookup_nutrition_unknown_returns_none():
    """查不到就留空，不要猜數值。"""
    assert kitchen.lookup_nutrition("嘎嘎嘎") is None


def test_nutrition_is_scaled_by_grams():
    per100 = {"kcal": 165, "protein": 31, "carb": 0, "fat": 3.6}

    got = kitchen.scale_nutrition(per100, 300)

    assert got["kcal"] == pytest.approx(495)
    assert got["protein"] == pytest.approx(93)


def test_scale_nutrition_handles_missing_inputs():
    assert kitchen.scale_nutrition(None, 300) is None
    assert kitchen.scale_nutrition({"kcal": 100}, None) is None


def test_describe_item_combines_everything():
    """一個品項從輸入到可寫入 Notion 的完整欄位。"""
    got = kitchen.describe_item("雞胸肉", 2, "片")

    assert got["category"] == "肉類"
    assert got["storage"] == "冷藏"
    assert got["grams"] == 300
    assert got["nutrition"]["kcal"] > 0
    assert got["approximate"] is True, "營養是粗估，要標記出來"


def test_describe_item_unknown_keeps_name_only():
    got = kitchen.describe_item("嘎嘎嘎", 1, "顆")

    assert got["category"] is None
    assert got["grams"] is None
    assert got["nutrition"] is None


def test_expiring_soon_filters_and_sorts():
    pantry = _pantry(("A", 5), ("B", 1), ("C", 2), ("D", -1))

    got = kitchen.expiring_soon(pantry, threshold_days=3)

    assert [p["name"] for p in got] == ["D", "B", "C"]
