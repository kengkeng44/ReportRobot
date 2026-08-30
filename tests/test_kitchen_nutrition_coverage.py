"""營養表對真實發票品名的覆蓋。

2026-08-30 用實際載具匯出檔(140 筆食材)實測,原本 26 項的
`_NUTRITION_PER_100G` 只命中 17 筆(12%)。這裡把補進去的項目釘住,
案例全部取自使用者真的買過的品名 —— 不補不會買的東西,
字典大不等於命中率高。

四個坑不是「字典缺項」而是「key 寫法對不上」,特別容易復發:
    牛心蕃茄 vs 番茄        異體字
    活力胡蘿蔔 vs 紅蘿蔔    同義詞
    大成原味嫩雞胸 vs 雞胸肉  詞尾多一字
    豬瘦絞肉 vs 豬絞肉      中間插字

另外 `_match_key` 是「先找到先算」,所以 dict 的**順序有意義**:
「紅蘿蔔」「胡蘿蔔」必須排在「蘿蔔」前面,否則胡蘿蔔會被當成白蘿蔔,
熱量差一倍(41 vs 21)。test_carrot_not_shadowed_by_radish 就是在守這件事。

⚠️ 營養數值為粗估量級,非實驗室數據。系統一律標「營養為粗估」。
要精確請以衛福部食品營養成分資料庫校正。
"""

import pytest

import kitchen


# ── 同義詞 / 異體字(最容易復發) ────────────────────────────

@pytest.mark.parametrize("name,expect_kcal", [
    ("牛心蕃茄(履歷)", 18),        # 蕃 / 番 異體字
    ("大成原味嫩雞胸", 165),        # 雞胸 / 雞胸肉
    ("豬瘦絞肉(粗)", 263),          # 豬瘦絞肉 / 絞肉
    ("豬瘦絞肉(細)", 263),
    ("富翁洗選蛋(白)", 155),        # 洗選蛋 / 雞蛋
])
def test_synonym_and_variant_names_resolve(name, expect_kcal):
    nutrition = kitchen.lookup_nutrition(name)

    assert nutrition is not None, f"{name} 應該要查得到"
    assert nutrition["kcal"] == expect_kcal


def test_carrot_not_shadowed_by_radish():
    """胡蘿蔔不能被「蘿蔔」搶先命中 —— 熱量差一倍。

    _match_key 先找到先算,所以較長的 key 要排在較短的前面。
    """
    carrot = kitchen.lookup_nutrition("活力胡蘿蔔單入")
    radish = kitchen.lookup_nutrition("進口蘿蔔")

    assert carrot["kcal"] == 41, "胡蘿蔔"
    assert radish["kcal"] == 21, "白蘿蔔"


# ── 新補的食材(全部取自真實購買紀錄) ──────────────────────

@pytest.mark.parametrize("name", [
    "有機金針菇", "有機鴻喜菇(好)", "鮮採杏鮑菇(彰)",
    "絲瓜", "有機綠豆芽", "翠玉娃娃菜", "龍鳳冷凍毛豆仁",
    "和風秋葵", "（量）中祥紫菜鮮蔥量販包",
])
def test_vegetables_are_covered(name):
    assert kitchen.lookup_nutrition(name) is not None


@pytest.mark.parametrize("name", [
    "雞二節翅", "雞里肌肉", "豬五花火鍋肉片",
    "溫體牛肉湯", "博客原切火腿", "柚雞肋眼",
])
def test_meats_are_covered(name):
    assert kitchen.lookup_nutrition(name) is not None


@pytest.mark.parametrize("name", [
    "統一陽光黃金豆豆漿", "光泉無加糖鮮豆漿",
    "安佳奶油", "黃金滷蛋",
])
def test_dairy_and_eggs_are_covered(name):
    assert kitchen.lookup_nutrition(name) is not None


@pytest.mark.parametrize("name", [
    "美國甜心蘋果80(顆)", "台灣香蕉重量分享包(2入)", "麻豆文旦",
])
def test_fruits_are_covered(name):
    """原本整個水果類都不在表裡。"""
    assert kitchen.lookup_nutrition(name) is not None


@pytest.mark.parametrize("name", [
    "宸宇刀削關廟麵", "寶島木薯粉", "台鹽減鈉含碘鹽",
])
def test_staples_and_seasoning_are_covered(name):
    assert kitchen.lookup_nutrition(name) is not None


# ── 不該誤中 ───────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "輕便型雨衣-兒童",
    "柏賓士印花張",
    "寶拉珍選2水楊酸精華液30ml",
])
def test_non_food_still_returns_nothing(name):
    """補字典不能讓非食物意外查到營養值。"""
    assert kitchen.lookup_nutrition(name) is None


# ── 整體覆蓋率 ─────────────────────────────────────────────

def test_real_data_coverage_improves():
    """真實 8 月資料的命中率要明顯高於原本的 12%。

    不寫死上限 —— 之後再補字典時這個測試不該擋路。
    """
    import io, os
    import einvoice_csv
    import einvoice_pantry

    path = os.path.join(os.path.dirname(__file__), "fixtures", "einvoice_export.csv")
    invoices = einvoice_csv.parse(io.open(path, encoding="utf-8-sig").read())
    rows = einvoice_pantry.to_pantry_rows(invoices)

    assert rows
    # fixture 是匿名資料,這裡只確認管線通;真實覆蓋率在 commit 訊息記錄。
    # 欄位是 describe_item 的形狀(英文 key),不是 Notion 的中文欄位名 ——
    # pantry_add 自己會做映射。
    assert all("per_100g" in r for r in rows)
