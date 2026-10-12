"""來源欄：顯示名稱 ↔ 內部值、寫入時標管道與記錄人、舊資料補標。"""

import pytest

import notion_db
import sources as S


# ── 對照 ─────────────────────────────────────────────────

@pytest.mark.parametrize("internal,channel,shown", [
    ("國泰消費彙整", None, "📧 國泰通知信"),
    ("國泰電子帳單", None, "📧 國泰帳單"),
    ("手動", S.FORM, "📝 記帳表單"),
    ("手動", None, "💬 LINE私訊"),
    (None, None, None),
])
def test_txn_display(internal, channel, shown):
    assert S.txn_display(internal, channel) == shown


@pytest.mark.parametrize("raw,internal", [
    ("📧 國泰通知信", "國泰消費彙整"),   # 去重指紋靠內部值，讀回來一定要換回去
    ("國泰消費彙整", "國泰消費彙整"),     # 還沒補標的舊資料
    ("📝 記帳表單", "手動"),
    ("💬 LINE（舊）", "手動"),
    ("手動", "手動"),
    (None, "手動"),                       # Notion 手打
])
def test_txn_internal_roundtrip(raw, internal):
    assert S.txn_internal(raw) == internal


def test_every_auto_source_roundtrips():
    for internal in ("國泰消費彙整", "國泰電子帳單", "國泰繳款入帳", "富邦轉帳", "PDF對帳單"):
        assert S.txn_internal(S.txn_display(internal)) == internal


@pytest.mark.parametrize("raw,shown", [
    (None, S.NOTION), ("手動", S.LINE_OLD), ("國泰消費彙整", "📧 國泰通知信"),
    ("📝 記帳表單", "📝 記帳表單"),
])
def test_txn_channel(raw, shown):
    assert S.txn_channel(raw) == shown


@pytest.mark.parametrize("raw,shown", [
    ("LINE", S.LINE_GROUP), ("表單", S.FORM), ("自動同步", S.SYNCED),
    (None, S.NOTION), (S.LINE_GROUP, S.LINE_GROUP),
])
def test_couple_display(raw, shown):
    assert S.couple_display(raw) == shown


# ── 補標 ─────────────────────────────────────────────────

def test_backfill(monkeypatch):
    txns = [
        {"page_id": "a", "source_raw": "手動", "recorder": ""},
        {"page_id": "b", "source_raw": "國泰消費彙整", "recorder": ""},
        {"page_id": "c", "source_raw": None, "recorder": "", "created_by": "u-notion"},
        {"page_id": "d", "source_raw": "📝 記帳表單", "recorder": "家豪"},   # 已經是新的，不動
    ]
    couple = [
        {"page_id": "x", "source_raw": "LINE", "recorder_raw": ""},
        {"page_id": "y", "source_raw": S.SYNCED, "recorder_raw": ""},          # 不動
        {"page_id": "z", "source_raw": None, "recorder_raw": "", "created_by": "u-gf"},
    ]
    t_set, c_set = {}, {}
    monkeypatch.setattr(S, "owner_name", lambda: "家豪")
    monkeypatch.setattr(S, "_PACE", 0)
    monkeypatch.setattr(notion_db, "transactions_load", lambda **kw: txns)
    monkeypatch.setattr(notion_db, "couple_load", lambda **kw: couple)
    monkeypatch.setattr(notion_db, "page_creator_name",
                        lambda uid: {"u-notion": "Jen", "u-gf": "小美"}.get(uid, ""))
    monkeypatch.setattr(notion_db, "transaction_set_origin",
                        lambda pid, src, rec: t_set.__setitem__(pid, (src, rec)) or True)
    monkeypatch.setattr(notion_db, "couple_set_origin",
                        lambda pid, src, rec=None: c_set.__setitem__(pid, (src, rec)) or True)

    assert S.backfill() == {"交易明細": 3, "共同帳本": 2}
    assert t_set == {"a": (S.LINE_OLD, "家豪"),
                     "b": ("📧 國泰通知信", S.AUTO_RECORDER),
                     "c": (S.NOTION, "Jen")}
    assert c_set == {"x": (S.LINE_GROUP, None), "z": (S.NOTION, "小美")}


# ── 寫入 ─────────────────────────────────────────────────

class _Pages:
    def __init__(self):
        self.created = []

    def create(self, parent, properties):
        self.created.append(properties)
        return {"id": "pid"}


class _Client:
    def __init__(self):
        self.pages = _Pages()


@pytest.fixture
def client(monkeypatch):
    c = _Client()
    monkeypatch.setattr(notion_db, "_get_client", lambda: c)
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db")
    monkeypatch.setattr(S, "owner_name", lambda: "家豪")
    return c


def _sel(props, key):
    return props[key]["select"]["name"]


def _txt(props, key):
    return props[key]["rich_text"][0]["text"]["content"]


def test_transaction_add_marks_channel_and_recorder(client):
    notion_db.transaction_add({"date": "2026-10-12", "amount": 120, "shop": "午餐",
                               "source": "手動", "channel": S.FORM, "fingerprint": "f"})
    p = client.pages.created[-1]
    assert _sel(p, "來源") == "📝 記帳表單" and _txt(p, "記錄人") == "家豪"


def test_transaction_add_auto_is_robot(client):
    notion_db.transaction_add({"date": "2026-10-12", "amount": 300, "shop": "全聯",
                               "source": "國泰消費彙整", "fingerprint": "f"})
    p = client.pages.created[-1]
    assert _sel(p, "來源") == "📧 國泰通知信" and _txt(p, "記錄人") == S.AUTO_RECORDER


def test_couple_add_uses_new_names(client):
    notion_db.couple_add({"date": "2026-10-12", "item": "午餐", "total": 120,
                          "kind": "共同", "source": "表單"})
    assert _sel(client.pages.created[-1], "來源") == "📝 記帳表單"
