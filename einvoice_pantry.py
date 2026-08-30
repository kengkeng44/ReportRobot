"""發票品項 → 食材庫存的橋接層。

定位(2026-08-30 使用者確認):載具資料**不進交易明細**。
記帳歸國泰彙整信管(那條線只有總額,本來就夠用),載具這條線負責回答
「買了什麼菜」以及後續的營養分析。兩條線分開,順帶也就沒有
同一筆消費被記兩次的問題。

    einvoice_csv.parse()  →  這裡過濾與轉換  →  notion_db 寫入食材庫存

營養與分類沿用 `kitchen.py` 既有的引擎,不另造一套 —— 手動加的食材與
發票匯入的食材要用同一套規則,否則同一顆高麗菜會因為來源不同而算出
兩種熱量。

為什麼要過濾
──────────
發票品項不等於食材。實測 2026-08 的 124 個不重複品名裡混著輕便雨衣、
環保水壺、印花張,以及「折讓」「999999」「30元盤」這種根本不是東西的列。
全倒進食材庫存的話,冰箱清單會變垃圾場,到期提醒也跟著失去意義。

過濾採**黑名單**而非白名單:白名單會把沒見過的食材擋在外面,
而漏掉一顆青菜比混進一件雨衣更難發現。
"""

from datetime import date

import kitchen


SOURCE = "載具發票"

# 明確不是食物的關鍵字。全部來自實際出現過的品名,不憑空想像。
_NON_FOOD_KEYWORDS = (
    "雨衣", "水壺", "隨行", "印花", "購物袋", "提袋", "餐具", "吸管",
    "衛生紙", "口罩", "電池", "襪", "毛巾",
)

# 不是品項的列:折讓折扣、店家自用代碼、迴轉壽司的計價盤。
_NON_ITEM_KEYWORDS = ("折讓", "折扣", "任選折", "元盤", "折抵")


def is_food(name):
    """這個品名是不是「買來吃的東西」。

    加工即食品(飯糰、麵包)算食物 —— 使用者確實吃下去了,
    只是查不到營養值。那是 `營養為粗估` 欄位要處理的事,不是過濾條件。
    """
    text = (name or "").strip()
    if not text:
        return False
    if text.isdigit():
        return False      # 「999999」這種店家自用代碼
    if any(k in text for k in _NON_ITEM_KEYWORDS):
        return False
    if any(k in text for k in _NON_FOOD_KEYWORDS):
        return False
    return True


def to_pantry_rows(invoices):
    """發票清單 → 可直接餵給 `notion_db.pantry_add()` 的列。

    輸出的是 `kitchen.describe_item()` 的形狀(英文 key),不是 Notion 的
    中文欄位名 —— pantry_add 自己會做映射。契約對齊消費端,不對齊資料庫。

    單位一律留 None:發票只給數量不給單位,「2」可能是 2 顆也可能是 2 包。
    連帶 grams 也估不出來,所以營養只存每 100g 的值,不算總量 ——
    硬乘出來的總熱量會是假的。重量由使用者在 Notion 補「重量克」。

    同一種食材不同天買的維持兩筆:到期日不一樣,合併會讓先買的那批
    永遠不過期。要不要合併是使用者在 Notion 自己決定的事。
    """
    rows = []

    for inv in invoices or []:
        if "作廢" in (inv.get("status") or ""):
            continue      # 解析層預設已擋,這裡再保險一次

        bought = inv.get("date")
        for item in inv.get("items") or []:
            name = (item.get("name") or "").strip()
            if not is_food(name):
                continue

            # 單位傳 None —— describe_item 會據此把 grams 留空
            row = kitchen.describe_item(name, item.get("qty"), None)
            row["bought"] = bought
            row["source"] = SOURCE
            row["shop"] = (inv.get("seller") or "").strip()
            if isinstance(bought, date) and row.get("category"):
                row["expiry"] = kitchen.estimate_expiry(bought, row["category"])
            rows.append(row)

    return rows
