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


# ── 群組待辦（兩人共用）與面板 ─────────────────────────────

def test_group_todo_uses_group_as_owner(env, monkeypatch):
    import personal
    seen = []
    monkeypatch.setattr(cr, "_handle_todo_subcmd", lambda key, body: seen.append((key, body)) or "ok")
    monkeypatch.setattr(personal, "list_todos", lambda key: seen.append((key, None)) or [])
    cr.handle("待辦 繳電費 1200", ctx=_group(user=GF))   # 不能被當成一筆帳
    cr.handle("待辦", ctx=_group(user=ADMIN))
    assert seen == [(GROUP, "繳電費 1200"), (GROUP, None)]


def test_group_todo_postback_uses_group(env, monkeypatch):
    import personal
    done = []
    monkeypatch.setattr(personal, "delete_todo", lambda key, tid: done.append((key, tid)) or True)
    monkeypatch.setattr(personal, "list_todos", lambda key: [])
    cr.handle_postback("action=todo_complete&id=3", GF, ctx=_group(user=GF))
    cr.handle_postback("action=todo_complete&id=4", ADMIN, ctx=DM)
    assert done == [(GROUP, 3), (ADMIN, 4)]


def test_panel_has_form_button_when_configured(env, monkeypatch):
    monkeypatch.setenv("LIFF_ID", "123-abc")
    card = cr.handle("選單", ctx=_group())
    first = card["contents"]["body"]["contents"][2]
    assert first["action"]["uri"] == "https://liff.line.me/123-abc"
    assert first["color"] == "#FFD400"


def test_richmenu_ledger_cell(monkeypatch):
    import setup_richmenu as sr
    main = sr.MENUS["main"]["cells"]
    ledger = [c for c in main if c[1] == "LEDGER"][0]
    monkeypatch.setenv("LIFF_ID", "123-abc")
    area = sr.build_areas([ledger])[0]["action"]
    assert area == {"type": "uri", "uri": "https://liff.line.me/123-abc"}
    monkeypatch.delenv("LIFF_ID")
    assert sr.build_areas([ledger])[0]["action"]["type"] == "message"
    assert sr._ink_for("#FFD400") != "white" and sr._ink_for("#F0AD4E") == "white"
    # 煮飯沒有消失，只是搬到「更多」
    assert any(c[3] == ("switch", "kitchen") for c in sr.MENUS["more"]["cells"])


# ── 交易明細 → 共同帳本 自動同步 ────────────────────────────

def _shared_txn(fp="fp1", split="共同", direction="支出"):
    return {"date": "2026-10-05", "shop": "全聯福利中心－板橋", "amount": 210,
            "total": 420, "split_type": split, "direction": direction,
            "category": "超市", "fingerprint": fp}


def test_mirror_shared_txn_uses_full_total_and_admin_as_payer(env, monkeypatch):
    rows = []
    monkeypatch.setattr(notion_db, "couple_has_source", lambda fp: False)
    monkeypatch.setattr(notion_db, "couple_add", lambda r: rows.append(r) or "pid")
    assert cl.mirror_txn(_shared_txn()) is True
    r = rows[0]
    assert r["total"] == 420 and r["payer_id"] == ADMIN and r["payer"] == "家豪"
    assert r["source"] == "自動同步" and r["source_id"] == "fp1" and r["date"] == "2026-10-05"


@pytest.mark.parametrize("txn", [_shared_txn(split="個人"),
                                 _shared_txn(direction="收入"),
                                 _shared_txn(fp="")])
def test_mirror_skips(env, monkeypatch, txn):
    monkeypatch.setattr(notion_db, "couple_has_source", lambda fp: False)
    monkeypatch.setattr(notion_db, "couple_add", lambda r: pytest.fail("不該同步"))
    assert cl.mirror_txn(txn) is False


def test_mirror_dedupes(env, monkeypatch):
    monkeypatch.setattr(notion_db, "couple_has_source", lambda fp: True)
    monkeypatch.setattr(notion_db, "couple_add", lambda r: pytest.fail("重複了"))
    assert cl.mirror_txn(_shared_txn()) is False


def test_backfill_counts(env, monkeypatch):
    seen = set()
    monkeypatch.setattr(notion_db, "transactions_load",
                        lambda **kw: [_shared_txn("a"), _shared_txn("a"),
                                      _shared_txn("b", split="個人"), _shared_txn("c")])
    monkeypatch.setattr(notion_db, "couple_has_source", lambda fp: fp in seen)
    monkeypatch.setattr(notion_db, "couple_add", lambda r: seen.add(r["source_id"]) or "pid")
    assert cl.backfill() == 2


# ── 依月份結算 ───────────────────────────────────────────

@pytest.mark.parametrize("text,offset", [
    ("結算", 0), ("上個月結算", -1), ("結算 9月", -1), ("9月結算", -1),
    ("結算 2026-08", -2), ("結算 12", -10),          # 10 月打 12 月 = 去年 12 月
    ("結算 2025年12月", -10), ("結算 11月", -11),
])
def test_settle_offset(text, offset):
    assert cl._settle_offset(text, TODAY) == offset


@pytest.mark.parametrize("text", ["結算 13", "結算 2027-01", "結算了嗎"])
def test_settle_offset_rejects(text):
    assert cl._settle_offset(text, TODAY) is None


def test_settle_reply_has_month_buttons(env, monkeypatch):
    import tz_utils
    monkeypatch.setattr(tz_utils, "today_tpe", lambda: TODAY)
    monkeypatch.setattr(notion_db, "couple_load", lambda **kw: [])
    msg = cr.handle("結算 8月", ctx=_group())
    assert "8 月" in msg["text"]
    labels = [i["action"]["label"] for i in msg["quickReply"]["items"]]
    assert labels == ["7 月", "9 月", "10 月"]


def test_panel_has_notion_link(env, monkeypatch):
    monkeypatch.setattr(notion_db, "couple_db_url", lambda: "https://www.notion.so/abc")
    body = cr.handle("選單", ctx=_group())["contents"]["body"]["contents"]
    assert body[-1]["action"]["uri"] == "https://www.notion.so/abc"


# ── 圓餅圖 ───────────────────────────────────────────────

def test_chart_slices_merge_and_skip_personal():
    import couple_chart as cc
    rows = [{"kind": "共同", "item": "全聯福利中心－板橋", "total": 400},
            {"kind": "共同", "item": "全聯福利中心－新店", "total": 100},
            {"kind": "個人", "item": "衣服", "total": 990}]
    rows += [{"kind": "共同", "item": f"品項{i}", "total": 10 + i} for i in range(8)]
    parts = cc.slices(rows)
    assert parts[0] == ("全聯福利中心", 500)
    assert len(parts) == 7 and parts[-1][0] == "其他"
    assert all(name != "衣服" for name, _v in parts)


def test_chart_command_returns_image(env, monkeypatch):
    import couple_chart as cc
    import tz_utils
    monkeypatch.setattr(tz_utils, "today_tpe", lambda: TODAY)
    monkeypatch.setattr(notion_db, "couple_load", lambda **kw: [
        {"kind": "共同", "item": "晚餐", "total": 600, "date": "2026-10-01"}])
    msgs = cr.handle("圖表", ctx=_group())
    img = msgs[0]
    assert img["type"] == "image" and img["originalContentUrl"].endswith(".png")
    token = img["originalContentUrl"].rsplit("/", 1)[1][:-4]
    assert cc.get(token).startswith(b"\x89PNG")
    assert [o["action"]["label"] for o in msgs[1]["quickReply"]["items"]] == ["9 月", "看結算"]


def test_chart_without_data_is_text(env, monkeypatch):
    monkeypatch.setattr(notion_db, "couple_load", lambda **kw: [])
    assert "沒有圖可以畫" in cr.handle("圖表 9月", ctx=_group())
