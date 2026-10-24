"""大類／細項判斷。樣本都是 2026-07～10 交易明細裡真的出現過的店家與明細。"""

import pytest

from spend_taxonomy import UNSORTED, apply, classify


@pytest.mark.parametrize("shop, items, bank, region, want", [
    # 超商、買菜、日用品原本全擠在「超市∕量販」
    ("全家便利商店－板橋廣榮店", "ＡＢ優酪乳無加糖", "超市∕量販", "TW", ("🍚 飲食", "早餐・超商")),
    ("全聯福利中心－板橋板新", "雞里肌肉、小白菜", "超市∕量販", "TW", ("🍚 飲食", "買菜食材")),
    ("屈臣氏", "鎂B6發泡錠", "超市∕量販", "TW", ("🏠 居家", "日用品")),
    ("LAWSON", "", "超市∕量販", "TW", ("🍚 飲食", "早餐・超商")),          # 不能被 AWS 吃掉
    # 蝦皮／momo 靠明細
    ("樂購蝦皮股份有限公司", "推測：空氣清淨機（9/30 晚上說要在蝦皮下單）", "家具家飾裝潢", "TW", ("🏠 居家", "家電")),
    ("樂購蝦皮股份有限公司", "瑪榭 無痕女內褲 ×3", "服飾∕鞋∕精品", "TW", ("👕 服飾美容", "內衣襪子")),
    ("樂購蝦皮股份有限公司", "防狼噴霧、筆電支架、晶片讀卡機", "家電∕３Ｃ通訊", "TW", ("📱 數位訂閱", "3C 配件")),
    ("富邦媒體科技（momo購物網）", "推測：除濕機", "百貨公司", "TW", ("🏠 居家", "家電")),
    ("ＣＯＵＰＡＮＧ", "Tefal 特福 不沾小炒鍋", "一般購物", "TW", ("🏠 居家", "家具廚具")),
    # 交通原本散在「其他」「旅遊」
    ("高鐵板橋站", "", "其他", "TW", ("🚇 交通", "高鐵台鐵")),
    ("國營臺灣鐵路公司網路購票", "", "其他", "TW", ("🚇 交通", "高鐵台鐵")),
    ("車容坊加油站－五股站", "", "其他", "TW", ("🚇 交通", "加油停車")),
    ("優步", "", "其他", "TW", ("🚇 交通", "計程車 Uber")),
    ("葛瑪蘭汽車客運股份有限公司", "", "其他", "TW", ("🚇 交通", "捷運公車")),
    # 訂閱：美國扣款也不能變旅遊
    ("ANTHROPIC* CLAUDE SUB", "", "家電∕３Ｃ通訊", "US", ("📱 數位訂閱", "AI 工具")),
    ("RAILWAY", "", "訂閱服務", "US", ("📱 數位訂閱", "雲端主機")),
    ("Amazon web services", "", "電信服務∕保險", "US", ("📱 數位訂閱", "雲端主機")),
    ("Preply", "", "線上付款", "TW", ("📚 學習", "語言課")),
    # 放錯類的
    ("洗頭髮 雙人", "", "服飾∕鞋∕精品", "TW", ("👕 服飾美容", "美髮保養")),
    ("國泰世紀產物保險（股）", "", "電信服務∕保險", "TW", ("🛡️ 保險規費", "保險")),
    ("情人節晚餐", "七夕情人節晚餐（跟小螢）", "餐飲", "TW", ("🍚 飲食", "約會大餐")),
    ("國立故宮博物院", "", "旅遊", "TW", ("✈️ 旅遊", "門票體驗")),
    ("台灣小米通訊有限公司信義威秀", "小米 C400 監視器、寵物餵食器", "家電∕３Ｃ通訊", "TW", ("🏠 居家", "家電")),
    # 海外
    ("AIRASIA COUNTER-BKI", "", "旅遊", "MY", ("✈️ 旅遊", "機票住宿")),
    ("MOBILE SUICA APPLE", "", "旅遊", "JP", ("✈️ 旅遊", "當地交通")),
    ("UBER *TRIP HELP.UBER.COM", "", "旅遊", "JP", ("✈️ 旅遊", "當地交通")),
    ("JR EAST SHOPPING CENTER", "新潟旅行：JR 車站商場購物", "一般購物", "JP", ("✈️ 旅遊", "旅行購物")),
    ("TONG DEPARTMENTAL", "", "超市∕量販", "MY", ("✈️ 旅遊", "旅行購物")),
    ("FAMILYMART", "", "超市∕量販", "JP", ("✈️ 旅遊", "旅行餐飲")),
    ("MONBERU NIIGATAMINAMITEN/", "新潟旅行：mont-bell 新潟南店（登山用品／衣服）", "服飾∕鞋∕精品", "JP", ("🏃 運動健康", "登山裝備")),
    ("UNIQLO CO.,LTD", "新潟旅行：日本 UNIQLO 衣服", "服飾∕鞋∕精品", "JP", ("👕 服飾美容", "衣服鞋子")),
    # 銀行類別保底
    ("餐飲", "Ｌ百香冬瓜茶", "餐飲", "TW", ("🍚 飲食", "正餐外食")),
    ("五花馬國際行銷股份有限公司", "牛肉捲餅、蔥油餅", "", "TW", ("🍚 飲食", "正餐外食")),
])
def test_real_samples(shop, items, bank, region, want):
    got = classify(shop, "", items, bank, region)
    assert got == want, (shop, got)


def test_hotpot_is_food_not_cookware():
    assert classify("老四川火鍋", "", "", "餐飲", "TW") == ("🍚 飲食", "正餐外食")


def test_unknown_marked_unsorted_not_guessed():
    assert classify("MULTIECUBE", "", "", "一般購物", "TW") == (UNSORTED, "")
    assert classify("被吃掉的錢幣", "", "", "其他", "TW") == (UNSORTED, "")


def test_apply_skips_non_spending_and_respects_given():
    assert "main_category" not in apply({"direction": "轉帳", "shop": "高鐵"})
    assert apply({"direction": "支出", "shop": "高鐵", "main_category": "🏠 居家"})["main_category"] == "🏠 居家"
    t = apply({"direction": "支出", "shop": "高鐵板橋站", "category": "其他", "region": "TW"})
    assert (t["main_category"], t["sub_category"]) == ("🚇 交通", "高鐵台鐵")


def test_schema_and_scheduler_wired():
    from pathlib import Path
    import notion_db
    assert "大類" in notion_db._SCHEMAS["交易明細"] and "細項" in notion_db._SCHEMAS["交易明細"]
    src = (Path(__file__).resolve().parent.parent / "server.py").read_text(encoding="utf-8")
    assert 'id="taxonomy_backfill_daily"' in src
