"""煮飯模板的純邏輯層。

只做計算，不碰 Notion 也不碰 LINE —— 這樣才好測，也才能被排程和
指令路由共用。Notion 讀寫在 notion_db.py，指令綁定在 command_router.py。

設計原則：看不懂就不猜。解析不出來的詞回報給使用者補，
不要塞一筆錯的數量或分類進庫存 —— 錯的庫存比沒有庫存更糟。
"""

import re
from datetime import timedelta


# ─────────────────────────────────────────────────────────
# 採購輸入解析
# ─────────────────────────────────────────────────────────

_PREFIXES = ("買了", "採買", "買", "加入")

# 名稱吃中文或英文，數量與單位都可省略（「買了 醬油」是很自然的講法）
_ITEM_RE = re.compile(
    r"(?P<name>[一-鿿]+|[A-Za-z][A-Za-z ]*?)"
    r"\s*(?P<qty>\d+(?:\.\d+)?)?"
    r"\s*(?P<unit>公斤|公克|克|顆|片|包|盒|瓶|條|把|尾|罐|串|斤|kg|g)?"
)


def _strip_prefix(text):
    out = (text or "").strip()
    for p in _PREFIXES:
        if out.startswith(p):
            return out[len(p):].strip()
    return out


def _tidy_qty(raw):
    if raw is None:
        return 1
    val = float(raw)
    return int(val) if val == int(val) else val


def parse_purchase(text):
    """「買了 高麗菜1顆 番茄5顆」→ ([{name, qty, unit}, ...], [看不懂的詞])。

    回傳兩個值而非丟例外：部分看得懂的還是要寫進庫存，
    看不懂的另外回報讓使用者補，不要整句吞掉。
    """
    cleaned = _strip_prefix(text)
    cleaned = re.sub(r"[、,，;；]+", " ", cleaned)
    if not cleaned:
        return [], []

    items = []
    spans = []
    for m in _ITEM_RE.finditer(cleaned):
        name = (m.group("name") or "").strip()
        if not name:
            continue
        items.append({
            "name": name,
            "qty": _tidy_qty(m.group("qty")),
            "unit": m.group("unit") or "",
        })
        spans.append((m.start(), m.end()))

    # 沒被任何 item 吃掉的片段就是看不懂的
    unknown = []
    pos = 0
    for start, end in spans:
        gap = cleaned[pos:start].strip()
        if gap:
            unknown.extend(gap.split())
        pos = end
    tail = cleaned[pos:].strip()
    if tail:
        unknown.extend(tail.split())

    return items, unknown


# ─────────────────────────────────────────────────────────
# 分類推測
# ─────────────────────────────────────────────────────────

# 順序即優先序。「鮪魚罐頭」要歸罐頭而不是海鮮，「雞蛋」要歸蛋奶而不是肉類，
# 所以特例類別必須排在通則前面。
_CATEGORY_RULES = (
    ("罐頭乾貨", ("罐頭", "乾貨", "泡麵", "麥片", "堅果", "乾")),
    ("蛋奶", ("蛋", "奶", "豆腐", "豆漿", "優格", "起司", "乳", "乾酪")),
    ("海鮮", ("魚", "蝦", "蟹", "貝", "蛤", "花枝", "透抽", "干貝", "蚵", "鮭", "鮪")),
    ("肉類", ("肉", "排", "雞", "豬", "牛", "羊", "絞", "培根", "香腸", "火腿")),
    ("主食", ("米", "麵", "飯", "吐司", "麵包", "麥", "冬粉", "水餃")),
    ("調味料", ("醬", "鹽", "糖", "醋", "胡椒", "味", "油", "香料", "咖哩")),
    ("蔬菜", ("菜", "瓜", "椒", "蔥", "蒜", "薑", "菇", "蘿蔔", "筍", "芹",
              "茄", "苗", "芽", "花椰", "玉米", "豆", "藕", "薯")),
)


def guess_category(name):
    """從品名猜分類。猜不出來回 None —— 硬歸一類會連帶算錯保存期限。"""
    if not name:
        return None
    for category, keywords in _CATEGORY_RULES:
        if any(k in name for k in keywords):
            return category
    return None


# ─────────────────────────────────────────────────────────
# 保存期限
# ─────────────────────────────────────────────────────────

_DEFAULT_STORAGE = {
    "蔬菜": "冷藏",
    "肉類": "冷藏",
    "海鮮": "冷藏",
    "蛋奶": "冷藏",
    "主食": "常溫",
    "調味料": "調味櫃",
    "罐頭乾貨": "常溫",
}

# 保守估計。使用者可以在 Notion 手動改到期日，這裡只求不要高估。
_SHELF_LIFE_DAYS = {
    "蔬菜": {"冷藏": 3, "冷凍": 30, "常溫": 2},
    "肉類": {"冷藏": 2, "冷凍": 90},
    "海鮮": {"冷藏": 1, "冷凍": 60},
    "蛋奶": {"冷藏": 7, "冷凍": 30},
    "主食": {"常溫": 180, "冷藏": 180, "冷凍": 180},
    "調味料": {"調味櫃": 365, "常溫": 365, "冷藏": 365},
    "罐頭乾貨": {"常溫": 365, "冷藏": 365},
}


def default_storage(category):
    """該分類預設放哪。未知分類回冷藏（最保守）。"""
    return _DEFAULT_STORAGE.get(category, "冷藏")


def estimate_expiry(bought_date, category, storage=None):
    """推算到期日。分類未知就回 None —— 不要編一個到期日出來誤導人。"""
    if not category or not bought_date:
        return None
    table = _SHELF_LIFE_DAYS.get(category)
    if not table:
        return None
    days = table.get(storage)
    if days is None:
        days = table.get(default_storage(category))
    if days is None:
        return None
    return bought_date + timedelta(days=days)


# ─────────────────────────────────────────────────────────
# 克數換算與營養粗估
#
# 刻意不接任何外部營養 API：查得到的值本來就因品種、部位、產地而異，
# 線上查詢換來的精度對「今天該煮什麼」沒有幫助，卻多一個會壞的相依。
# 這裡只求數量級正確；要精準值時使用者自己查了手動改 Notion。
# ─────────────────────────────────────────────────────────

_WEIGHT_UNITS = {
    "克": 1, "公克": 1, "g": 1,
    "公斤": 1000, "kg": 1000,
    "斤": 600, "台斤": 600,
}

# 個別品項的典型單重（克）。只列常買的，其餘走分類預設。
_TYPICAL_GRAMS = {
    "高麗菜": {"顆": 1000},
    "白菜": {"顆": 800},
    "番茄": {"顆": 120},
    "洋蔥": {"顆": 200},
    "馬鈴薯": {"顆": 150},
    "紅蘿蔔": {"條": 150},
    "小黃瓜": {"條": 100},
    "雞蛋": {"顆": 50, "盒": 500},
    "雞胸肉": {"片": 150},
    "雞腿": {"隻": 200, "片": 200},
    "豬排": {"片": 120},
    "鮭魚": {"片": 150},
    "板豆腐": {"盒": 300, "塊": 300},
    "吐司": {"片": 30, "包": 400},
}

# 分類層級的兜底單重（克）
_CATEGORY_TYPICAL_GRAMS = {
    "蔬菜": {"顆": 200, "把": 150, "包": 250, "條": 120, "盒": 200},
    "肉類": {"片": 150, "盒": 300, "包": 300, "隻": 200},
    "海鮮": {"片": 150, "尾": 200, "盒": 300, "包": 300},
    "蛋奶": {"顆": 50, "盒": 300, "瓶": 900, "包": 250},
    "主食": {"包": 500, "盒": 300, "片": 30},
    "調味料": {"瓶": 500, "包": 200, "罐": 400},
    "罐頭乾貨": {"罐": 150, "包": 200, "盒": 200},
}

# 每 100g 的粗估值（kcal / 蛋白質 / 碳水 / 脂肪，單位克）
# 每 100g 的 (熱量 kcal, 蛋白質 g, 碳水 g, 脂肪 g)。
#
# ⚠️ **順序有意義**。_match_key 是「先找到先算」的包含比對,所以較長、
# 較具體的 key 一定要排在較短的前面。經典災情:「胡蘿蔔」若排在「蘿蔔」
# 後面,活力胡蘿蔔會被當成白蘿蔔,熱量差一倍(41 vs 21)。
# 新增項目時請照著現有分組插,不要圖方便加在最後面。
#
# 收錄原則:只補**使用者真的買過**的東西(2026-08 載具匯出實測)。
# 字典大不等於命中率高,塞一堆不會買的品項只會拖慢比對又增加誤中機會。
#
# 數值為粗估量級而非實驗室數據,寫入時一律標「營養為粗估」。
# 要精確請以衛福部食品營養成分資料庫校正。
_NUTRITION_PER_100G = {
    # ── 葉菜 ──
    "高麗菜": (25, 1.3, 5.8, 0.1),
    "青江菜": (13, 1.5, 2.2, 0.2),
    "娃娃菜": (13, 1.5, 2.2, 0.2),
    "白菜": (13, 1.2, 2.2, 0.1),
    "菠菜": (23, 2.9, 3.6, 0.4),
    "空心菜": (20, 2.0, 3.1, 0.3),
    # ── 根莖瓜果 ──
    # 「紅蘿蔔」「胡蘿蔔」務必在「蘿蔔」之前,否則會被白蘿蔔蓋掉
    "紅蘿蔔": (41, 0.9, 9.6, 0.2),
    "胡蘿蔔": (41, 0.9, 9.6, 0.2),
    "蘿蔔": (21, 0.7, 4.5, 0.1),      # 白蘿蔔
    "馬鈴薯": (77, 2.0, 17.5, 0.1),
    "地瓜": (114, 1.3, 27.0, 0.2),
    "洋蔥": (40, 1.1, 9.3, 0.1),
    "小黃瓜": (15, 0.7, 3.6, 0.1),
    "絲瓜": (17, 1.0, 3.4, 0.1),
    "秋葵": (33, 2.0, 7.0, 0.2),
    "番茄": (18, 0.9, 3.9, 0.2),
    "蕃茄": (18, 0.9, 3.9, 0.2),      # 異體字,實測品名寫「牛心蕃茄」
    "豆芽": (30, 3.2, 4.3, 0.1),
    "毛豆": (125, 11.0, 10.0, 5.0),
    # ── 菇蕈藻 ──
    "金針菇": (34, 2.7, 7.4, 0.4),
    "鴻喜菇": (30, 2.7, 5.5, 0.4),
    "杏鮑菇": (41, 2.7, 8.3, 0.2),
    "香菇": (34, 3.0, 6.5, 0.2),
    "紫菜": (250, 28.0, 40.0, 1.0),
    # ── 水果 ──
    "蘋果": (52, 0.3, 14.0, 0.2),
    "香蕉": (89, 1.1, 23.0, 0.3),
    "文旦": (38, 0.8, 9.6, 0.2),
    # ── 肉 ──
    # 「雞胸」要有獨立 key:實測品名是「大成原味嫩雞胸」,沒有「肉」字
    "雞胸肉": (165, 31.0, 0.0, 3.6),
    "雞胸": (165, 31.0, 0.0, 3.6),
    "雞里肌": (110, 24.0, 0.0, 1.2),
    "雞腿": (204, 18.0, 0.0, 14.0),
    "二節翅": (203, 18.0, 0.0, 14.0),
    "雞翅": (203, 18.0, 0.0, 14.0),
    "豬五花": (395, 14.0, 0.0, 37.0),
    "豬絞肉": (263, 17.0, 0.0, 21.0),
    "絞肉": (263, 17.0, 0.0, 21.0),   # 「豬瘦絞肉」中間插了字,接不到上面那個
    "豬排": (242, 21.0, 0.0, 17.0),
    "牛小排": (290, 18.0, 0.0, 24.0),
    "肋眼": (291, 24.0, 0.0, 21.0),
    "牛肉": (250, 26.0, 0.0, 15.0),
    "火腿": (145, 16.0, 1.5, 8.0),
    # ── 海鮮 ──
    "鮭魚": (208, 20.0, 0.0, 13.0),
    "蝦仁": (99, 24.0, 0.2, 0.3),
    "鮪魚罐頭": (116, 26.0, 0.0, 1.0),
    # ── 蛋奶豆 ──
    "洗選蛋": (155, 13.0, 1.1, 11.0),
    "滷蛋": (155, 13.0, 1.5, 11.0),
    "雞蛋": (155, 13.0, 1.1, 11.0),
    "鮮奶": (64, 3.2, 4.8, 3.6),
    "奶油": (717, 0.9, 0.1, 81.0),
    "豆漿": (43, 3.6, 2.0, 1.9),
    "板豆腐": (88, 8.5, 2.5, 5.0),
    # ── 主食 ──
    "白米": (356, 7.0, 78.0, 0.6),
    "義大利麵": (359, 13.0, 71.0, 1.5),
    "關廟麵": (280, 9.0, 57.0, 1.0),
    "木薯粉": (358, 0.2, 88.0, 0.0),
    "吐司": (299, 9.0, 55.0, 4.5),
    # ── 調味 ──
    # key 用「碘鹽」而非「鹽」—— 「鹽」會誤中「鹽蔥燒肉飯糰」,
    # 讓一個飯糰的熱量變成 0
    "碘鹽": (0, 0.0, 0.0, 0.0),
    "醬油": (63, 8.0, 6.0, 0.1),
}


def _match_key(name, table):
    """先試完全相同，再試包含關係（「有機高麗菜」→「高麗菜」）。"""
    if not name:
        return None
    if name in table:
        return name
    for key in table:
        if key in name:
            return key
    return None


def estimate_grams(name, qty, unit):
    """把「2 片雞胸肉」換算成克。估不出來回 None。

    估不出來時寧可留空 —— 編一個克數出來會讓整筆營養都是錯的。
    """
    if qty is None:
        return None
    unit = (unit or "").strip()

    if unit in _WEIGHT_UNITS:
        return qty * _WEIGHT_UNITS[unit]
    if not unit:
        return None

    key = _match_key(name, _TYPICAL_GRAMS)
    if key and unit in _TYPICAL_GRAMS[key]:
        return qty * _TYPICAL_GRAMS[key][unit]

    category = guess_category(name)
    per_unit = _CATEGORY_TYPICAL_GRAMS.get(category, {}).get(unit)
    if per_unit:
        return qty * per_unit
    return None


def lookup_nutrition(name):
    """回每 100g 的粗估營養。查不到回 None（留空好過填錯）。"""
    key = _match_key(name, _NUTRITION_PER_100G)
    if not key:
        return None
    kcal, protein, carb, fat = _NUTRITION_PER_100G[key]
    return {"kcal": kcal, "protein": protein, "carb": carb, "fat": fat}


def scale_nutrition(per_100g, grams):
    """把每 100g 的值換算成實際克數的總量。"""
    if not per_100g or not grams:
        return None
    ratio = grams / 100.0
    return {k: round(v * ratio, 1) for k, v in per_100g.items()}


def describe_item(name, qty, unit):
    """一個品項從輸入到可寫入 Notion 的完整欄位。

    每一格都可能是 None —— 呼叫端要能接受部分留空，
    使用者在 Notion 補上比我們猜錯好。
    """
    category = guess_category(name)
    grams = estimate_grams(name, qty, unit)
    per_100g = lookup_nutrition(name)
    return {
        "name": name,
        "qty": qty,
        "unit": unit,
        "category": category,
        "storage": default_storage(category) if category else None,
        "grams": grams,
        "per_100g": per_100g,
        "nutrition": scale_nutrition(per_100g, grams),
        "approximate": True,      # 內建對照表推的，不是實測值
    }


# ─────────────────────────────────────────────────────────
# 到期提醒與「今天煮什麼」
# ─────────────────────────────────────────────────────────

def _qty_text(row):
    qty = row.get("qty")
    unit = row.get("unit") or ""
    if qty is None:
        return ""
    qty_s = int(qty) if float(qty) == int(qty) else qty
    return f"{qty_s}{unit}"


def _days_text(days):
    if days is None:
        return "未設到期日"
    if days < 0:
        return f"已過期 {abs(int(days))} 天"
    if days == 0:
        return "今天到期"
    return f"剩 {int(days)} 天"


def format_pantry(rows):
    """庫存清單，最快過期的排前面。"""
    if not rows:
        return "冰箱是空的。用「買了 高麗菜1顆」加進來。"

    # 沒有到期日的排最後，不要混在急件裡
    ordered = sorted(rows, key=lambda r: (r.get("days_left") is None,
                                          r.get("days_left") or 0))
    lines = ["🥬 目前庫存"]
    for r in ordered:
        qty = _qty_text(r)
        qty_part = f"　{qty}" if qty else ""
        lines.append(f"・{r['name']}{qty_part}　{_days_text(r.get('days_left'))}")
    return "\n".join(lines)


def format_expiring(rows, threshold_days=3):
    hits = expiring_soon(rows, threshold_days)
    if not hits:
        return f"{threshold_days} 天內沒有要過期的食材。"
    lines = [f"⚠️ {threshold_days} 天內要處理"]
    for r in hits:
        lines.append(f"・{r['name']}　{_days_text(r.get('days_left'))}")
    return "\n".join(lines)


def format_recommendations(recs):
    if not recs:
        return "沒有快過期的食材，或現有食譜都缺料。"
    lines = ["💡 建議今天煮"]
    for r in recs[:3]:
        mins = r.get("minutes")
        time_part = f"（約 {mins} 分鐘）" if mins else ""
        lines.append(f"・{r['name']}{time_part}　用掉 {r['uses_expiring']} 樣快過期食材")
    return "\n".join(lines)


def format_shopping(rows):
    if not rows:
        return "採購清單是空的。\n用掉食材時會自動加進來，也可以打「要買 醬油」。"
    lines = [f"🛒 待買 {len(rows)} 樣"]
    for r in rows:
        auto = "　(自動)" if r.get("source") == "低庫存自動" else ""
        lines.append(f"・{r['name']}{auto}")
    return "\n".join(lines)


def format_added(added, unknown):
    """回報寫入結果。看不懂的要明講，不能默默吞掉。"""
    lines = []
    if added:
        lines.append(f"✅ 已加入 {len(added)} 樣")
        for it in added:
            qty = _qty_text(it)
            grams = f"　約 {int(it['grams'])}g" if it.get("grams") else ""
            exp = it.get("expiry")
            exp_part = f"　到期 {exp.isoformat()}" if exp else "　到期日未設"
            lines.append(f"・{it['name']}　{qty}{grams}{exp_part}")
    if unknown:
        lines.append(f"\n❓ 這幾樣看不懂，請補數量或改個講法:{'、'.join(unknown)}")
    if not lines:
        return "沒有解析到任何品項。試試「買了 高麗菜1顆 番茄5顆」。"
    return "\n".join(lines)


def expiring_soon(pantry, threshold_days=3):
    """挑出 days_left <= threshold 的食材，最急的排前面（含已過期的負值）。"""
    hits = [p for p in pantry if p.get("days_left") is not None
            and p["days_left"] <= threshold_days]
    return sorted(hits, key=lambda p: p["days_left"])


def expiring_actions(pantry, threshold_days=3, limit=5):
    """快過期食材的「可操作」清單，給推播卡片放按鈕用（純邏輯，不碰 Notion/LINE）。

    回 (items, more)：
    - items: [{"page_id", "name", "days_text"}]，最急的排前面，最多 limit 筆
    - more:  被 limit 截掉、卡片上放不下的筆數

    沒有 page_id 的直接跳過 —— 按鈕定位不到 Notion 那一列，放了也是按了沒事，
    比沒有按鈕更糟。跳過的不計入 more（那是「放不下」，不是「不能放」）。
    """
    hits = [r for r in expiring_soon(pantry, threshold_days) if r.get("page_id")]
    items = [{
        "page_id": r["page_id"],
        "name": r["name"],
        "days_text": _days_text(r.get("days_left")),
    } for r in hits[:limit]]
    return items, max(0, len(hits) - limit)


# 第一天沒有任何歷史時墊檔用的，台灣家庭冰箱的最大公因數。
# 只是墊檔：使用者自己買過的一律排在前面，買幾次之後這串就會被擠光。
_COMMON_ITEMS = ["高麗菜", "雞蛋", "牛奶", "番茄", "洋蔥",
                 "紅蘿蔔", "雞胸肉", "豆腐", "香蕉", "吐司",
                 "馬鈴薯", "青蔥", "白米"]


def frequent_items(rows, limit=10, pad=True):
    """常買清單：買過的次數多的排前面。純邏輯，不碰 Notion。

    rows 是庫存列（在庫 + 用完都要餵進來，只看在庫的話常買但剛好吃完的
    會消失）。同次數保持第一次出現的順序 —— 每次跳出來的按鈕位置都在動
    比排序不準更難用。

    pad=True 時用 _COMMON_ITEMS 補到 limit：沒歷史就給空按鈕列，
    等於這個功能第一天不存在。
    """
    counts = {}
    order = []
    for r in rows or []:
        name = (r.get("name") or "").strip()
        if not name:
            continue
        if name not in counts:
            order.append(name)
        counts[name] = counts.get(name, 0) + 1

    ranked = sorted(order, key=lambda n: (-counts[n], order.index(n)))
    out = ranked[:limit]

    if pad:
        for name in _COMMON_ITEMS:
            if len(out) >= limit:
                break
            if name not in counts:
                out.append(name)
    return out


def recommend(pantry, recipes, threshold_days=3):
    """依「用掉最多快過期食材」推薦食譜，同分則挑烹調時間短的。

    缺料的食譜直接排除 —— 推了也煮不了。
    完全用不到快過期食材的也排除 —— 那就失去提醒的意義了。
    """
    available = {p["name"] for p in pantry}
    urgent = {p["name"] for p in expiring_soon(pantry, threshold_days)}
    if not urgent:
        return []

    out = []
    for r in recipes:
        ingredients = set(r.get("ingredients") or [])
        if not ingredients or not ingredients <= available:
            continue
        uses = len(ingredients & urgent)
        if uses == 0:
            continue
        item = dict(r)
        item["uses_expiring"] = uses
        out.append(item)

    return sorted(out, key=lambda r: (-r["uses_expiring"], r.get("minutes") or 0))
