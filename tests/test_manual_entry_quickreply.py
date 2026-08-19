"""「記一筆」不帶參數時的兩段式 Quick Reply：點品項 → 點金額 → 記完。

原本要打「記一筆 午餐 120」，手機上打中文是最大的摩擦。
改成按「記一筆」→ 跳常記品項 → 點「午餐」→ 跳常用金額 → 點「120」。

按鈕送出的文字本身攜帶進度（記一筆 / 記一筆 午餐 / 記一筆 午餐 120），
所以三種狀態靠 arg 內容判斷，不需要對話狀態機。
"""

import pytest

import command_router as cr
import finance_report as fr


def _txn(shop, amount, source="手動"):
    return {"date": "2026-08-19", "amount": amount, "shop": shop,
            "category": "餐飲", "direction": "支出", "currency": "TWD",
            "status": "已結帳", "source": source}


# ── 分類判斷 ─────────────────────────────────────────────

@pytest.mark.parametrize("shop", ["早餐", "午餐", "晚餐", "咖啡", "飲料", "點心"])
def test_default_items_are_food(shop):
    assert fr.guess_category(shop) == "餐飲"


def test_food_keyword_inside_a_longer_name():
    """「跟同事吃午餐」也該是餐飲 —— 手打時不會剛好只打兩個字。"""
    assert fr.guess_category("跟同事吃午餐") == "餐飲"


def test_unknown_item_is_other():
    """國泰分類裡沒有「交通」，不自創類別（notion_db.py:90）。"""
    assert fr.guess_category("搭車") == "其他"


def test_blank_is_other():
    assert fr.guess_category("") == "其他"
    assert fr.guess_category(None) == "其他"


def test_parse_manual_uses_guessed_category():
    assert fr.parse_manual("午餐 120")["category"] == "餐飲"
    assert fr.parse_manual("搭車 30")["category"] == "其他"


# ── 常記品項 ─────────────────────────────────────────────

def test_frequent_items_ranks_by_count():
    txns = [_txn("午餐", 120), _txn("午餐", 100), _txn("午餐", 150),
            _txn("咖啡", 55), _txn("咖啡", 65),
            _txn("搭車", 30)]

    assert fr.frequent_expense_items(txns, limit=3, pad=False) == [
        "午餐", "咖啡", "搭車"]


def test_frequent_items_ignores_auto_synced():
    """信用卡同步的店名放按鈕上沒意義，還會被 LINE 截成半截。"""
    txns = [_txn("全聯福利中心－板橋板新", 361, source="國泰消費彙整"),
            _txn("全聯福利中心－板橋板新", 210, source="國泰消費彙整"),
            _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False) == ["午餐"]


def test_frequent_items_ties_keep_first_seen_order():
    """同次數時位置要穩定：按鈕每次都在跳比排序不準更難用。"""
    txns = [_txn("咖啡", 55), _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False) == ["咖啡", "午餐"]


def test_frequent_items_ignores_blank_names():
    txns = [_txn("", 100), _txn("   ", 100), _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False) == ["午餐"]


def test_frequent_items_pads_with_defaults():
    """第一天沒歷史，給空按鈕列等於這個功能不存在。"""
    assert fr.frequent_expense_items([], limit=6) == [
        "午餐", "晚餐", "早餐", "咖啡", "飲料", "點心"]


def test_padding_never_duplicates_history():
    txns = [_txn("咖啡", 55)]

    out = fr.frequent_expense_items(txns, limit=6)

    assert out[0] == "咖啡"
    assert out.count("咖啡") == 1
    assert len(out) == 6


def test_history_always_outranks_padding():
    txns = [_txn("搭車", 30)]

    assert fr.frequent_expense_items(txns, limit=6)[0] == "搭車"


def test_frequent_items_respects_limit():
    txns = [_txn(f"品項{i}", 100) for i in range(20)]

    assert len(fr.frequent_expense_items(txns, limit=6)) == 6
