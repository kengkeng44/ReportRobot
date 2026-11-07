"""平日上午在全家 = 個人；週末、國定假日、下午、別家店、已有分攤類型的不動。"""

import finance_report as fr


def _txn(date, time, shop="全家便利商店－板橋廣榮店", **kw):
    return {"date": date, "time": time, "shop": shop, "amount": 49, "direction": "支出", **kw}


def test_weekday_morning_is_personal():
    assert fr.apply_personal_rule(_txn("2026-10-27", "8:12"))["split_type"] == "個人"   # 週二


def test_afternoon_weekend_holiday_other_shop_untouched():
    assert "split_type" not in fr.apply_personal_rule(_txn("2026-10-27", "12:00"))
    assert "split_type" not in fr.apply_personal_rule(_txn("2026-10-26", "2:23"))       # 凌晨
    assert "split_type" not in fr.apply_personal_rule(_txn("2026-10-25", "08:00"))      # 週日
    assert "split_type" not in fr.apply_personal_rule(_txn("2026-10-09", "08:00"))      # 國慶連假補假
    assert "split_type" not in fr.apply_personal_rule(_txn("2026-10-27", "08:00", shop="統一超商"))
    assert "split_type" not in fr.apply_personal_rule(_txn("2026-10-27", ""))           # 沒時間


def test_personal_shops_any_time():
    for shop in ("ANTHROPIC* CLAUDE SUB", "Preply", "Amazon web services", "１０１美食街",
                 "高鐵智慧型手機ＩＰｈｏｎｅ", "國光汽車客運－板橋客運站"):
        assert fr.apply_personal_rule(_txn("2026-10-25", "", shop=shop))["split_type"] == "個人", shop
    assert "split_type" not in fr.apply_personal_rule(_txn("2026-10-25", "", shop="全聯福利中心"))


def test_existing_split_wins():
    out = fr.apply_personal_rule(_txn("2026-10-27", "08:00", split_type="共同"))
    assert out["split_type"] == "共同"
