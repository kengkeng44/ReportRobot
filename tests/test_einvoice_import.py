"""載具 CSV 匯入腳本的核心邏輯。

腳本本身只負責 I/O(讀檔、呼叫 Notion、印摘要),會踩到網路的部分不測;
真正需要守的是**去重規則**,因為它決定了重跑會不會炸出兩份庫存。

去重鍵是 (名稱, 購買日) 而不是名稱:
同一種青江菜 8/1 買一次、8/20 又買一次是兩筆不同的庫存,
到期日不一樣,合併會讓先買的那批永遠不過期。
但同一份 CSV 跑兩次,那兩批的 (名稱, 購買日) 完全相同 —— 該擋。
"""

from datetime import date

import pytest

import import_einvoice


def _row(name, bought, qty=1):
    return {"name": name, "qty": qty, "bought": bought, "category": "蔬菜"}


# ── 去重 ───────────────────────────────────────────────────

def test_new_rows_are_all_planned():
    rows = [_row("青江菜", date(2026, 8, 1)), _row("鮭魚", date(2026, 8, 2))]

    to_add, skipped = import_einvoice.plan_import(rows, existing=[])

    assert len(to_add) == 2
    assert skipped == []


def test_existing_same_name_and_date_is_skipped():
    """重跑同一份 CSV 不該再寫一次。"""
    rows = [_row("青江菜", date(2026, 8, 1))]
    existing = [{"name": "青江菜", "bought": date(2026, 8, 1)}]

    to_add, skipped = import_einvoice.plan_import(rows, existing)

    assert to_add == []
    assert len(skipped) == 1


def test_same_name_different_date_is_not_duplicate():
    """8/1 買的跟 8/20 買的是兩批,到期日不同。"""
    rows = [_row("青江菜", date(2026, 8, 20))]
    existing = [{"name": "青江菜", "bought": date(2026, 8, 1)}]

    to_add, skipped = import_einvoice.plan_import(rows, existing)

    assert len(to_add) == 1
    assert skipped == []


def test_existing_without_date_never_blocks():
    """手動加的庫存可能沒填購買日 —— 那種不該擋掉發票匯入。"""
    rows = [_row("青江菜", date(2026, 8, 1))]
    existing = [{"name": "青江菜", "bought": None}]

    to_add, _ = import_einvoice.plan_import(rows, existing)

    assert len(to_add) == 1


def test_duplicate_within_same_file_is_kept():
    """同一天同一家店買兩把青江菜,發票上就是兩列 —— 兩列都要進。

    只擋「已經在 Notion 裡」的,不擋檔案內部的重複。
    """
    rows = [_row("青江菜", date(2026, 8, 1)), _row("青江菜", date(2026, 8, 1))]

    to_add, _ = import_einvoice.plan_import(rows, existing=[])

    assert len(to_add) == 2


def test_string_date_from_notion_is_comparable():
    """Notion 讀回來的日期是字串,不能因為型別不同就漏擋。"""
    rows = [_row("青江菜", date(2026, 8, 1))]
    existing = [{"name": "青江菜", "bought": "2026-08-01"}]

    to_add, skipped = import_einvoice.plan_import(rows, existing)

    assert to_add == [], "字串與 date 應視為同一天"
    assert len(skipped) == 1


# ── 摘要 ───────────────────────────────────────────────────

def test_summary_reports_counts():
    rows = [_row("青江菜", date(2026, 8, 1)), _row("鮭魚", date(2026, 8, 2))]

    text = import_einvoice.format_summary(rows, skipped=[_row("蘋果", date(2026, 8, 3))])

    assert "2" in text and "1" in text


def test_summary_groups_by_category():
    """dry-run 要看得出分類分布,才知道過濾有沒有出錯。"""
    rows = [_row("青江菜", date(2026, 8, 1)), _row("小白菜", date(2026, 8, 1))]

    text = import_einvoice.format_summary(rows, skipped=[])

    assert "蔬菜" in text


def test_summary_survives_missing_category():
    """分類猜不出來是常態(實測 33/140),摘要不能因此爆掉。"""
    rows = [{"name": "某加工食品", "qty": 1, "bought": date(2026, 8, 1),
             "category": None}]

    text = import_einvoice.format_summary(rows, skipped=[])

    assert text
