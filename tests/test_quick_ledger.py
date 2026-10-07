"""快速記帳：一鍵組合、私訊直接打、情侶群組共同帳本、LIFF 表單後端。"""

from datetime import date, timedelta

import pytest

import command_router as cr
import couple_ledger as cl
import finance_report as fr
import liff_api
import line_sender
import notion_db
from flex_builder import quick_reply_text

TODAY = date(2026, 10, 7)
ADMIN = "Uadmin"
GF = "Ugf"
GROUP = "Cgroup"
DM = {"source_type": "user", "user_id": ADMIN}


def _txn(shop, total, split="個人", days_ago=0, source="手動", direction="支出"):
    return {"date": (TODAY - timedelta(days=days_ago)).isoformat(), "shop": shop,
            "total": total, "amount": total, "split_type": split,
            "source": source, "direction": direction}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ADMIN_LINE_USER_ID", ADMIN)
    monkeypatch.setenv("COUPLE_GROUP_ID", GROUP)
    monkeypatch.delenv("LIFF_ID", raising=False)
    monkeypatch.setattr(notion_db, "is_configured", lambda: True)
    names = {ADMIN: "家豪", GF: "小美"}
    monkeypatch.setattr(line_sender, "group_member_name",
                        lambda g, u: names.get(u) if g == GROUP else None)


def _group(user=ADMIN, gid=GROUP):
    return {"source_type": "group", "user_id": user, "group_id": gid}


# ── 一鍵組合 ─────────────────────────────────────────────

def test_combos_need_two_hits_and_rank_by_weight():
    txns = [_txn("咖啡", 55), _txn("咖啡", 55), _txn("咖啡", 55),
            _txn("午餐", 120), _txn("午餐", 120),
            _txn("生日大餐", 1280)]                     # 只記過一次 → 不上按鈕
    assert fr.frequent_combos(txns, today=TODAY) == [
        ("咖啡", 55, "個人"), ("午餐", 120, "個人")]


def test_combos_keep_split_apart_and_skip_income_and_card_sync():
    txns = [_txn("晚餐", 600, "共同"), _txn("晚餐", 600, "共同"),
            _txn("晚餐", 600), _txn("晚餐", 600),
            _txn("薪水", 50000, direction="收入"), _txn("薪水", 50000, direction="收入"),
            _txn("全聯", 300, source="國泰消費彙整"), _txn("全聯", 300, source="國泰消費彙整")]
    combos = fr.frequent_combos(txns, today=TODAY)
    assert ("晚餐", 600, "共同") in combos and ("晚餐", 600, "個人") in combos
    assert all(c[0] not in ("薪水", "全聯") for c in combos)


def test_combos_ignore_older_than_90_days():
    txns = [_txn("咖啡", 55, days_ago=120), _txn("咖啡", 55, days_ago=100)]
    assert fr.frequent_combos(txns, today=TODAY) == []


def test_build_manual_txn_keeps_digits_in_item():
    """表單送來的「7-11」不能被拼回句子再 parse —— 7 會被當成金額。"""
    txn = fr.build_manual_txn("7-11", 85, "個人", today=TODAY)
    assert txn["shop"] == "7-11" and txn["amount"] == 85


# ── 私訊直接打「午餐 120」────────────────────────────────

@pytest.mark.parametrize("text", ["午餐 120", "午餐120", "晚餐 600 共同",
                                  "咖啡 55 單人", "早餐 60元"])
def test_bare_entry_parses(text):
    assert cr.parse(text)[0] == "quick_entry"


@pytest.mark.parametrize("text,kind", [
    ("2330", "stock"),                         # 純數字是查股票
    ("提醒 30 分鐘後 喝水", "reminder_add"),    # 既有指令照舊
    ("記一筆 午餐 120", "fin_manual"),
])
def test_existing_commands_win(text, kind):
    assert cr.parse(text)[0] == kind


@pytest.mark.parametrize("text", ["今天天氣不錯走了快一萬步數 8000", "0050 100", "hello 100",
                                  "📝 午餐 NT$120・共同（表單）"])
def test_not_an_entry(text):
    p = cr.parse(text)
    assert p is None or p[0] != "quick_entry"


def test_quick_entry_arg_defaults_to_personal():
    assert cr._quick_entry_arg("午餐 120") == "午餐 120 個人"
    assert cr._quick_entry_arg("咖啡 55 單人") == "咖啡 55 個人"
    assert cr._quick_entry_arg("晚餐 600 共同") == "晚餐 600 共同"


def test_bare_entry_in_dm_records_in_one_step(env, monkeypatch):
    written = []
    monkeypatch.setattr(notion_db, "transaction_add", lambda t: written.append(t) or "pid")
    reply = cr.handle("午餐 120", ctx=DM)
    assert "已記錄" in reply
    assert written[0]["shop"] == "午餐" and written[0]["split_type"] == "個人"


def test_bare_entry_in_family_group_is_silent(env, monkeypatch):
    monkeypatch.setattr(notion_db, "transaction_add",
                        lambda t: pytest.fail("家人群組不能記帳"))
    monkeypatch.setattr(notion_db, "couple_add",
                        lambda r: pytest.fail("家人群組不能記帳"))
    assert cr.handle("便當 80", ctx=_group(gid="Cfamily")) is None


# ── 記一筆按鈕：組合在前 + 快取 ──────────────────────────

def test_menu_puts_combos_first_and_caches(env, monkeypatch):
    calls = []
    txns = [_txn("咖啡", 55), _txn("咖啡", 55)]
    monkeypatch.setattr(notion_db, "transactions_load",
                        lambda **kw: calls.append(1) or txns)
    monkeypatch.setattr(fr, "date", type("D", (), {"today": staticmethod(lambda: TODAY),
                                                   "fromisoformat": date.fromisoformat}))
    msg = cr.handle("記一筆", ctx=DM)
    first = msg["quickReply"]["items"][0]["action"]
    assert first["text"] == "記一筆 咖啡 55 個人"
    cr.handle("記一筆 咖啡", ctx=DM)
    assert len(calls) == 1                       # 第二段沒有再撈 Notion


def test_written_txn_enters_cache(env, monkeypatch):
    monkeypatch.setattr(notion_db, "transactions_load", lambda **kw: [])
    monkeypatch.setattr(notion_db, "transaction_add", lambda t: "pid")
    cr._cached_txns()
    cr.handle("午餐 120", ctx=DM)
    assert cr._TXN_CACHE["rows"][0]["shop"] == "午餐"


def test_liff_button_only_when_configured(env, monkeypatch):
    monkeypatch.setattr(notion_db, "transactions_load", lambda **kw: [])
    labels = [i["action"]["label"] for i in cr.handle("記一筆", ctx=DM)["quickReply"]["items"]]
    assert "📝 表單" not in labels
    monkeypatch.setenv("LIFF_ID", "123-abc")
    first = cr.handle("記一筆", ctx=DM)["quickReply"]["items"][0]["action"]
    assert first == {"type": "uri", "label": "📝 表單", "uri": "https://liff.line.me/123-abc"}


def test_quick_reply_text_message_actions_unchanged():
    msg = quick_reply_text("hi", [("午餐", "記一筆 午餐")])
    assert msg["quickReply"]["items"][0]["action"] == {
        "type": "message", "label": "午餐", "text": "記一筆 午餐"}


# ── 共同帳本 ─────────────────────────────────────────────

@pytest.mark.parametrize("text,item,total,kind", [
    ("午餐 120", "午餐", 120, "共同"),
    ("衣服 990 個人", "衣服", 990, "個人"),
    ("咖啡 55 單人", "咖啡", 55, "個人"),
    ("記一筆 晚餐 600", "晚餐", 600, "共同"),
    ("電影票 640 共同", "電影票", 640, "共同"),
])
def test_couple_parse_defaults_to_shared(text, item, total, kind):
    assert cl.parse_entry(text) == {"item": item, "total": total, "kind": kind}


@pytest.mark.parametrize("text", ["2330", "午餐", "結算", "abc 100", "午餐 0"])
def test_couple_parse_rejects(text):
    assert cl.parse_entry(text) is None


def test_group_entry_goes_to_couple_ledger_with_payer(env, monkeypatch):
    rows = []
    monkeypatch.setattr(notion_db, "couple_add", lambda r: rows.append(r) or "pid")
    monkeypatch.setattr(notion_db, "transaction_add",
                        lambda t: pytest.fail("共同帳不該寫進我的交易明細"))
    reply = cr.handle("午餐 120", ctx=_group(user=GF))
    assert rows[0]["payer"] == "小美" and rows[0]["payer_id"] == GF
    assert rows[0]["kind"] == "共同" and rows[0]["total"] == 120
    assert "共同・小美付" in reply["text"]
    assert reply["quickReply"]["items"][0]["action"]["text"] == "撤銷"


def test_group_still_answers_other_commands(env, monkeypatch):
    """記帳群組裡查股票、看說明照舊 —— 共同帳本只接記帳相關的。"""
    assert cl.handle("2330", _group()) is None
    assert cr.handle("help", ctx=_group()) == cr.HELP_TEXT


def test_settle_two_payers():
    rows = [
        {"kind": "共同", "total": 1000, "payer": "家豪", "payer_id": ADMIN},
        {"kind": "共同", "total": 200, "payer": "小美", "payer_id": GF},
        {"kind": "個人", "total": 990, "payer": "小美", "payer_id": GF},
    ]
    text = cl.settle_text(rows, "10 月")
    assert "共同支出 NT$1,200（2 筆）" in text
    assert "小美 要給 家豪 NT$400" in text          # 家豪多付 1000-600
    assert "個人（不列入結算）：小美 NT$990" in text


def test_settle_single_payer_and_even():
    one = [{"kind": "共同", "total": 600, "payer": "家豪", "payer_id": ADMIN}]
    assert "對方要給 家豪 NT$300" in cl.settle_text(one, "10 月")
    even = one + [{"kind": "共同", "total": 600, "payer": "小美", "payer_id": GF}]
    assert "剛好打平" in cl.settle_text(even, "10 月")
    assert "還沒有記帳" in cl.settle_text([], "10 月")


def test_settle_queries_this_month(env, monkeypatch):
    seen = {}
    monkeypatch.setattr(notion_db, "couple_load",
                        lambda **kw: seen.update(kw) or [])
    import tz_utils
    monkeypatch.setattr(tz_utils, "today_tpe", lambda: TODAY)
    cr.handle("結算", ctx=_group())
    assert seen["since"] == "2026-10-01" and seen["until"] == "2026-10-31"
    cr.handle("上個月結算", ctx=_group())
    assert seen["since"] == "2026-09-01" and seen["until"] == "2026-09-30"


def test_undo_only_deletes_own_latest(env, monkeypatch):
    rows = [{"page_id": "p2", "date": "2026-10-07", "item": "飲料", "total": 50, "payer_id": GF},
            {"page_id": "p1", "date": "2026-10-07", "item": "午餐", "total": 120, "payer_id": ADMIN}]
    deleted = []
    monkeypatch.setattr(notion_db, "couple_load", lambda **kw: rows)
    monkeypatch.setattr(notion_db, "couple_delete", lambda pid: deleted.append(pid) or True)
    assert "午餐" in cr.handle("撤銷", ctx=_group(user=ADMIN))
    assert deleted == ["p1"]


def test_setup_reply_admin_only(env):
    other = {"source_type": "group", "user_id": GF, "group_id": "Cnew"}
    assert cl.setup_reply("記帳群組", other) is None
    mine = {"source_type": "group", "user_id": ADMIN, "group_id": "Cnew"}
    assert "Cnew" in cl.setup_reply("記帳群組", mine)
    assert "已經是記帳群組" in cl.setup_reply("記帳群組", _group())


def test_not_couple_group_without_env(monkeypatch):
    monkeypatch.delenv("COUPLE_GROUP_ID", raising=False)
    assert not cl.is_couple_chat(_group())


# ── LIFF 後端 ────────────────────────────────────────────

def test_allowed_modes(env):
    assert liff_api.allowed_modes(ADMIN) == ["couple", "personal"]
    assert liff_api.allowed_modes(GF) == ["couple"]
    assert liff_api.allowed_modes("Ustranger") == []


def test_record_rejects_without_permission(env):
    status, body = liff_api.record(GF, {"mode": "personal", "item": "午餐", "amount": 120})
    assert status == 403 and not body["ok"]


@pytest.mark.parametrize("payload", [
    {"mode": "couple", "item": "", "amount": 120},
    {"mode": "couple", "item": "午餐", "amount": 0},
    {"mode": "couple", "item": "午餐", "amount": "abc"},
])
def test_record_validates(env, payload):
    assert liff_api.record(GF, payload)[0] == 400


def test_record_couple_from_form(env, monkeypatch):
    rows = []
    monkeypatch.setattr(notion_db, "couple_add", lambda r: rows.append(r) or "pid")
    status, body = liff_api.record(GF, {"mode": "couple", "item": "晚餐",
                                        "amount": 600, "kind": "共同"})
    assert status == 200 and rows[0]["source"] == "表單" and rows[0]["payer"] == "小美"


def test_record_personal_from_form(env, monkeypatch):
    written = []
    monkeypatch.setattr(notion_db, "transaction_add", lambda t: written.append(t) or "pid")
    status, body = liff_api.record(ADMIN, {"mode": "personal", "item": "7-11",
                                           "amount": 85, "kind": "個人"})
    assert status == 200 and written[0]["shop"] == "7-11" and written[0]["amount"] == 85


def test_verify_requires_liff_id(monkeypatch):
    monkeypatch.delenv("LIFF_ID", raising=False)
    assert liff_api.verify_id_token("anything") is None
