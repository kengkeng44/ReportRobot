"""
Notion 持久化基礎設施。

設計：
- lazy DB 建立：第一次需要時才在 NOTION_PARENT_PAGE_ID 底下找 / 建 DB
- 三個 DB：Todos / Reminders / LineQuota
- 所有 Notion 失敗都 fallback：呼叫端檢查回傳值是否 None，自行處理 in-memory
- 寫入失敗時 print warn（但不 raise），避免拖垮主流程

設定：
- NOTION_TOKEN：integration internal token（secret_xxx）
- NOTION_PARENT_PAGE_ID：父頁面 ID（去除 - 的 32 字元 hex 或帶 -）

注意：
- 使用前要在 Notion 該頁面 Share → Connect to → 選你的 integration，
  否則 API 會回 object_not_found / forbidden。
"""

import os
import threading

import holdings
from datetime import datetime


_TOKEN = os.environ.get("NOTION_TOKEN", "")
_PARENT_PAGE = os.environ.get("NOTION_PARENT_PAGE_ID", "")

_client = None
_db_id_cache = {}  # name → db_id
_section_page_cache = {}  # 區塊名 → page_id
_lock = threading.Lock()


# 新模板的 DB 各自收進一個區塊子頁，不跟核心 DB 混在根頁。
# Todos / Reminders / LineQuota 不列在這裡 —— 它們已經在線上跑，搬家會找不到既有資料。
_SECTIONS = {
    "財務中心": {
        "icon": "💰",
        "dbs": ("帳戶", "交易明細", "信用卡帳單", "起始庫存", "持倉", "淨值快照"),
    },
    "煮飯模板": {
        "icon": "🍳",
        "dbs": ("食材庫存", "食譜", "本週菜單", "採購清單"),
    },
    "語言學習": {
        "icon": "📚",
        "dbs": ("語句庫", "金句庫"),
    },
}

_DB_SECTION = {db: sec for sec, cfg in _SECTIONS.items() for db in cfg["dbs"]}


def is_configured():
    """Notion 是否可用。沒 token 或 parent page 就回 False。"""
    return bool(_TOKEN and _PARENT_PAGE)


def _get_client():
    global _client
    if not is_configured():
        return None
    if _client is None:
        try:
            from notion_client import Client
            _client = Client(auth=_TOKEN)
        except Exception as e:
            print(f"[notion] client 建立失敗：{e}")
            return None
    return _client


def _normalize_id(page_id):
    return (page_id or "").replace("-", "")


def _read_select(props, name, default=""):
    """讀 select 屬性的名稱。欄位不存在或沒選值都回 default。

    遷移前建立的資料列不會有新欄位，所以這裡必須容忍缺欄位。
    """
    sel = (props.get(name, {}) or {}).get("select")
    return (sel or {}).get("name") or default


# ─────────────────────────────────────────────────────────
# DB Schema 定義（每個 DB 對應一個用途）
# ─────────────────────────────────────────────────────────

def _select(*names_colors):
    """(名稱, 顏色) tuple 串 → Notion select options。"""
    return {"select": {"options": [{"name": n, "color": c} for n, c in names_colors]}}


# 消費類別沿用國泰帳單自帶分類，不自創（見 spec 4.1）。
#
# 後段四個（線上付款 / 教育∕學費 / 一般購物 / 家具家飾裝潢）是國泰後來
# 實際送出、被 Notion 自動長出來的選項。2026-08-25 健檢才發現程式碼只認
# 前九個 —— Notion 遇到未定義的 select 值不會報錯而是擴充 schema，所以
# 這種偏移完全沒有訊號，只會讓按類別分組的報表安靜地漏桶。
# 顏色刻意對齊線上現況，避免 _ensure_properties 之後又製造新的差異。
_SPEND_CATEGORIES = (
    ("餐飲", "orange"), ("超市∕量販", "green"), ("百貨公司", "pink"),
    ("服飾∕鞋∕精品", "purple"), ("家電∕３Ｃ通訊", "blue"), ("旅遊", "yellow"),
    ("電信服務", "gray"), ("醫療", "red"), ("訂閱服務", "brown"),
    ("線上付款", "purple"), ("教育∕學費", "gray"), ("一般購物", "orange"),
    ("家具家飾裝潢", "brown"),
    ("其他", "default"),
)

SPEND_CATEGORIES = tuple(name for name, _ in _SPEND_CATEGORIES)
SPEND_CATEGORY_DEFAULT = "其他"


def normalize_spend_category(raw):
    """把來源給的消費類別收斂到白名單內。認不出來回「其他」並留下訊息。

    擋在寫入端而不是各個 parser 裡：手動記帳與日後新增的資料源都會經過
    transaction_add，一個關卡就全都保護到。代價是國泰若新增類別會先落入
    「其他」—— 這是刻意的，看得見的「其他」變多，好過 schema 無聲膨脹。
    """
    if not raw:
        return SPEND_CATEGORY_DEFAULT
    text = str(raw).strip()
    if text in SPEND_CATEGORIES:
        return text
    # 國泰信件的斜線實測有全形（∕ U+2215）與半形混用，換算後再比一次，
    # 不要讓一個字元差異把「超市/量販」判成未知類別
    alt = text.replace("/", "∕")
    if alt in SPEND_CATEGORIES:
        return alt
    print(f"[notion] 未知消費類別「{text}」→ 歸入「{SPEND_CATEGORY_DEFAULT}」")
    return SPEND_CATEGORY_DEFAULT

_SCHEMAS = {
    "Todos": {
        "Name": {"title": {}},                                  # 待辦內容
        "UserId": {"rich_text": {}},                            # LINE userId
        "Done": {"checkbox": {}},                               # 完成狀態
        "LocalId": {"number": {"format": "number"}},            # 對應 in-memory 編號（給 user 看）
        # 三大分類。既有 DB 會由 _ensure_properties 自動補上這欄。
        "分類": _select(("工作", "blue"), ("生活", "green"), ("我的專案", "purple")),
    },
    "Reminders": {
        "Name": {"title": {}},                                  # 提醒內容
        "UserId": {"rich_text": {}},
        "FireAt": {"date": {}},                                 # 觸發時間（含時區）
        "Status": {                                             # pending / fired / cancelled
            "select": {
                "options": [
                    {"name": "pending", "color": "yellow"},
                    {"name": "fired", "color": "green"},
                    {"name": "cancelled", "color": "gray"},
                ]
            }
        },
        "LocalId": {"number": {"format": "number"}},
    },
    "LineQuota": {
        "Month": {"title": {}},                                 # YYYY-MM
        "Count": {"number": {"format": "number"}},
    },
    # 已經講過的小知識/笑話/新鮮事。存在 Notion 而不是記憶體：
    # Railway redeploy 會重啟 process，in-memory 的歷史一歸零就又開始重複。
    "今日一則": {
        "內容": {"title": {}},
        "類型": _select(("小知識", "blue"), ("笑話", "orange"), ("新鮮事", "green")),
        "主題": {"rich_text": {}},
        "日期": {"date": {}},
        # 論壇來源文章連結。同一篇 PTT 文章不要被挑第二次 ——
        # 內容比對擋不住「同一篇但 AI 換句話整理」的情況。
        "來源": {"url": {}},
    },

    # ── 語言學習(2026-09-01)──────────────────────────
    #
    # Preply 沒有公開 API(spec 第 1 節查證結果),所以老師整理的句子
    # 由使用者直接貼進這張表。程式只負責排程與出題。
    #
    # 「下次出現」為空 = 今天就該出現。使用者貼完不必再填任何欄位,
    # 否則這張表就變成一件家事(phrasebook.pick_due 吃這條規則)。
    "語句庫": {
        "句子": {"title": {}},
        "語言": _select(("英文", "blue"), ("西班牙文", "orange")),
        "中文意思": {"rich_text": {}},
        "情境備註": {"rich_text": {}},          # 老師的補充、用法陷阱
        "來源": _select(("Preply課堂", "green"), ("自己整理", "gray"),
                        ("AI生成", "purple")),
        "加入日期": {"date": {}},
        "出現次數": {"number": {"format": "number"}},
        "上次出現": {"date": {}},
        "下次出現": {"date": {}},
    },

    # 中文金句。刻意跟語句庫分開:出現規則不同(隨機不重複 vs 間隔
    # 重複),合成一張表要多一個「模式」欄位,而那欄的值永遠等於語言 ——
    # 兩張表反而少一個可以填錯的地方(spec 2.2)。
    "金句庫": {
        "金句": {"title": {}},
        "出處": {"rich_text": {}},
        # 從舊的「每日一句」搬過來的主題標籤(改變 / 休息 / 力量 …)。
        # 目前只存不用 —— 之後想「只看某個主題」或配合心情出句才用得上,
        # 但資料丟了就回不來,所以搬的時候一起帶進來。
        "主題": {"multi_select": {"options": []}},
        "加入日期": {"date": {}},
        "上次出現": {"date": {}},               # 有值代表講過了
    },

    # ── 財務中心 ────────────────────────────────────────
    "帳戶": {
        "名稱": {"title": {}},
        "類型": _select(("信用卡", "orange"), ("存款", "blue"), ("證券", "green")),
        "銀行": {"rich_text": {}},
        "幣別": _select(("TWD", "default"), ("USD", "green")),
        "末四碼": {"rich_text": {}},
        "目前餘額": {"number": {"format": "number"}},
        "歸屬Gmail": _select(("renhezheng44", "blue"), ("jenho.cheng", "purple")),
        "餘額更新時間": {"date": {}},
    },
    "交易明細": {
        "摘要": {"title": {}},
        "日期": {"date": {}},
        "金額": {"number": {"format": "number"}},
        "方向": _select(("支出", "red"), ("收入", "green"), ("轉帳", "gray"), ("還款", "blue")),
        "類別": _select(*_SPEND_CATEGORIES),
        "商店": {"rich_text": {}},
        # 國泰的彙整通知連海外消費都已換算台幣，所以目前這欄一律 TWD。
        # 先建起來，之後接券商 / 訂閱扣款信才不必再動一次 schema 與既有資料。
        "幣別": _select(("TWD", "default"), ("USD", "green")),
        # 海外消費（非 TW）的金額是授權當下的台幣估算，結匯後會變 ——
        # 存下來才分得出「這筆還會變」跟「這筆定了」，否則畫面上長得一樣。
        "消費地區": _select(("TW", "default"), ("US", "orange"),
                            ("JP", "pink"), ("EU", "blue")),
        "卡末四碼": {"rich_text": {}},
        # 授權 = 當下刷卡紀錄；已結帳 = 月帳單確認後的最終金額（見 spec 4.3）
        "狀態": _select(("授權中", "yellow"), ("已結帳", "green"), ("待確認", "red")),
        "來源": _select(
            ("國泰消費彙整", "blue"), ("國泰電子帳單", "purple"), ("國泰繳款入帳", "brown"),
            ("富邦轉帳", "orange"), ("PDF對帳單", "gray"), ("手動", "default"),
        ),
        "原信連結": {"url": {}},
        # 共同消費把「金額」存成我實際負擔的那半，整桌多少錢存這裡。
        # 「金額」欄的語意（我實際負擔）維持不變，六處既有報表才不用改。
        "分攤類型": _select(("個人", "default"), ("共同", "blue")),
        "原始總額": {"number": {"format": "number"}},
        "Fingerprint": {"rich_text": {}},                       # 去重鍵，見 spec 3.3
    },
    "信用卡帳單": {
        "期別": {"title": {}},                                   # YYYY-MM
        "結帳日": {"date": {}},
        "繳款截止日": {"date": {}},
        "應繳總額": {"number": {"format": "number"}},
        "最低應繳": {"number": {"format": "number"}},
        "實際繳款": {"number": {"format": "number"}},
        "狀態": _select(("未繳", "red"), ("已繳", "green"), ("自動扣繳", "blue")),
    },
    "持倉": {
        "代號": {"title": {}},
        "名稱": {"rich_text": {}},
        "市場": _select(("TW", "blue"), ("US", "purple")),
        "股數": {"number": {"format": "number"}},
        "平均成本": {"number": {"format": "number"}},
        "現價": {"number": {"format": "number"}},
        "市值": {"number": {"format": "number"}},
        "未實現損益": {"number": {"format": "number"}},
        "報酬率": {"number": {"format": "percent"}},
        "更新時間": {"date": {}},
    },
    # 持倉計算的**輸入**,跟「持倉」那張輸出表刻意分開:那張每天被
    # finance_sync 覆寫,把起始庫存填進去會讓算錯的結果寫回 Notion、
    # 下次當成起點讀回來,錯誤固化成「事實」而且查不出源頭。這張只讀不寫。
    "起始庫存": {
        "代號": {"title": {}},
        "市場": _select(("TW", "blue"), ("US", "purple")),
        "股數": {"number": {"format": "number"}},
        "平均成本": {"number": {"format": "number"}},
        "基準日": {"date": {}},
        "備註": {"rich_text": {}},
    },
    "淨值快照": {
        "日期": {"title": {}},                                   # YYYY-MM-DD
        "現金": {"number": {"format": "number"}},
        "股票市值": {"number": {"format": "number"}},
        "信用卡未繳": {"number": {"format": "number"}},
        "淨值": {"number": {"format": "number"}},
    },

    # ── 煮飯模板 ────────────────────────────────────────
    "食材庫存": {
        "名稱": {"title": {}},
        "數量": {"number": {"format": "number"}},
        "單位": _select(("顆", "default"), ("片", "default"), ("克", "default"),
                        ("包", "default"), ("盒", "default"), ("瓶", "default")),
        "購買日": {"date": {}},
        "到期日": {"date": {}},
        "剩餘天數": {"formula": {"expression": 'dateBetween(prop("到期日"), now(), "days")'}},
        "存放位置": _select(("冷藏", "blue"), ("冷凍", "purple"), ("常溫", "brown"), ("調味櫃", "gray")),
        "分類": _select(("蔬菜", "green"), ("肉類", "red"), ("海鮮", "blue"), ("蛋奶", "yellow"),
                        ("主食", "orange"), ("調味料", "brown"), ("罐頭乾貨", "gray")),
        "來源": _select(("載具發票", "purple"), ("手動", "gray")),
        # 營養一律以「每 100g」存，總量交給 Notion formula 算 ——
        # 這樣使用者改了重量克，總熱量會自己跟著更新。
        "重量克": {"number": {"format": "number"}},
        "熱量": {"number": {"format": "number"}},               # 每 100g
        "蛋白質": {"number": {"format": "number"}},             # 每 100g
        "碳水": {"number": {"format": "number"}},               # 每 100g
        "脂肪": {"number": {"format": "number"}},               # 每 100g
        "總熱量": {"formula": {"expression":
                   'round(prop("熱量") * prop("重量克") / 100)'}},
        # 內建對照表推估的值，不是實測。使用者手動查證後可取消勾選。
        "營養為粗估": {"checkbox": {}},
        "狀態": _select(("在庫", "green"), ("用完", "gray"), ("丟棄", "red")),
    },
    "食譜": {
        "名稱": {"title": {}},
        "步驟": {"rich_text": {}},
        "烹調時間": {"number": {"format": "number"}},            # 分鐘
        "難度": _select(("簡單", "green"), ("普通", "yellow"), ("困難", "red")),
        "份數": {"number": {"format": "number"}},
        "每份熱量": {"number": {"format": "number"}},
        "圖片": {"files": {}},
        "來源": {"url": {}},
        "標籤": {"multi_select": {"options": []}},
    },
    "本週菜單": {
        "日期": {"title": {}},
        "餐別": _select(("早餐", "yellow"), ("午餐", "orange"), ("晚餐", "blue")),
        "已完成": {"checkbox": {}},
    },
    "採購清單": {
        "品名": {"title": {}},
        "數量": {"number": {"format": "number"}},
        "分類": _select(("蔬菜", "green"), ("肉類", "red"), ("海鮮", "blue"), ("蛋奶", "yellow"),
                        ("主食", "orange"), ("調味料", "brown"), ("罐頭乾貨", "gray")),
        "已購買": {"checkbox": {}},
        "來源": _select(("手動", "default"), ("低庫存自動", "blue"), ("食譜缺料", "purple")),
    },
}


# relation 必須等目標 DB 存在才能建，所以獨立成第二階段（見 spec 3.1）。
# 值是「目標 DB 名稱」，會在 _ensure_relations 解析成真實 database_id。
_RELATIONS = {
    # 「交易明細 → 帳戶」曾經定義在這裡，2026-08-25 移除：transaction_add
    # 從來沒寫過這欄，帳戶 DB 本身也是 0 筆，等於一個永遠空的欄位。
    # 卡片辨識已經有「卡末四碼」文字欄可用。
    # 注意 _ensure_properties 只補不刪 —— 線上那欄要手動移除。
    "信用卡帳單": {"卡片": "帳戶"},
    "食譜": {"所需食材": "食材庫存"},
    "本週菜單": {"食譜": "食譜"},
}


def get_or_create_section_page(title):
    """在根頁底下找 / 建一個區塊子頁（財務中心、煮飯模板）。回 page_id 或 None。"""
    if title in _section_page_cache:
        return _section_page_cache[title]

    client = _get_client()
    if not client:
        return None

    parent_norm = _normalize_id(_PARENT_PAGE)

    # 先找既有同名子頁，避免每次部署都長出一個新的
    try:
        res = client.search(
            query=title,
            filter={"value": "page", "property": "object"},
        )
        for r in res.get("results", []):
            parent = r.get("parent", {}) or {}
            if parent.get("type") != "page_id":
                continue
            if _normalize_id(parent.get("page_id", "")) != parent_norm:
                continue
            blocks = ((r.get("properties", {}) or {}).get("title", {}) or {}).get("title", []) or []
            if "".join(b.get("plain_text", "") for b in blocks) == title:
                _section_page_cache[title] = r["id"]
                print(f"[notion] 重用既有區塊頁：{title} → {r['id']}")
                return r["id"]
    except Exception as e:
        print(f"[notion] search 區塊頁失敗 {title}：{e}")

    with _lock:
        if title in _section_page_cache:
            return _section_page_cache[title]
        try:
            page = client.pages.create(
                parent={"type": "page_id", "page_id": _PARENT_PAGE},
                icon={"type": "emoji", "emoji": _SECTIONS.get(title, {}).get("icon", "📁")},
                properties={"title": {"title": [{"text": {"content": title}}]}},
            )
            _section_page_cache[title] = page["id"]
            print(f"[notion] 建立區塊頁：{title} → {page['id']}")
            return page["id"]
        except Exception as e:
            print(f"[notion] 建立區塊頁失敗 {title}：{e}")
            return None


def _parent_for(name):
    """這個 DB 該掛在哪一頁底下。

    區塊 DB 收進自己的子頁；子頁建不出來時退回根頁 ——
    有地方放總比整個功能失效好。
    """
    section = _DB_SECTION.get(name)
    if not section:
        return _PARENT_PAGE
    return get_or_create_section_page(section) or _PARENT_PAGE


def get_or_create_db(name):
    """找 / 建一個 DB。回傳 db_id 或 None。

    核心 DB（Todos / Reminders / LineQuota）放根頁，
    財務與煮飯的 DB 各自收進區塊子頁（見 _SECTIONS）。
    結果 cache 到 _db_id_cache，避免每次重新 search。
    """
    if name in _db_id_cache:
        return _db_id_cache[name]

    client = _get_client()
    if not client:
        return None
    if name not in _SCHEMAS:
        print(f"[notion] 未定義的 DB schema：{name}")
        return None

    parent_page = _parent_for(name)
    parent_norm = _normalize_id(parent_page)
    db_id = None

    # 1. 先 search 同名 DB（已建過就重用）
    try:
        res = client.search(
            query=name,
            filter={"value": "database", "property": "object"},
        )
        for r in res.get("results", []):
            parent = r.get("parent", {}) or {}
            if parent.get("type") != "page_id":
                continue
            if _normalize_id(parent.get("page_id", "")) != parent_norm:
                continue
            title_blocks = r.get("title", []) or []
            db_title = "".join(b.get("plain_text", "") for b in title_blocks)
            if db_title == name:
                db_id = r["id"]
                print(f"[notion] 重用既有 DB：{name} → {db_id}")
                break
    except Exception as e:
        print(f"[notion] search DB 失敗：{e}")

    # 2. 建立新 DB（第一階段：只帶非 relation 欄位）
    if db_id is None:
        with _lock:
            if name in _db_id_cache:
                return _db_id_cache[name]
            try:
                db = client.databases.create(
                    parent={"type": "page_id", "page_id": parent_page},
                    title=[{"type": "text", "text": {"content": name}}],
                    properties=_SCHEMAS[name],
                )
                db_id = db["id"]
                print(f"[notion] 建立 DB：{name} → {db_id}")
            except Exception as e:
                print(f"[notion] 建立 DB 失敗 {name}：{e}")
                return None

    # 先進 cache 再補 schema —— 若兩個 DB 互相 relation，遞迴會在這裡收斂
    _db_id_cache[name] = db_id

    _ensure_properties(db_id, name)
    _ensure_relations(db_id, name)
    return db_id


def _retrieve_props(db_id, name):
    """讀取 DB 目前的 properties。失敗回 None（呼叫端據此放棄，不硬改）。"""
    client = _get_client()
    if not client:
        return None
    try:
        return client.databases.retrieve(database_id=db_id).get("properties", {}) or {}
    except Exception as e:
        print(f"[notion] retrieve DB 失敗 {name}：{e}")
        return None


def _ensure_properties(db_id, name):
    """既有 DB 缺少 schema 欄位時補上。

    只新增缺少的欄位，不動既有欄位定義 —— 使用者可能已經在 Notion 手動
    調整過選項，覆寫會把他的設定洗掉。
    """
    existing = _retrieve_props(db_id, name)
    if existing is None:
        return

    missing = {k: v for k, v in _SCHEMAS.get(name, {}).items() if k not in existing}
    if not missing:
        return
    try:
        _get_client().databases.update(database_id=db_id, properties=missing)
        print(f"[notion] {name} 補上欄位：{list(missing)}")
    except Exception as e:
        print(f"[notion] 補欄位失敗 {name}：{e}")


def _ensure_relations(db_id, name):
    """第二階段：把 _RELATIONS 的目標 DB 名稱解析成真實 id 後補上 relation。"""
    wanted = _RELATIONS.get(name)
    if not wanted:
        return
    existing = _retrieve_props(db_id, name)
    if existing is None:
        return

    props = {}
    for prop_name, target_name in wanted.items():
        if prop_name in existing:
            continue
        target_id = get_or_create_db(target_name)
        if not target_id:
            print(f"[notion] {name}.{prop_name} 的目標 DB「{target_name}」不可用，跳過 relation")
            continue
        props[prop_name] = {
            "relation": {
                "database_id": target_id,
                "type": "single_property",
                "single_property": {},
            }
        }

    if not props:
        return
    try:
        _get_client().databases.update(database_id=db_id, properties=props)
        print(f"[notion] {name} 補上 relation：{list(props)}")
    except Exception as e:
        print(f"[notion] 補 relation 失敗 {name}：{e}")


def ensure_all_dbs():
    """確保 _SCHEMAS 裡每個 DB 都真的存在。回 (成功數, 總數)。

    為什麼需要這支：get_or_create_db 是 lazy 的，只有真的有人要讀寫某個
    DB 時才會建。這在上游有提早 return 時會失效 —— 2026-08-25 健檢發現
    食譜 / 本週菜單 / 採購清單 從上線起就不存在，因為食材庫存是空的，
    daily_report 的 expiring_soon() 一回空就 return，永遠走不到 recipes_load()。
    整條鏈的 log 全綠，沒有任何人會發現。

    呼叫端負責放到背景執行：這裡會打數十次 Notion API（每個 DB 至少
    search + retrieve），而 Notion 限流 3 req/s，同步跑會拖慢啟動。
    """
    if not is_configured():
        print("[notion] 未設定，跳過 ensure_all_dbs")
        return 0, 0
    ok = 0
    for name in _SCHEMAS:
        try:
            if get_or_create_db(name):
                ok += 1
            else:
                print(f"[notion] ensure_all_dbs：{name} 建立失敗")
        except Exception as e:
            # 單一 DB 失敗不該讓其餘的都不建
            print(f"[notion] ensure_all_dbs：{name} 例外 {e}")
    print(f"[notion] ensure_all_dbs 完成：{ok}/{len(_SCHEMAS)}")
    return ok, len(_SCHEMAS)


# ─────────────────────────────────────────────────────────
# LineQuota：當月 push 計數的持久化
# 一行 = 一個月，title=YYYY-MM, count=當月用量
# ─────────────────────────────────────────────────────────

def quota_get_month(month_str):
    """讀取某月計數，回 (page_id, count) 或 (None, 0)。"""
    db_id = get_or_create_db("LineQuota")
    client = _get_client()
    if not db_id or not client:
        return None, 0
    try:
        res = client.databases.query(
            database_id=db_id,
            filter={
                "property": "Month",
                "title": {"equals": month_str},
            },
            page_size=1,
        )
        results = res.get("results", [])
        if not results:
            return None, 0
        page = results[0]
        page_id = page["id"]
        props = page.get("properties", {}) or {}
        count_prop = props.get("Count", {}) or {}
        count = count_prop.get("number") or 0
        return page_id, int(count)
    except Exception as e:
        print(f"[notion] quota_get_month 失敗：{e}")
        return None, 0


def quota_set_month(month_str, count):
    """寫入某月計數。沒有就建，已有就 update。"""
    db_id = get_or_create_db("LineQuota")
    client = _get_client()
    if not db_id or not client:
        return False
    try:
        page_id, _ = quota_get_month(month_str)
        if page_id:
            client.pages.update(
                page_id=page_id,
                properties={"Count": {"number": int(count)}},
            )
        else:
            client.pages.create(
                parent={"database_id": db_id},
                properties={
                    "Month": {"title": [{"text": {"content": month_str}}]},
                    "Count": {"number": int(count)},
                },
            )
        return True
    except Exception as e:
        print(f"[notion] quota_set_month 失敗：{e}")
        return False


# ─────────────────────────────────────────────────────────
# 今日一則：已經講過的小知識 / 笑話 / 新鮮事
# 一行 = 一則，給 humor.py 當「不要再講這些」的依據
# ─────────────────────────────────────────────────────────

def daily_extra_recent(kind, limit=25):
    """撈某類型最近講過的內容，新到舊。Notion 不可用就回空 list。

    刻意回 []（而不是 raise）：沒有歷史只是少了去重的保護，
    主題輪替還在，不該讓整段「今日一則」消失。
    """
    db_id = get_or_create_db("今日一則")
    client = _get_client()
    if not db_id or not client:
        return []
    try:
        res = client.databases.query(
            database_id=db_id,
            filter={"property": "類型", "select": {"equals": kind}},
            sorts=[{"property": "日期", "direction": "descending"}],
            page_size=min(int(limit), 100),
        )
        out = []
        for page in res.get("results", []):
            text = _read_title(page.get("properties", {}) or {}, "內容")
            if text:
                out.append(text)
        return out
    except Exception as e:
        print(f"[notion] daily_extra_recent 失敗：{e}")
        return []


def daily_extra_recent_links(kind, limit=60):
    """撈某類型最近用過的來源連結。給 PTT 挑文時排除已經推播過的文章。"""
    db_id = get_or_create_db("今日一則")
    client = _get_client()
    if not db_id or not client:
        return []
    try:
        res = client.databases.query(
            database_id=db_id,
            filter={"property": "類型", "select": {"equals": kind}},
            sorts=[{"property": "日期", "direction": "descending"}],
            page_size=min(int(limit), 100),
        )
        out = []
        for page in res.get("results", []):
            url = (page.get("properties", {}) or {}).get("來源", {}).get("url")
            if url:
                out.append(url)
        return out
    except Exception as e:
        print(f"[notion] daily_extra_recent_links 失敗：{e}")
        return []


def daily_extra_add(kind, text, topic="", day=None, source=None):
    """記下今天講了什麼。寫失敗只 print，不影響已經生出來的內容。"""
    db_id = get_or_create_db("今日一則")
    client = _get_client()
    if not db_id or not client or not text:
        return False
    day_str = (day or datetime.now().date()).isoformat()
    try:
        client.pages.create(
            parent={"database_id": db_id},
            properties={
                # Notion title 上限 2000 字元，這裡的內容遠短於此，不截斷
                "內容": {"title": [{"text": {"content": text}}]},
                "類型": {"select": {"name": kind}},
                "主題": {"rich_text": [{"text": {"content": topic or ""}}]},
                "日期": {"date": {"start": day_str}},
                # url 欄位不接受空字串，沒有來源就送 None
                "來源": {"url": source or None},
            },
        )
        return True
    except Exception as e:
        print(f"[notion] daily_extra_add 失敗：{e}")
        return False


# ─────────────────────────────────────────────────────────
# 語句庫 / 金句庫(2026-09-01)
# ─────────────────────────────────────────────────────────

def _query_all(db_id, client, limit, **extra):
    """分頁撈到 limit 筆。Notion 單頁上限 100,不分頁會安靜地少拿。

    transactions_load 的註解已經記過這個坑:只查一次就回,limit 傳 200
    也只拿得到 100 筆,而且不會報錯。
    """
    out, cursor = [], None
    while len(out) < limit:
        kwargs = dict(extra)
        kwargs["database_id"] = db_id
        kwargs["page_size"] = min(limit - len(out), 100)
        if cursor:
            kwargs["start_cursor"] = cursor
        res = client.databases.query(**kwargs)
        page = res.get("results", [])
        # 空頁但宣稱還有下一頁 → 迴圈永遠跑不完(len(out) 不會長)。
        # 真實的 Notion 不會這樣回,但這是每天早上跑的排程 ——
        # 卡死比報錯難查太多,值得這一行保險。
        if not page:
            break
        out.extend(page)
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    return out


def phrases_load(language, limit=500):
    """撈某語言的全部句子。到期判斷交給 phrasebook.pick_due。

    刻意不在 Notion 端 filter 到期日:「下次出現為空」要寫成 is_empty
    的 or 分支,那個分支寫錯不會報錯 —— 只會讓使用者剛貼的句子永遠
    不出現。語句庫是幾百筆等級,全撈一次再在 Python 判斷更安全。
    """
    db_id = get_or_create_db("語句庫")
    client = _get_client()
    if not db_id or not client:
        return []
    out = []
    try:
        rows = _query_all(
            db_id, client, limit,
            filter={"property": "語言", "select": {"equals": language}},
        )
        for r in rows:
            props = r.get("properties", {}) or {}
            sentence = _read_title(props, "句子")
            # 在 Notion 按了新增卻還沒貼句子的那一列不是資料。
            # 不濾掉的話它反而會被優先挑中(「下次出現」也是空的,
            # pick_due 把空的排最前),信裡就出現一行「[EN] 」後面沒東西。
            if not sentence.strip():
                continue
            out.append({
                "page_id": r.get("id"),
                "sentence": sentence,
                "meaning": _read_rich_text(props, "中文意思"),
                "note": _read_rich_text(props, "情境備註"),
                # 手貼的句子不會填這欄。None 會讓 advance 的 +1 變 TypeError
                "appeared": _read_number(props, "出現次數") or 0,
                "due": _read_date(props, "下次出現"),
            })
    except Exception as e:
        print(f"[notion] phrases_load 失敗：{e}")
    return out


def phrase_advance(page_id, fields):
    """寫回出現次數 / 上次出現 / 下次出現。fields 來自 phrasebook.advance()。

    失敗只回 False:信這時已經寄出去了,排程沒推進頂多明天同一句再出現
    一次,不值得把整個每日 job 拉進 error listener。
    """
    client = _get_client()
    if not client or not page_id:
        return False
    try:
        client.pages.update(page_id=page_id, properties={
            "出現次數": {"number": fields["appeared"]},
            "上次出現": {"date": {"start": fields["last_seen"].isoformat()}},
            "下次出現": {"date": {"start": fields["due"].isoformat()}},
        })
        return True
    except Exception as e:
        print(f"[notion] phrase_advance 失敗：{e}")
        return False


def phrase_add(sentence, language, meaning="", note="",
               source="AI生成", day=None, due=None):
    """新增一句到語句庫。AI 補位生的句子走這裡。

    due 由呼叫端算好再傳進來,不在這裡 import phrasebook ——
    notion_db 是底層,反過來相依會變成循環。
    """
    db_id = get_or_create_db("語句庫")
    client = _get_client()
    if not db_id or not client or not sentence:
        return False
    day = day or datetime.now().date()
    due = due or day
    try:
        client.pages.create(parent={"database_id": db_id}, properties={
            "句子": {"title": [{"text": {"content": sentence}}]},
            "語言": {"select": {"name": language}},
            "中文意思": {"rich_text": [{"text": {"content": meaning or ""}}]},
            "情境備註": {"rich_text": [{"text": {"content": note or ""}}]},
            "來源": {"select": {"name": source}},
            "加入日期": {"date": {"start": day.isoformat()}},
            # 生出來當天就用掉了,所以是 1 不是 0
            "出現次數": {"number": 1},
            "上次出現": {"date": {"start": day.isoformat()}},
            "下次出現": {"date": {"start": due.isoformat()}},
        })
        return True
    except Exception as e:
        print(f"[notion] phrase_add 失敗：{e}")
        return False


def quotes_load(limit=500):
    """撈全部中文金句。挑選交給 phrasebook.pick_quote。"""
    db_id = get_or_create_db("金句庫")
    client = _get_client()
    if not db_id or not client:
        return []
    out = []
    try:
        for r in _query_all(db_id, client, limit):
            props = r.get("properties", {}) or {}
            sentence = _read_title(props, "金句")
            if not sentence.strip():
                continue          # 同 phrases_load:空白列不是資料
            out.append({
                "page_id": r.get("id"),
                "sentence": sentence,
                "source": _read_rich_text(props, "出處"),
                # 沒講過必須是 None（不是 ""）—— pick_quote 靠它分類
                "last_seen": _read_date(props, "上次出現"),
            })
    except Exception as e:
        print(f"[notion] quotes_load 失敗：{e}")
    return out


def quote_mark_seen(page_id, today):
    """標記這句金句今天講過了。"""
    client = _get_client()
    if not client or not page_id:
        return False
    try:
        client.pages.update(page_id=page_id, properties={
            "上次出現": {"date": {"start": today.isoformat()}},
        })
        return True
    except Exception as e:
        print(f"[notion] quote_mark_seen 失敗：{e}")
        return False


# ─────────────────────────────────────────────────────────
# Todos：每筆待辦 = Notion DB 一個 page
# ─────────────────────────────────────────────────────────

def todos_load_for_user(user_id):
    """載入該 user 所有未完成待辦，回 list of dict
    {page_id, local_id, text, done}。失敗回 []。"""
    db_id = get_or_create_db("Todos")
    client = _get_client()
    if not db_id or not client:
        return []
    try:
        # 篩 user + done=False
        res = client.databases.query(
            database_id=db_id,
            filter={
                "and": [
                    {"property": "UserId", "rich_text": {"equals": user_id}},
                    {"property": "Done", "checkbox": {"equals": False}},
                ]
            },
            sorts=[{"property": "LocalId", "direction": "ascending"}],
            page_size=100,
        )
        out = []
        for r in res.get("results", []):
            props = r.get("properties", {}) or {}
            name_blocks = (props.get("Name", {}) or {}).get("title", []) or []
            text = "".join(b.get("plain_text", "") for b in name_blocks)
            local_id = (props.get("LocalId", {}) or {}).get("number") or 0
            done = (props.get("Done", {}) or {}).get("checkbox", False)
            out.append({
                "page_id": r["id"],
                "local_id": int(local_id),
                "text": text,
                "done": bool(done),
                "category": _read_select(props, "分類"),
            })
        return out
    except Exception as e:
        print(f"[notion] todos_load_for_user 失敗：{e}")
        return []


def todos_load_all_users():
    """載入所有 user 的未完成待辦（給排程提醒掃描用）。
    回 dict {user_id: [todo dict, ...]}。"""
    db_id = get_or_create_db("Todos")
    client = _get_client()
    if not db_id or not client:
        return {}
    try:
        res = client.databases.query(
            database_id=db_id,
            filter={"property": "Done", "checkbox": {"equals": False}},
            page_size=100,
        )
        out = {}
        for r in res.get("results", []):
            props = r.get("properties", {}) or {}
            uid_blocks = (props.get("UserId", {}) or {}).get("rich_text", []) or []
            user_id = "".join(b.get("plain_text", "") for b in uid_blocks)
            if not user_id:
                continue
            name_blocks = (props.get("Name", {}) or {}).get("title", []) or []
            text = "".join(b.get("plain_text", "") for b in name_blocks)
            local_id = (props.get("LocalId", {}) or {}).get("number") or 0
            out.setdefault(user_id, []).append({
                "page_id": r["id"],
                "local_id": int(local_id),
                "text": text,
                "done": False,
                "category": _read_select(props, "分類"),
            })
        return out
    except Exception as e:
        print(f"[notion] todos_load_all_users 失敗：{e}")
        return {}


TODO_CATEGORIES = ("工作", "生活", "我的專案")
TODO_CATEGORY_DEFAULT = "生活"


def normalize_todo_category(raw):
    """把使用者輸入的分類正規化成三大類之一。認不出來就回預設值。

    接受簡寫（工作/生活/專案）與英文，避免使用者每次都要打全名。
    """
    if not raw:
        return TODO_CATEGORY_DEFAULT
    text = str(raw).strip().lower()
    aliases = {
        "工作": "工作", "work": "工作", "job": "工作", "公事": "工作",
        "生活": "生活", "life": "生活", "私事": "生活", "家裡": "生活",
        "我的專案": "我的專案", "專案": "我的專案", "project": "我的專案",
        "proj": "我的專案", "side": "我的專案",
    }
    return aliases.get(text, TODO_CATEGORY_DEFAULT)


def todos_create(user_id, text, local_id, category=None):
    """建立一筆待辦。回 page_id 或 None。

    category 未指定時歸到「生活」—— 寧可分錯也不要留空，
    留空的話 Notion 上的分類檢視會漏掉這筆。
    """
    db_id = get_or_create_db("Todos")
    client = _get_client()
    if not db_id or not client:
        return None
    try:
        page = client.pages.create(
            parent={"database_id": db_id},
            properties={
                "Name": {"title": [{"text": {"content": text}}]},
                "UserId": {"rich_text": [{"text": {"content": user_id}}]},
                "Done": {"checkbox": False},
                "LocalId": {"number": int(local_id)},
                "分類": {"select": {"name": normalize_todo_category(category)}},
            },
        )
        return page["id"]
    except Exception as e:
        print(f"[notion] todos_create 失敗：{e}")
        return None


def todos_delete(page_id):
    """archived=True 等同刪除。"""
    client = _get_client()
    if not client:
        return False
    try:
        client.pages.update(page_id=page_id, archived=True)
        return True
    except Exception as e:
        print(f"[notion] todos_delete 失敗：{e}")
        return False


# ─────────────────────────────────────────────────────────
# Reminders：每筆提醒 = 一個 page，FireAt 是 datetime（含 +08:00 時區）
# Status: pending / fired / cancelled
# ─────────────────────────────────────────────────────────

def reminders_load_pending_all():
    """載入「所有 user 的 pending reminders」（給 startup reschedule 用）。
    回 list of dict {page_id, user_id, local_id, text, fire_at(datetime)}。
    fire_at 一律 timezone-aware (TPE)。"""
    db_id = get_or_create_db("Reminders")
    client = _get_client()
    if not db_id or not client:
        return []
    try:
        res = client.databases.query(
            database_id=db_id,
            filter={"property": "Status", "select": {"equals": "pending"}},
            page_size=100,
        )
        out = []
        for r in res.get("results", []):
            props = r.get("properties", {}) or {}
            uid_blocks = (props.get("UserId", {}) or {}).get("rich_text", []) or []
            user_id = "".join(b.get("plain_text", "") for b in uid_blocks)
            name_blocks = (props.get("Name", {}) or {}).get("title", []) or []
            text = "".join(b.get("plain_text", "") for b in name_blocks)
            local_id = (props.get("LocalId", {}) or {}).get("number") or 0
            fire_obj = (props.get("FireAt", {}) or {}).get("date")
            if not fire_obj or not fire_obj.get("start"):
                continue
            fire_at = datetime.fromisoformat(fire_obj["start"])
            out.append({
                "page_id": r["id"],
                "user_id": user_id,
                "local_id": int(local_id),
                "text": text,
                "fire_at": fire_at,
            })
        return out
    except Exception as e:
        print(f"[notion] reminders_load_pending_all 失敗：{e}")
        return []


def reminders_load_for_user(user_id):
    """載入該 user 的 pending reminders。"""
    return [r for r in reminders_load_pending_all() if r["user_id"] == user_id]


def reminders_create(user_id, text, fire_at, local_id):
    """建一筆 pending reminder。fire_at 必須含時區。回 page_id 或 None。"""
    db_id = get_or_create_db("Reminders")
    client = _get_client()
    if not db_id or not client:
        return None
    try:
        # Notion date 接受 ISO 8601 with timezone offset
        iso = fire_at.isoformat() if fire_at.tzinfo else fire_at.isoformat() + "+08:00"
        page = client.pages.create(
            parent={"database_id": db_id},
            properties={
                "Name": {"title": [{"text": {"content": text}}]},
                "UserId": {"rich_text": [{"text": {"content": user_id}}]},
                "FireAt": {"date": {"start": iso}},
                "Status": {"select": {"name": "pending"}},
                "LocalId": {"number": int(local_id)},
            },
        )
        return page["id"]
    except Exception as e:
        print(f"[notion] reminders_create 失敗：{e}")
        return None


# ─────────────────────────────────────────────────────────
# 食材庫存：一筆食材 = 一個 page
# ─────────────────────────────────────────────────────────

def _prop_number(v):
    return {"number": v} if v is not None else None


def _prop_select(v):
    return {"select": {"name": v}} if v else None


def _prop_date(v):
    return {"date": {"start": v.isoformat()}} if v else None


def _read_number(props, name):
    return (props.get(name, {}) or {}).get("number")


def _read_formula_number(props, name):
    return ((props.get(name, {}) or {}).get("formula") or {}).get("number")


def _read_date(props, name):
    """回 ISO 字串(Notion 就是這樣存的)。沒填回 None。"""
    d = (props.get(name, {}) or {}).get("date") or {}
    return d.get("start") or None


def _read_title(props, name):
    blocks = (props.get(name, {}) or {}).get("title", []) or []
    return "".join(b.get("plain_text", "") for b in blocks)


def pantry_add(item):
    """寫入一筆食材。item 來自 kitchen.describe_item()，另可帶 expiry(date)。

    每個欄位都可能是 None（估不出克數、猜不出分類）—— None 的欄位直接不送，
    Notion 的 select 不接受 name=None，硬送會整筆失敗。
    """
    db_id = get_or_create_db("食材庫存")
    client = _get_client()
    if not db_id or not client:
        return None

    per_100g = item.get("per_100g") or {}
    candidates = {
        "名稱": {"title": [{"text": {"content": item["name"]}}]},
        "數量": _prop_number(item.get("qty")),
        "單位": _prop_select(item.get("unit")),
        "購買日": _prop_date(item.get("bought")),
        "到期日": _prop_date(item.get("expiry")),
        "存放位置": _prop_select(item.get("storage")),
        "分類": _prop_select(item.get("category")),
        "重量克": _prop_number(item.get("grams")),
        "熱量": _prop_number(per_100g.get("kcal")),
        "蛋白質": _prop_number(per_100g.get("protein")),
        "碳水": _prop_number(per_100g.get("carb")),
        "脂肪": _prop_number(per_100g.get("fat")),
        "營養為粗估": {"checkbox": bool(item.get("approximate", True))},
        "狀態": _prop_select("在庫"),
        # 分得出哪些是發票匯入、哪些是手動加的 —— 之後要重跑或清掉
        # 某一批時才有得篩
        "來源": _prop_select(item.get("source")),
    }
    props = {k: v for k, v in candidates.items() if v is not None}

    try:
        page = client.pages.create(parent={"database_id": db_id}, properties=props)
        return page["id"]
    except Exception as e:
        print(f"[notion] pantry_add 失敗 {item.get('name')}：{e}")
        return None


def pantry_load(status="在庫"):
    """載入庫存。回 list of dict {page_id, name, qty, unit, days_left, category}。"""
    db_id = get_or_create_db("食材庫存")
    client = _get_client()
    if not db_id or not client:
        return []
    try:
        res = client.databases.query(
            database_id=db_id,
            filter={"property": "狀態", "select": {"equals": status}},
            page_size=100,
        )
        out = []
        for r in res.get("results", []):
            props = r.get("properties", {}) or {}
            out.append({
                "page_id": r["id"],
                "name": _read_title(props, "名稱"),
                "qty": _read_number(props, "數量"),
                "unit": _read_select(props, "單位"),
                "category": _read_select(props, "分類"),
                "grams": _read_number(props, "重量克"),
                "days_left": _read_formula_number(props, "剩餘天數"),
                # 匯入腳本靠 (名稱, 購買日) 去重 —— 少了這個,重跑會寫出兩份
                "bought": _read_date(props, "購買日"),
                "source": _read_select(props, "來源"),
            })
        return out
    except Exception as e:
        print(f"[notion] pantry_load 失敗：{e}")
        return []


# ─────────────────────────────────────────────────────────
# 交易明細：一筆交易 = 一個 page，靠 Fingerprint 去重
# ─────────────────────────────────────────────────────────

def transactions_existing_fingerprints(limit=400):
    """撈近期已存在的指紋集合。

    一次撈起來在記憶體比對，而不是每筆交易各查一次 Notion ——
    一天幾十筆的話那是幾十次 API 往返，而且 Notion 有 3 req/s 限流。
    """
    db_id = get_or_create_db("交易明細")
    client = _get_client()
    if not db_id or not client:
        return set()

    out = set()
    cursor = None
    try:
        while len(out) < limit:
            kwargs = {"database_id": db_id, "page_size": 100,
                      "sorts": [{"property": "日期", "direction": "descending"}]}
            if cursor:
                kwargs["start_cursor"] = cursor
            res = client.databases.query(**kwargs)
            for r in res.get("results", []):
                props = r.get("properties", {}) or {}
                blocks = (props.get("Fingerprint", {}) or {}).get("rich_text", []) or []
                fp = "".join(b.get("plain_text", "") for b in blocks)
                if fp:
                    out.add(fp)
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
    except Exception as e:
        print(f"[notion] 讀取既有指紋失敗：{e}")
        # 回空集合會導致重複寫入，所以這裡回 None 讓呼叫端決定放棄
        return None
    return out


def transaction_add(txn):
    """寫入一筆交易。txn 是 parser 產出的 dict。"""
    db_id = get_or_create_db("交易明細")
    client = _get_client()
    if not db_id or not client:
        return None

    title = txn.get("shop") or txn.get("category") or "消費"
    candidates = {
        "摘要": {"title": [{"text": {"content": title}}]},
        "日期": {"date": {"start": txn["date"]}},
        "金額": _prop_number(txn.get("amount")),
        # 沒給幣別就當台幣：手動記帳與既有資料都不會帶這欄
        "幣別": _prop_select(txn.get("currency") or "TWD"),
        "消費地區": _prop_select(txn.get("region")),
        "卡末四碼": ({"rich_text": [{"text": {"content": txn["card_last4"]}}]}
                     if txn.get("card_last4") else None),
        "方向": _prop_select(txn.get("direction")),
        # 正規化擋在這裡，所有來源（國泰、手動記帳、日後新 parser）一併受保護。
        # 沒帶類別就維持不寫這欄 —— 硬填「其他」會把「不知道」偽裝成「已分類」。
        "類別": (_prop_select(normalize_spend_category(txn["category"]))
                 if txn.get("category") else None),
        "商店": {"rich_text": [{"text": {"content": txn.get("shop") or ""}}]},
        "狀態": _prop_select(txn.get("status")),
        "來源": _prop_select(txn.get("source")),
        # 沒帶就不寫這兩欄 —— 國泰同步走的是同一個函式，硬填「個人」
        # 會把「這個來源沒有分攤概念」偽裝成「已經判斷過是個人」。
        "分攤類型": _prop_select(txn.get("split_type")),
        "原始總額": _prop_number(txn.get("total")),
        "Fingerprint": {"rich_text": [{"text": {"content": txn["fingerprint"]}}]},
    }
    if txn.get("mail_url"):
        candidates["原信連結"] = {"url": txn["mail_url"]}
    props = {k: v for k, v in candidates.items() if v is not None}

    try:
        page = client.pages.create(parent={"database_id": db_id}, properties=props)
        return page["id"]
    except Exception as e:
        print(f"[notion] transaction_add 失敗 {txn.get('fingerprint')}：{e}")
        return None


def starting_holdings_load():
    """讀「起始庫存」表 —— 持倉計算的輸入。只讀不寫。

    基準日缺的列直接跳過:基準日決定哪些成交要跳過(快照日以前的已經含在
    庫存裡),猜一個日期就是少算或雙重計算,而且錯得無聲無息。
    寧可少一列,也不要生一個看起來正常的錯數字。

    Notion 掛掉回 [] 不丟例外 —— 每日排程不該因為讀不到設定就整個進
    error listener,退回成交累加就好。
    """
    db_id = get_or_create_db("起始庫存")
    client = _get_client()
    if not db_id or not client:
        return []

    try:
        res = client.databases.query(database_id=db_id, page_size=100)
    except Exception as e:
        print(f"[notion] 起始庫存讀取失敗：{e}")
        return []

    out = []
    for row in res.get("results", []):
        props = row.get("properties", {}) or {}
        ticker = _read_title(props, "代號").strip()
        shares = _read_number(props, "股數")
        asof = ((props.get("基準日", {}) or {}).get("date") or {}).get("start")
        if not ticker or not shares or shares <= 0 or not asof:
            continue
        # 市場沒填就用代號推 —— 手打漏一欄很正常,不要整列丟掉。
        # guess_market 認得 AU9901(臺銀金)是台幣計價。
        out.append({
            "ticker": ticker,
            "market": _read_select(props, "市場") or holdings.guess_market(ticker),
            "shares": int(shares),
            "avg_cost": _read_number(props, "平均成本"),
            "asof": asof[:10],
        })
    return out


def transactions_load(limit=200):
    """撈交易明細（新到舊）。回 list of dict，欄位名對齊 finance_report。

    要真的撈到 limit 筆就必須分頁：Notion 單頁上限是 100，超過得用
    next_cursor 續撈。原本只查一次就回，limit 傳 200 也只拿得到 100 筆 ——
    而且不會報錯，本月支出只是靜靜變小，看起來就像那個月比較省。
    """
    db_id = get_or_create_db("交易明細")
    client = _get_client()
    if not db_id or not client:
        return []
    out = []
    cursor = None
    try:
        while len(out) < limit:
            kwargs = {
                "database_id": db_id,
                "sorts": [{"property": "日期", "direction": "descending"}],
                "page_size": min(limit - len(out), 100),
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            res = client.databases.query(**kwargs)
            for r in res.get("results", []):
                props = r.get("properties", {}) or {}
                date_obj = (props.get("日期", {}) or {}).get("date") or {}
                out.append({
                    "date": (date_obj.get("start") or "")[:10],
                    "amount": _read_number(props, "金額"),
                    # 遷移前的資料沒有幣別欄，一律當台幣（那時只有國泰這個來源）
                    "currency": _read_select(props, "幣別") or "TWD",
                    "region": _read_select(props, "消費地區"),
                    "category": _read_select(props, "類別"),
                    "shop": _read_rich_text(props, "商店"),
                    "direction": _read_select(props, "方向") or "支出",
                    "status": _read_select(props, "狀態"),
                    # 沒讀這欄就分不出手動記帳與自動同步 —— transaction_add
                    # 一直有寫進去，讀不回來只會安靜地得到 None。
                    "source": _read_select(props, "來源"),
                    # 遷移前的資料沒有這兩欄。國泰同步的本來就是自己刷的，
                    # 一律當個人；原始總額回退成金額 —— 個人消費兩者相等。
                    # 沒有這兩條 fallback，所有統計都得特判 None。
                    "split_type": _read_select(props, "分攤類型") or "個人",
                    "total": (_read_number(props, "原始總額")
                              if _read_number(props, "原始總額") is not None
                              else _read_number(props, "金額")),
                })
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
            if not cursor:
                break
        return out
    except Exception as e:
        # 已經撈到的先回去，總比整個變空好 —— 半個月的資料還是能算
        print(f"[notion] transactions_load 失敗（已取得 {len(out)} 筆）：{e}")
        return out


def card_statements_load():
    db_id = get_or_create_db("信用卡帳單")
    client = _get_client()
    if not db_id or not client:
        return []
    try:
        res = client.databases.query(database_id=db_id, page_size=20)
        out = []
        for r in res.get("results", []):
            props = r.get("properties", {}) or {}
            due = (props.get("繳款截止日", {}) or {}).get("date") or {}
            out.append({
                "period": _read_title(props, "期別"),
                "due": (due.get("start") or "")[:10],
                "amount": _read_number(props, "應繳總額"),
                "minimum": _read_number(props, "最低應繳"),
                "status": _read_select(props, "狀態"),
            })
        return out
    except Exception as e:
        print(f"[notion] card_statements_load 失敗：{e}")
        return []


def networth_load(limit=30):
    """撈淨值快照，舊到新。

    必須指定排序：Notion 不給 sorts 時的順序未定義，畫成折線圖會是
    一團亂麻，而且看起來只像「淨值波動很大」，不像程式有問題。
    「日期」是 title（YYYY-MM-DD 字串），字典序剛好等於時間序。
    """
    db_id = get_or_create_db("淨值快照")
    client = _get_client()
    if not db_id or not client:
        return []
    try:
        res = client.databases.query(
            database_id=db_id,
            sorts=[{"property": "日期", "direction": "ascending"}],
            page_size=limit,
        )
        out = []
        for r in res.get("results", []):
            props = r.get("properties", {}) or {}
            out.append({
                "date": _read_title(props, "日期"),
                "cash": _read_number(props, "現金"),
                "stock": _read_number(props, "股票市值"),
                "card_due": _read_number(props, "信用卡未繳"),
                "net": _read_number(props, "淨值"),
            })
        return out
    except Exception as e:
        print(f"[notion] networth_load 失敗：{e}")
        return []


def holdings_sync(rows):
    """把持倉寫進 Notion。以「代號」為鍵 upsert。

    用 update 而非砍掉重建：重建會讓 page id 每天變，
    使用者在 Notion 對某檔加的註解與連結就全沒了。
    回 (更新數, 新增數)。
    """
    db_id = get_or_create_db("持倉")
    client = _get_client()
    if not db_id or not client:
        return 0, 0

    existing = {}
    try:
        res = client.databases.query(database_id=db_id, page_size=100)
        for r in res.get("results", []):
            code = _read_title(r.get("properties", {}) or {}, "代號")
            if code:
                existing[code] = r["id"]
    except Exception as e:
        print(f"[notion] 讀取既有持倉失敗：{e}")
        return 0, 0

    now = datetime.now().astimezone().isoformat()
    updated = created = 0

    for row in rows:
        code = row.get("ticker")
        if not code:
            continue
        market_value = None
        if row.get("current") is not None and row.get("shares") is not None:
            market_value = row["shares"] * row["current"]

        candidates = {
            "名稱": {"rich_text": [{"text": {"content": row.get("display") or ""}}]},
            "市場": _prop_select("US" if row.get("is_us") else "TW"),
            "股數": _prop_number(row.get("shares")),
            "平均成本": _prop_number(row.get("avg")),
            "現價": _prop_number(row.get("current")),
            "市值": _prop_number(market_value),
            "未實現損益": _prop_number(row.get("pnl")),
            # Notion 的 percent 格式是「1 = 100%」，所以要除以 100
            "報酬率": _prop_number(row["pnl_pct"] / 100 if row.get("pnl_pct") is not None else None),
            "更新時間": {"date": {"start": now}},
        }
        props = {k: v for k, v in candidates.items() if v is not None}

        try:
            if code in existing:
                client.pages.update(page_id=existing[code], properties=props)
                updated += 1
            else:
                props["代號"] = {"title": [{"text": {"content": code}}]}
                client.pages.create(parent={"database_id": db_id}, properties=props)
                created += 1
        except Exception as e:
            print(f"[notion] 持倉寫入失敗 {code}：{e}")

    return updated, created


def holdings_load(limit=100):
    """讀回持倉。回 list of dict，欄位名對齊 holdings_sync 的輸入。

    原本只有 holdings_sync 能寫、沒有東西讀得回來 —— 每日推播是直接從
    Gmail 重算的，所以一直沒人發現這個缺口。Dashboard 需要它。

    報酬率在 Notion 存成 percent 格式（1 = 100%），這裡乘回 100 還原成
    人看的數字，跟 holdings_sync 寫入時除以 100 對稱。
    """
    db_id = get_or_create_db("持倉")
    client = _get_client()
    if not db_id or not client:
        return []
    try:
        res = client.databases.query(database_id=db_id, page_size=limit)
        out = []
        for r in res.get("results", []):
            props = r.get("properties", {}) or {}
            pct = _read_number(props, "報酬率")
            out.append({
                "ticker": _read_title(props, "代號"),
                "display": _read_rich_text(props, "名稱"),
                "market": _read_select(props, "市場"),
                "shares": _read_number(props, "股數"),
                "avg": _read_number(props, "平均成本"),
                "current": _read_number(props, "現價"),
                "value": _read_number(props, "市值"),
                "pnl": _read_number(props, "未實現損益"),
                "pnl_pct": pct * 100 if pct is not None else None,
            })
        # 市值大的排前面；沒市值的沉到最後而不是被當成 0 混在中間
        out.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0)))
        return out
    except Exception as e:
        print(f"[notion] holdings_load 失敗：{e}")
        return []


def networth_upsert(day, cash=None, stock=None, card_due=None, net=None):
    """一天一筆淨值快照，同一天重跑會覆寫而非新增。"""
    db_id = get_or_create_db("淨值快照")
    client = _get_client()
    if not db_id or not client:
        return None

    candidates = {
        "現金": _prop_number(cash),
        "股票市值": _prop_number(stock),
        "信用卡未繳": _prop_number(card_due),
        "淨值": _prop_number(net),
    }
    props = {k: v for k, v in candidates.items() if v is not None}

    try:
        res = client.databases.query(
            database_id=db_id,
            filter={"property": "日期", "title": {"equals": day}},
            page_size=1,
        )
        hit = (res.get("results") or [None])[0]
        if hit:
            client.pages.update(page_id=hit["id"], properties=props)
            return hit["id"]
        props["日期"] = {"title": [{"text": {"content": day}}]}
        page = client.pages.create(parent={"database_id": db_id}, properties=props)
        return page["id"]
    except Exception as e:
        print(f"[notion] networth_upsert 失敗：{e}")
        return None


def _read_rich_text(props, name):
    blocks = (props.get(name, {}) or {}).get("rich_text", []) or []
    return "".join(b.get("plain_text", "") for b in blocks)


def shopping_load(only_pending=True):
    """採購清單。預設只回還沒買的。"""
    db_id = get_or_create_db("採購清單")
    client = _get_client()
    if not db_id or not client:
        return []
    try:
        kwargs = {"database_id": db_id, "page_size": 100}
        if only_pending:
            kwargs["filter"] = {"property": "已購買", "checkbox": {"equals": False}}
        res = client.databases.query(**kwargs)
        out = []
        for r in res.get("results", []):
            props = r.get("properties", {}) or {}
            out.append({
                "page_id": r["id"],
                "name": _read_title(props, "品名"),
                "qty": _read_number(props, "數量"),
                "category": _read_select(props, "分類"),
                "source": _read_select(props, "來源"),
            })
        return out
    except Exception as e:
        print(f"[notion] shopping_load 失敗：{e}")
        return []


def shopping_add(name, category=None, source="手動", qty=1):
    """加一筆採購項目。已存在同名未購買的就不重複加。"""
    db_id = get_or_create_db("採購清單")
    client = _get_client()
    if not db_id or not client:
        return None

    for row in shopping_load(only_pending=True):
        if row["name"] == name:
            return row["page_id"]      # 已經在清單上，不要長出第二筆

    candidates = {
        "品名": {"title": [{"text": {"content": name}}]},
        "數量": _prop_number(qty),
        "分類": _prop_select(category),
        "已購買": {"checkbox": False},
        "來源": _prop_select(source),
    }
    props = {k: v for k, v in candidates.items() if v is not None}
    try:
        page = client.pages.create(parent={"database_id": db_id}, properties=props)
        return page["id"]
    except Exception as e:
        print(f"[notion] shopping_add 失敗 {name}：{e}")
        return None


def shopping_mark_bought(page_id):
    client = _get_client()
    if not client:
        return False
    try:
        client.pages.update(page_id=page_id,
                            properties={"已購買": {"checkbox": True}})
        return True
    except Exception as e:
        print(f"[notion] shopping_mark_bought 失敗：{e}")
        return False


def _read_relation_ids(props, name):
    return [r.get("id") for r in (props.get(name, {}) or {}).get("relation", []) or []]


def recipes_load(pantry_rows=None):
    """載入食譜。回 list of dict {name, ingredients(名稱), minutes}。

    食材是 relation，只拿得到 page_id。用已載入的庫存在本地對照成名稱，
    避免對每道食譜的每樣食材各打一次 API（N+1）。
    對照不到的 id 直接略過 —— 那代表食材已被刪除。
    """
    db_id = get_or_create_db("食譜")
    client = _get_client()
    if not db_id or not client:
        return []

    id_to_name = {r["page_id"]: r["name"] for r in (pantry_rows or [])}

    try:
        res = client.databases.query(database_id=db_id, page_size=100)
        out = []
        for r in res.get("results", []):
            props = r.get("properties", {}) or {}
            ingredients = [id_to_name[i] for i in _read_relation_ids(props, "所需食材")
                           if i in id_to_name]
            out.append({
                "page_id": r["id"],
                "name": _read_title(props, "名稱"),
                "ingredients": ingredients,
                "minutes": _read_number(props, "烹調時間"),
            })
        return out
    except Exception as e:
        print(f"[notion] recipes_load 失敗：{e}")
        return []


def pantry_set_status(page_id, status):
    """狀態 in {在庫, 用完, 丟棄}。"""
    client = _get_client()
    if not client:
        return False
    try:
        client.pages.update(
            page_id=page_id,
            properties={"狀態": {"select": {"name": status}}},
        )
        return True
    except Exception as e:
        print(f"[notion] pantry_set_status 失敗：{e}")
        return False


def reminders_set_status(page_id, status):
    """status in {pending, fired, cancelled}。"""
    client = _get_client()
    if not client:
        return False
    try:
        client.pages.update(
            page_id=page_id,
            properties={"Status": {"select": {"name": status}}},
        )
        return True
    except Exception as e:
        print(f"[notion] reminders_set_status 失敗：{e}")
        return False
