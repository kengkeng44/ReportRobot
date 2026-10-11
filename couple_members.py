"""
記帳群組的成員與管理員。

名單存在 Notion「共同記帳 → 記帳成員」。LINE 一般帳號拿不到「群組裡有誰」
（那支 API 只開給認證帳號），所以名單是自己長出來的：
  - 第一次讀到空表時，把 ADMIN_LINE_USER_ID 和共同帳本裡出現過的人都加進來，
    全部設成管理員 —— 上線那一刻群組裡的兩個人就是這樣變成管理員的
  - 之後有人在群組講話，查不到就補進來，角色是「成員」

權限：大家記帳、代記、改、刪都一樣；管理員只多了「設定誰是管理員」。
ADMIN_LINE_USER_ID（bot 主人）永遠是管理員，名單怎麼改都拿不掉 ——
不然一個手滑就沒有人能再設定了。
"""

import os
import re
import time

ADMIN = "管理員"
MEMBER = "成員"

_CACHE = {"at": 0.0, "rows": None}
_CACHE_TTL = 600

_LIST_KEYWORDS = {"成員", "記帳成員", "管理員", "誰是管理員"}
_ROLE_RE = re.compile(r"^(設|設定|設為|加|取消|移除)\s*管理員\s*(.*)$")


def owner_id():
    return os.environ.get("ADMIN_LINE_USER_ID", "").strip()


def _seed():
    """空表 → 把 bot 主人和共同帳本裡出現過的人都設成管理員。"""
    import couple_ledger
    import line_sender
    import notion_db

    gid = couple_ledger.couple_group_id()
    ids = []
    if owner_id():
        ids.append(owner_id())
    try:
        for r in notion_db.couple_load(limit=300):
            for uid in (r.get("payer_id"), r.get("recorder_id")):
                if uid and uid not in ids:
                    ids.append(uid)
    except Exception as e:
        print(f"[members] 讀共同帳本失敗，只先加主人：{e}")

    rows = []
    for uid in ids:
        name = line_sender.group_member_name(gid, uid) if gid else ""
        if name is None:
            continue          # 確定不在群組裡（例如已經退出）
        pid = notion_db.member_upsert(uid, name or "", ADMIN)
        if pid:
            rows.append({"page_id": pid, "user_id": uid, "name": name or "", "role": ADMIN})
    print(f"[members] 首次建立名單：{len(rows)} 位管理員")
    return rows


def load(refresh=False):
    """成員名單（快取 10 分鐘）。Notion 掛掉時回上一次的，沒有上一次就回空。"""
    import notion_db

    now = time.monotonic()
    if not refresh and _CACHE["rows"] is not None and now - _CACHE["at"] < _CACHE_TTL:
        return _CACHE["rows"]
    try:
        rows = notion_db.members_load()
        if not rows:
            rows = _seed()
    except Exception as e:
        print(f"[members] 讀取失敗：{e}")
        return _CACHE["rows"] or []
    _CACHE["rows"] = rows
    _CACHE["at"] = now
    return rows


def find(user_id):
    return next((m for m in load() if m["user_id"] == user_id), None)


def is_admin(user_id):
    if not user_id:
        return False
    if user_id == owner_id():
        return True
    m = find(user_id)
    return bool(m and m["role"] == ADMIN)


def name_of(user_id):
    """名單上的名字；名單沒有就問 LINE。查不到回 ""。"""
    m = find(user_id)
    if m and m.get("name") and m["name"] != "（未知）":
        return m["name"]
    import couple_ledger
    import line_sender
    return line_sender.group_member_name(couple_ledger.couple_group_id(), user_id) or ""


def touch(user_id, name=None):
    """群組裡有人講話就確認他在名單上。不在就補成「成員」。"""
    import notion_db

    if not user_id or find(user_id):
        return
    if name is None:
        import couple_ledger
        import line_sender
        name = line_sender.group_member_name(couple_ledger.couple_group_id(), user_id)
        if name is None:
            return
    pid = notion_db.member_upsert(user_id, name or "", MEMBER)
    if pid and _CACHE["rows"] is not None:
        _CACHE["rows"] = _CACHE["rows"] + [
            {"page_id": pid, "user_id": user_id, "name": name or "", "role": MEMBER}]


def for_form():
    """給表單「誰付的」用：[{id, name}]，管理員排前面。"""
    rows = sorted(load(), key=lambda m: m["role"] != ADMIN)
    return [{"id": m["user_id"], "name": m["name"] or "（未知）"} for m in rows]


def list_text():
    rows = load()
    if not rows:
        return "記帳成員名單還是空的。"
    lines = ["👥 記帳成員"]
    for m in sorted(rows, key=lambda m: m["role"] != ADMIN):
        badge = "👑 管理員" if m["role"] == ADMIN or m["user_id"] == owner_id() else "成員"
        lines.append(f"・{m['name'] or '（未知）'}　{badge}")
    lines.append("")
    lines.append("管理員可以打「設管理員 @某人」或「取消管理員 @某人」。")
    lines.append("記帳、幫別人記、改、刪，大家權限都一樣。")
    return "\n".join(lines)


def _resolve(target, mentions):
    """「@小美」或「小美」→ user_id。@提及優先（名字可能重複或改過）。"""
    if mentions:
        return mentions[0]
    name = (target or "").strip().lstrip("@").strip()
    if not name:
        return None
    hits = [m for m in load() if m["name"] == name]
    return hits[0]["user_id"] if len(hits) == 1 else None


def handle(text, ctx):
    """「成員」/「設管理員 @某人」/「取消管理員 @某人」。不是這類指令回 None。"""
    import notion_db

    t = (text or "").strip().lstrip("/").strip()
    if t in _LIST_KEYWORDS:
        return list_text()
    m = _ROLE_RE.match(t)
    if not m:
        return None

    actor = (ctx or {}).get("user_id")
    if not is_admin(actor):
        return "只有管理員可以設定管理員。打「成員」看誰是管理員。"
    target = _resolve(m.group(2), (ctx or {}).get("mentions") or [])
    if not target:
        return ("要指定是誰：在群組打「設管理員 @某人」，"
                "或打名單上的名字（打「成員」看名單）。")

    grant = m.group(1) not in ("取消", "移除")
    member = find(target)
    name = (member or {}).get("name") or name_of(target) or "這位"
    if not grant:
        if target == owner_id():
            return f"{name} 是 bot 主人，永遠是管理員，拿不掉。"
        if not member or member["role"] != ADMIN:
            return f"{name} 本來就不是管理員。"
    elif member and member["role"] == ADMIN:
        return f"{name} 已經是管理員了。"

    role = ADMIN if grant else MEMBER
    pid = notion_db.member_upsert(target, name if not member else None, role,
                                  page_id=(member or {}).get("page_id"))
    if not pid:
        return "寫入 Notion 失敗，請稍後再試。"
    load(refresh=True)
    return f"👑 {name} 現在是管理員了。" if grant else f"✅ 已取消 {name} 的管理員。"
