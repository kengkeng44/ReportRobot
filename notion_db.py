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
from datetime import datetime, timezone


_TOKEN = os.environ.get("NOTION_TOKEN", "")
_PARENT_PAGE = os.environ.get("NOTION_PARENT_PAGE_ID", "")

_client = None
_db_id_cache = {}  # name → db_id
_lock = threading.Lock()


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


# ─────────────────────────────────────────────────────────
# DB Schema 定義（每個 DB 對應一個用途）
# ─────────────────────────────────────────────────────────

_SCHEMAS = {
    "Todos": {
        "Name": {"title": {}},                                  # 待辦內容
        "UserId": {"rich_text": {}},                            # LINE userId
        "Done": {"checkbox": {}},                               # 完成狀態
        "LocalId": {"number": {"format": "number"}},            # 對應 in-memory 編號（給 user 看）
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
}


def get_or_create_db(name):
    """在 NOTION_PARENT_PAGE_ID 底下找 / 建一個 DB。回傳 db_id 或 None。
    結果 cache 到 _db_id_cache，避免每次重新 search。"""
    if name in _db_id_cache:
        return _db_id_cache[name]

    client = _get_client()
    if not client:
        return None
    if name not in _SCHEMAS:
        print(f"[notion] 未定義的 DB schema：{name}")
        return None

    parent_norm = _normalize_id(_PARENT_PAGE)

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
                _db_id_cache[name] = r["id"]
                print(f"[notion] 重用既有 DB：{name} → {r['id']}")
                return r["id"]
    except Exception as e:
        print(f"[notion] search DB 失敗：{e}")

    # 2. 建立新 DB
    with _lock:
        if name in _db_id_cache:
            return _db_id_cache[name]
        try:
            db = client.databases.create(
                parent={"type": "page_id", "page_id": _PARENT_PAGE},
                title=[{"type": "text", "text": {"content": name}}],
                properties=_SCHEMAS[name],
            )
            _db_id_cache[name] = db["id"]
            print(f"[notion] 建立 DB：{name} → {db['id']}")
            return db["id"]
        except Exception as e:
            print(f"[notion] 建立 DB 失敗 {name}：{e}")
            return None


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
