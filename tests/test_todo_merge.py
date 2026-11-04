"""三份待辦合成一份：開機載入共同待辦時，把你和小螢舊表裡的未完成待辦搬進來。"""

import notion_db
import personal

GROUP = "Cgroup"


def _row(pid, name, uid, local_id, by=""):
    props = {
        "Name": {"type": "title", "title": [{"plain_text": name}]},
        "UserId": {"type": "rich_text", "rich_text": [{"plain_text": uid}]},
        "Done": {"type": "checkbox", "checkbox": False},
        "LocalId": {"type": "number", "number": local_id},
        "類型": {"type": "select", "select": {"name": "📌 其他"}},
    }
    if by:
        props["輸入者"] = {"type": "rich_text", "rich_text": [{"plain_text": by}]}
    return {"id": pid, "properties": props}


class FakeClient:
    def __init__(self, tables):
        self.tables = tables          # db_id → list of rows
        self.archived = []
        self.databases = self
        self.pages = self

    def query(self, database_id, filter=None, **kw):
        uid = filter["and"][0]["rich_text"]["equals"]
        rows = [r for r in self.tables[database_id]
                if r["properties"]["UserId"]["rich_text"][0]["plain_text"] == uid
                and r["id"] not in self.archived]
        return {"results": rows}

    def create(self, parent, properties):
        db = parent["database_id"]
        n = len(self.tables[db]) + 1
        props = {}
        for k, v in properties.items():
            t = next(iter(v))
            val = v[t]
            if t in ("title", "rich_text"):
                val = [{"plain_text": b["text"]["content"]} for b in val]
            props[k] = {"type": t, t: val}
        self.tables[db].append({"id": f"{db}-new{n}", "properties": props})
        return {"id": f"{db}-new{n}"}

    def update(self, page_id, archived=False, **kw):
        if archived:
            self.archived.append(page_id)


def test_admin_and_partner_lists_move_into_shared(monkeypatch):
    monkeypatch.setenv("COUPLE_GROUP_ID", GROUP)
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")
    monkeypatch.setenv("PARTNER_LINE_USER_ID", "Upartner")
    names = {"Uadmin": "鄭仁和", "Upartner": "小螢"}
    monkeypatch.setattr(personal, "member_name", lambda uid: names.get(uid, ""))
    ids = {"共同待辦": "dbShared", "Todos": "dbTodos", "小螢待辦": "dbPartner"}
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: ids[name])
    client = FakeClient({
        "dbShared": [_row("s1", "買衛生紙", GROUP, 1, by="C"), _row("s2", "砧板", GROUP, 2)],
        "dbTodos": [_row("t1", "山屋申請", "Uadmin", 1), _row("t2", "交報告", "Uadmin", 2)],
        "dbPartner": [_row("p1", "還書", "Upartner", 1, by="小螢")],
    })
    monkeypatch.setattr(notion_db, "_get_client", lambda: client)

    rows = notion_db.todos_load_for_user(GROUP)

    assert [r["text"] for r in rows] == ["買衛生紙", "砧板", "山屋申請", "交報告", "還書"]
    assert [r["local_id"] for r in rows] == [1, 2, 3, 4, 5]
    assert [r["by"] for r in rows] == ["C", "", "鄭仁和", "鄭仁和", "小螢"]
    assert sorted(client.archived) == ["p1", "t1", "t2"]
    # 第二次載入舊表已空，不會再搬一次
    assert len(notion_db.todos_load_for_user(GROUP)) == 5


def test_no_group_means_no_merge(monkeypatch):
    monkeypatch.delenv("COUPLE_GROUP_ID", raising=False)
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")
    assert personal.canonical_owner("Uadmin") == "Uadmin"
