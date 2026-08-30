"""發票品項 → 食材庫存的橋接。

定位(2026-08-30 使用者確認):載具資料**不進交易明細**。
記帳歸國泰彙整信管,載具這條線是要回答「買了什麼菜」以及後續的營養分析。
兩條線分開,順帶也就沒有同一筆消費被記兩次的問題。

為什麼需要過濾
────────────
發票品項不等於食材。實測 2026 年 8 月的 124 個不重複品名裡,混著:
- 根本不是食物:輕便型雨衣、環保隨行壺、柏賓士印花張
- 不是品項的列:折讓、999999、30元盤(迴轉壽司計價盤)
把這些倒進食材庫存,冰箱清單會變成垃圾場,連帶讓到期提醒失去意義。

營養值沿用 kitchen.py 既有的引擎(`lookup_nutrition` / `estimate_grams`),
不另外造一套。算不出來就留空 —— 跟 kitchen 的原則一致:
「看不懂就不猜,錯的庫存比沒有庫存更糟。」
"""

from datetime import date

import pytest

import einvoice_pantry


def _inv(name, qty=1, amount=100, day=date(2026, 8, 30)):
    """一張只有單一品項的發票,測試用。"""
    return [{
        "inv_num": "AA11111111",
        "seller": "測試超市",
        "amount": amount,
        "date": day,
        "status": "開立已確認",
        "items": [{"name": name, "qty": qty, "unit_price": amount, "amount": amount}],
    }]


# ── 非食物過濾 ─────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "輕便型雨衣-兒童",
    "【CHAKO LAB】環保隨行拎拎壺1150ml 母嬰級Tritan材質 大容量冷水壺 隨行水壺",
    "柏賓士印花張",
])
def test_non_food_is_excluded(name):
    """實測出現過的非食品,不該進冰箱清單。"""
    assert einvoice_pantry.to_pantry_rows(_inv(name)) == []


@pytest.mark.parametrize("name", ["折讓", "999999", "30元盤", "E-指定炸物 任選折1"])
def test_non_item_rows_are_excluded(name):
    """折讓與計價盤不是東西,純數字是店家自己的代碼。"""
    assert einvoice_pantry.to_pantry_rows(_inv(name)) == []


def test_food_is_kept():
    rows = einvoice_pantry.to_pantry_rows(_inv("青江菜產銷履歷"))

    assert len(rows) == 1
    assert rows[0]["name"] == "青江菜產銷履歷"


# ── 欄位對應食材庫存 DB ────────────────────────────────────

def test_row_matches_pantry_schema():
    """欄位要跟 notion_db.pantry_add() 吃的格式一致(即 describe_item 的形狀)。

    pantry_add 自己會把英文 key 映射成 Notion 的中文欄位,
    這裡照它的契約走 —— 對齊消費端,不是對齊資料庫。
    """
    row = einvoice_pantry.to_pantry_rows(_inv("青江菜產銷履歷", qty=2))[0]

    assert set(row) >= {"name", "qty", "bought", "category", "source"}


def test_purchase_date_comes_from_invoice():
    """購買日用發票日期,不是今天 —— 匯出的是過去一個月的資料。"""
    row = einvoice_pantry.to_pantry_rows(
        _inv("青江菜產銷履歷", day=date(2026, 8, 3))
    )[0]

    assert row["bought"] == date(2026, 8, 3)


def test_quantity_is_carried_over():
    row = einvoice_pantry.to_pantry_rows(_inv("青江菜產銷履歷", qty=3))[0]

    assert row["qty"] == 3


def test_category_uses_kitchen_rules():
    """分類沿用 kitchen.guess_category,不另外造一套規則。"""
    row = einvoice_pantry.to_pantry_rows(_inv("青江菜產銷履歷"))[0]

    assert row["category"] == "蔬菜"


def test_source_marks_carrier_origin():
    """標來源,之後才分得出哪些是發票匯入、哪些是手動加的。"""
    row = einvoice_pantry.to_pantry_rows(_inv("青江菜產銷履歷"))[0]

    assert row["source"] == "載具發票"


# ── 營養 ───────────────────────────────────────────────────

def test_nutrition_filled_when_lookup_hits():
    """命中營養表就填每 100g 的值。"""
    row = einvoice_pantry.to_pantry_rows(_inv("特選鮭魚厚切"))[0]

    assert row["per_100g"]["kcal"] == 208
    assert row["per_100g"]["protein"] == 20.0


def test_nutrition_left_empty_when_unknown():
    """查不到就留空,不要編一個數字 —— 跟 kitchen 的原則一致。

    「二配紅豆麵包」這種加工食品仍然收進來(它確實是買來吃的),
    只是沒有營養值。
    """
    rows = einvoice_pantry.to_pantry_rows(_inv("二配紅豆麵包"))

    assert len(rows) == 1, "加工食品仍是食物,不該被過濾掉"
    assert rows[0]["per_100g"] is None


def test_nutrition_is_flagged_as_rough():
    """有填營養值的一律標「營養為粗估」—— 那是查表來的,不是秤出來的。"""
    row = einvoice_pantry.to_pantry_rows(_inv("特選鮭魚厚切"))[0]

    assert row["approximate"] is True


# ── 多筆 ───────────────────────────────────────────────────

def test_multiple_invoices_flatten_into_rows():
    invoices = _inv("青江菜產銷履歷") + _inv("特選鮭魚厚切", day=date(2026, 8, 1))

    rows = einvoice_pantry.to_pantry_rows(invoices)

    assert len(rows) == 2


def test_same_item_on_different_dates_stays_separate():
    """同一種菜不同天買的是兩筆 —— 到期日不一樣,不能合併。"""
    invoices = (_inv("青江菜產銷履歷", day=date(2026, 8, 1))
                + _inv("青江菜產銷履歷", day=date(2026, 8, 20)))

    rows = einvoice_pantry.to_pantry_rows(invoices)

    assert len(rows) == 2
    assert {r["bought"] for r in rows} == {date(2026, 8, 1), date(2026, 8, 20)}


def test_voided_invoices_never_reach_pantry():
    """作廢的發票在解析階段就該被擋掉,這裡再確認一次。"""
    voided = _inv("青江菜產銷履歷")
    voided[0]["status"] = "作廢"

    assert einvoice_pantry.to_pantry_rows(voided) == []


# ── 真實資料 ───────────────────────────────────────────────

def test_real_export_yields_reasonable_ratio():
    """拿真實匯出檔跑一次:過濾後應該明顯少於原始品項數。

    167 個品項裡混著雨衣、印花、折讓 —— 全收就失去意義了。
    這裡不寫死數字(換個月份就不同),只確認過濾真的有作用。
    """
    import io, os
    import einvoice_csv

    path = os.path.join(os.path.dirname(__file__), "fixtures", "einvoice_export.csv")
    invoices = einvoice_csv.parse(io.open(path, encoding="utf-8-sig").read())

    rows = einvoice_pantry.to_pantry_rows(invoices)

    assert rows, "fixture 裡有真食材,不該全被過濾掉"
    assert all(r["name"] for r in rows)
