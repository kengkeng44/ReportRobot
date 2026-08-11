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
# 到期提醒與「今天煮什麼」
# ─────────────────────────────────────────────────────────

def expiring_soon(pantry, threshold_days=3):
    """挑出 days_left <= threshold 的食材，最急的排前面（含已過期的負值）。"""
    hits = [p for p in pantry if p.get("days_left") is not None
            and p["days_left"] <= threshold_days]
    return sorted(hits, key=lambda p: p["days_left"])


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
