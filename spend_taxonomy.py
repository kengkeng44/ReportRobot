"""交易明細的「大類／細項」：依「錢花在什麼上」分，不是依銀行的店家類型分。

為什麼另開兩欄、不改「類別」：「類別」是國泰帶來的店家類型（蝦皮一律算一般購物、
momo 的除濕機算百貨公司），機器人的報表、月摘要、共同帳本都在用它，改了會連鎖出事。
2026-10-24 使用者看了 7 月以來 290 筆的分析後選了這套兩層分類。

判斷順序（先中先贏）：
1. 店家／摘要關鍵字
2. 明細（載具發票品項、手動備註）關鍵字 —— 蝦皮、momo 這種什麼都賣的店只能靠這個
3. 海外消費（消費地區不是 TW）→ 旅遊（數位訂閱除外：Claude、Railway 是美國扣款）
4. 銀行類別對得上的才對（餐飲 → 飲食），對不上的標「❓待分類」，不硬猜
"""

import os
import time
import unicodedata

UNSORTED = "❓待分類"
OTHER = "其他"

# (大類, 細項, 關鍵字們) —— 順序有意義：越具體的放越前面
_RULES = [
    # 數位訂閱放最前面：美國扣款，不能被「海外 → 旅遊」吃掉
    ("📱 數位訂閱", "AI 工具", ["ANTHROPIC", "CLAUDE", "OPENAI", "CHATGPT", "CURSOR", "PERPLEXITY", "MIDJOURNEY"]),
    ("📱 數位訂閱", "雲端主機", ["RAILWAY", "AMAZON WEB SERVICES", "GOOGLE CLOUD", "VERCEL", "GITHUB", "NOTION", "INFISICAL"]),
    ("📱 數位訂閱", "電信費", ["台灣大哥大", "中華電信", "遠傳", "台灣之星", "MYFONE", "電信費"]),
    ("📱 數位訂閱", "影音訂閱", ["NETFLIX", "SPOTIFY", "YOUTUBE", "DISNEY", "KKBOX", "APPLE.COM/BILL"]),

    ("📚 學習", "語言課", ["PREPLY", "ITALKI", "家教", "英文課"]),
    ("📚 學習", "書籍", ["博客來", "金石堂", "書店", "圖書"]),

    ("🛡️ 保險規費", "保險", ["保險", "產險", "人壽"]),
    ("🛡️ 保險規費", "規費稅金", ["規費", "監理", "罰單", "稅款"]),

    ("🍚 飲食", "約會大餐", ["情人節晚餐", "七夕晚餐", "紀念日晚餐", "約會晚餐", "大餐"]),
    ("💑 社交娛樂", "約會活動", ["情人節", "七夕", "紀念日", "約會"]),
    ("💑 社交娛樂", "禮物", ["禮物", "花店", "生日禮"]),
    ("💑 社交娛樂", "展覽電影", ["影城", "電影", "展覽", "大研創意", "KTV"]),

    ("🚇 交通", "高鐵台鐵", ["高鐵", "台鐵", "臺灣鐵路", "鐵路公司"]),
    ("🚇 交通", "計程車 Uber", ["UBER *TRIP", "UBER * PENDING", "優步", "計程車", "大車隊", "55688", "LINE TAXI"]),
    ("🚇 交通", "加油停車", ["加油", "中油", "台塑石油", "停車", "車容坊", "遠通電收"]),
    ("🚇 交通", "捷運公車", ["捷運", "悠遊卡", "一卡通", "客運", "公車"]),
    ("🚇 交通", OTHER, ["交通∕運輸"]),

    ("✈️ 旅遊", "機票住宿", ["AIRASIA", "航空", "AIRLINE", "長榮", "華航", "星宇", "虎航", "AGODA", "BOOKING", "飯店", "旅館", "民宿", "HOTEL"]),
    ("✈️ 旅遊", "門票體驗", ["KLOOK", "KKDAY", "客遊天下", "故宮", "博物館", "樂園", "門票"]),
    ("✈️ 旅遊", "旅行購物", ["SHOPPING CENTER", "免稅店", "DUTY FREE"]),
    ("✈️ 旅遊", "當地交通", ["SUICA", "ECHIGO", "新幹線", "JR EAST", "JR WEST"]),

    ("🏃 運動健康", "登山裝備", ["MONT-BELL", "MONTBELL", "MONBERU", "SNOW PEAK", "雪諾必克", "迪卡儂", "DECATHLON", "登山"]),
    ("🏃 運動健康", "運動場館", ["體育", "健身", "WORLD GYM", "柏文", "專心練", "運動中心", "球場"]),
    ("🏃 運動健康", "醫療藥品", ["醫院", "診所", "藥局", "杏一", "美德耐", "牙醫", "藥妝"]),

    ("👕 服飾美容", "內衣襪子", ["內衣", "內褲", "襪", "三花", "瑪榭"]),
    ("👕 服飾美容", "美髮保養", ["洗頭", "剪髮", "美髮", "髮廊", "美容", "保養", "濃的工作室"]),
    ("👕 服飾美容", "衣服鞋子", ["UNIQLO", "ZARA", "H&M", "海恩斯莫里斯", "NET ", "服飾", "鞋", "衣服",
                               "禾山部長行", "咖旺商行", "開久工作室", "沐沐商行", "曜雅行", "穿搭童樂園", "重新定義服裝"]),

    ("🏠 居家", "寵物", ["寵物", "餵食器", "飛起來行銷", "飼料", "貓砂", "貓用", "犬用", "給貓"]),
    ("🏠 居家", "家電", ["除濕機", "空氣清淨", "小米", "燦坤", "全國電子", "冷氣", "電風扇", "吸塵器", "監視器"]),
    ("🏠 居家", "家具廚具", ["IKEA", "HOLA", "和樂家居", "特力屋", "宜得利", "NITORI", "炒鍋", "湯鍋", "平底鍋", "保鮮盒", "時鐘", "CLOCK", "家具", "勝立展業"]),
    ("🏠 居家", "日用品", ["屈臣氏", "寶雅", "康是美", "大創", "DAISO", "無印", "生活用品", "衛生紙", "電池", "雨衣"]),

    ("📱 數位訂閱", "3C 配件", ["讀卡機", "支架", "充電", "耳機", "傳輸線", "分享器", "路由器", "九易宇軒", "巨諾資訊", "安可飛"]),

    ("🍚 飲食", "飲料咖啡", ["星巴克", "STARBUCKS", "路易莎", "CAMA", "50嵐", "可不可", "清心", "迷客夏", "麻古",
                           "咖啡", "茶飲", "可樂", "汽水", "樂芳源", "樂展源", "沐熠", "沐津", "榕美樹館", "一角企業社", "泓茂"]),
    ("🍚 飲食", "買菜食材", ["全聯", "家樂福", "好市多", "COSTCO", "康達盛通", "統冠", "美廉社", "楓康", "頂好", "大潤發", "愛買"]),
    ("🍚 飲食", "早餐・超商", ["全家", "統一超商", "7-ELEVEN", "SEVEN-ELEVEN", "FAMILYMART", "LAWSON", "萊爾富", "OK超商", "早餐"]),
    ("🍚 飲食", "正餐外食", ["壽司", "麥當勞", "和德昌", "肯德基", "富利餐飲", "富胖達", "FOODPANDA", "UBER EATS", "美食街",
                           "餐廳", "午餐", "晚餐", "早午餐", "小吃", "拉麵", "火鍋", "便當", "漢堡",
                           "捲餅", "水餃", "湯餃", "牛肉麵", "炒飯", "滷肉飯", "咖哩", "五花馬",
                           "孟青商行", "溫米工作室", "吉花", "和侑商行", "波奇寶餐行", "甜肉商行", "貓灶屋", "金鑽牛", "鑫光威登"]),
]

# 銀行類別 → (大類, 細項)：只收一對一不會錯的；其他的寧可標待分類
_BANK_FALLBACK = {
    "餐飲": ("🍚 飲食", "正餐外食"),
    "超市∕量販": ("🍚 飲食", "買菜食材"),
    "服飾∕鞋∕精品": ("👕 服飾美容", "衣服鞋子"),
    "美容∕個人護理": ("👕 服飾美容", "美髮保養"),
    "運動∕健身": ("🏃 運動健康", "運動場館"),
    "醫療": ("🏃 運動健康", "醫療藥品"),
    "家具家飾裝潢": ("🏠 居家", "家具廚具"),
    "家電∕３Ｃ通訊": ("🏠 居家", "家電"),
    "電信服務": ("📱 數位訂閱", "電信費"),
    "訂閱服務": ("📱 數位訂閱", OTHER),
    "教育∕學費": ("📚 學習", OTHER),
    "旅遊": ("✈️ 旅遊", OTHER),
    "交通∕運輸": ("🚇 交通", OTHER),
}

MAIN_CATEGORIES = ["🍚 飲食", "🏠 居家", "🚇 交通", "✈️ 旅遊", "👕 服飾美容", "📱 數位訂閱",
                   "📚 學習", "🏃 運動健康", "💑 社交娛樂", "🛡️ 保險規費", UNSORTED]

_TRANSPORT_HINTS = ["UBER", "TAXI", "BUS", "TRAIN", "RAIL", "METRO", "SUICA", "JR "]


def _norm(text):
    """全形轉半形、轉大寫：國泰給的店名常是「ＧｌｏｂａｌＭａｌｌ」「ＣＯＵＰＡＮＧ」。"""
    return unicodedata.normalize("NFKC", text or "").upper()


def _match(text):
    t = _norm(text)
    if not t:
        return None
    for main, sub, words in _RULES:
        if any(_norm(w) in t for w in words):
            return main, sub
    return None


def classify(shop="", summary="", items="", bank_category="", region=""):
    """回 (大類, 細項)。判斷不了回 (❓待分類, "")。"""
    hit = _match(f"{shop} {summary}") or _match(items)
    # 明細寫了「XX 旅行」的（新潟旅行：mont-bell）：店家規則先中就照店家，沒中才算旅遊
    if hit is None and ("旅行" in (items or "") or "旅遊" in (items or "")):
        hit = ("✈️ 旅遊", OTHER)
    foreign = (region or "").strip().upper() not in ("", "TW")
    # 海外：只有吃的、交通、分不出來的改算旅遊；在日本買的 mont-bell、UNIQLO 還是照原本的類
    if foreign and (hit is None or hit[0] in ("🍚 飲食", "🚇 交通")):
        text = _norm(f"{shop} {summary} {items}")
        if any(h in text for h in _TRANSPORT_HINTS) or (hit and hit[0] == "🚇 交通"):
            return "✈️ 旅遊", "當地交通"
        if hit and hit[0] == "🍚 飲食":
            return "✈️ 旅遊", "旅行餐飲"
        return "✈️ 旅遊", "旅行購物"
    if hit:
        return hit
    if bank_category in _BANK_FALLBACK:
        return _BANK_FALLBACK[bank_category]
    return UNSORTED, ""


def apply(txn):
    """寫入前補上大類／細項（txn 已經帶的就不動 —— 使用者在表單選過的優先）。"""
    if txn.get("direction") not in (None, "", "支出") or txn.get("main_category"):
        return txn
    main, sub = classify(txn.get("shop"), txn.get("summary"), txn.get("items"),
                         txn.get("category"), txn.get("region"))
    return {**txn, "main_category": main, "sub_category": sub}


# ── 舊資料補標 ─────────────────────────────────────────

_PACE = 0.4


def _plain(props, name, kind):
    v = (props.get(name) or {}).get(kind)
    if kind in ("title", "rich_text"):
        return "".join(t.get("plain_text", "") for t in (v or []))
    if kind == "select":
        return (v or {}).get("name", "")
    return v


def backfill(limit=3000):
    """大類空白、或還是 ❓待分類 的支出列重新判斷。

    只補空的與待分類的：你在 Notion 手動改過的大類不會被蓋掉。
    待分類的會一直重試 —— 載具發票的品項常常晚幾天才補進明細，到時就分得出來。
    """
    import notion_db
    client = notion_db._get_client()
    db_id = notion_db.get_or_create_db("交易明細")
    if not client or not db_id:
        return {"checked": 0, "updated": 0}
    rows = notion_db._query_all(db_id, client, limit, filter={"and": [
        {"property": "方向", "select": {"equals": "支出"}},
        {"or": [{"property": "大類", "select": {"is_empty": True}},
                {"property": "大類", "select": {"equals": UNSORTED}}]},
    ]})
    updated = 0
    for r in rows:
        p = r.get("properties") or {}
        main, sub = classify(_plain(p, "商店", "rich_text"), _plain(p, "摘要", "title"),
                             _plain(p, "明細", "rich_text"), _plain(p, "類別", "select"),
                             _plain(p, "消費地區", "select"))
        old = _plain(p, "大類", "select")
        if main == old:
            continue
        props = {"大類": {"select": {"name": main}}}
        props["細項"] = {"select": {"name": sub}} if sub else {"select": None}
        try:
            client.pages.update(page_id=r["id"], properties=props)
            updated += 1
        except Exception as e:
            print(f"[taxonomy] 更新失敗 {r['id']}：{e}")
        time.sleep(_PACE)
    result = {"checked": len(rows), "updated": updated}
    print(f"[taxonomy] 大類補標：{result}")
    return result
