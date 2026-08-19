"""「記一筆」不帶參數時的兩段式 Quick Reply：點品項 → 點金額 → 記完。

原本要打「記一筆 午餐 120」，手機上打中文是最大的摩擦。
改成按「記一筆」→ 跳常記品項 → 點「午餐」→ 跳常用金額 → 點「120」。

按鈕送出的文字本身攜帶進度（記一筆 / 記一筆 午餐 / 記一筆 午餐 120），
所以三種狀態靠 arg 內容判斷，不需要對話狀態機。
"""

import pytest

import command_router as cr
import finance_report as fr


def _txn(shop, amount, source="手動"):
    return {"date": "2026-08-19", "amount": amount, "shop": shop,
            "category": "餐飲", "direction": "支出", "currency": "TWD",
            "status": "已結帳", "source": source}


# ── 分類判斷 ─────────────────────────────────────────────

@pytest.mark.parametrize("shop", ["早餐", "午餐", "晚餐", "咖啡", "飲料", "點心"])
def test_default_items_are_food(shop):
    assert fr.guess_category(shop) == "餐飲"


def test_food_keyword_inside_a_longer_name():
    """「跟同事吃午餐」也該是餐飲 —— 手打時不會剛好只打兩個字。"""
    assert fr.guess_category("跟同事吃午餐") == "餐飲"


def test_unknown_item_is_other():
    """國泰分類裡沒有「交通」，不自創類別（notion_db.py:90）。"""
    assert fr.guess_category("搭車") == "其他"


def test_blank_is_other():
    assert fr.guess_category("") == "其他"
    assert fr.guess_category(None) == "其他"


def test_parse_manual_uses_guessed_category():
    assert fr.parse_manual("午餐 120")["category"] == "餐飲"
    assert fr.parse_manual("搭車 30")["category"] == "其他"


# ── 常記品項 ─────────────────────────────────────────────

def test_frequent_items_ranks_by_count():
    txns = [_txn("午餐", 120), _txn("午餐", 100), _txn("午餐", 150),
            _txn("咖啡", 55), _txn("咖啡", 65),
            _txn("搭車", 30)]

    assert fr.frequent_expense_items(txns, limit=3, pad=False) == [
        "午餐", "咖啡", "搭車"]


def test_frequent_items_ignores_auto_synced():
    """信用卡同步的店名放按鈕上沒意義，還會被 LINE 截成半截。"""
    txns = [_txn("全聯福利中心－板橋板新", 361, source="國泰消費彙整"),
            _txn("全聯福利中心－板橋板新", 210, source="國泰消費彙整"),
            _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False) == ["午餐"]


def test_frequent_items_ties_keep_first_seen_order():
    """同次數時位置要穩定：按鈕每次都在跳比排序不準更難用。"""
    txns = [_txn("咖啡", 55), _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False) == ["咖啡", "午餐"]


def test_frequent_items_ignores_blank_names():
    txns = [_txn("", 100), _txn("   ", 100), _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False) == ["午餐"]


def test_frequent_items_pads_with_defaults():
    """第一天沒歷史，給空按鈕列等於這個功能不存在。"""
    assert fr.frequent_expense_items([], limit=6) == [
        "午餐", "晚餐", "早餐", "咖啡", "飲料", "點心"]


def test_padding_never_duplicates_history():
    txns = [_txn("咖啡", 55)]

    out = fr.frequent_expense_items(txns, limit=6)

    assert out[0] == "咖啡"
    assert out.count("咖啡") == 1
    assert len(out) == 6


def test_history_always_outranks_padding():
    txns = [_txn("搭車", 30)]

    assert fr.frequent_expense_items(txns, limit=6)[0] == "搭車"


def test_frequent_items_respects_limit():
    txns = [_txn(f"品項{i}", 100) for i in range(20)]

    assert len(fr.frequent_expense_items(txns, limit=6)) == 6


# ── 常用金額 ─────────────────────────────────────────────

def test_amounts_rank_by_count():
    txns = [_txn("午餐", 120), _txn("午餐", 120), _txn("午餐", 100)]

    assert fr.frequent_amounts(txns, "午餐", limit=5, pad=False) == [120, 100]


def test_amounts_are_per_item():
    """共用一份全域金額清單會讓咖啡的按鈕上出現 200 元。"""
    txns = [_txn("午餐", 120), _txn("咖啡", 55)]

    assert fr.frequent_amounts(txns, "咖啡", limit=5, pad=False) == [55]


def test_amounts_ignore_auto_synced():
    txns = [_txn("午餐", 999, source="國泰消費彙整"), _txn("午餐", 120)]

    assert fr.frequent_amounts(txns, "午餐", limit=5, pad=False) == [120]


def test_amounts_pad_with_seeds():
    """第一天沒歷史，金額按鈕不能是空的。"""
    assert fr.frequent_amounts([], "午餐") == [100, 120, 150]


def test_seeds_never_duplicate_history():
    txns = [_txn("午餐", 120)]

    out = fr.frequent_amounts(txns, "午餐")

    assert out[0] == 120
    assert out.count(120) == 1


def test_unknown_item_has_no_seed_amounts():
    """使用者自己打的品項沒有種子金額 —— 呼叫端要據此不放 quickReply，
    空的 quickReply 物件會被 LINE 當格式錯誤整則退回。"""
    assert fr.frequent_amounts([], "搭車") == []


def test_unknown_item_still_learns_from_history():
    txns = [_txn("搭車", 30), _txn("搭車", 30), _txn("搭車", 45)]

    assert fr.frequent_amounts(txns, "搭車") == [30, 45]


def test_blank_item_returns_empty():
    assert fr.frequent_amounts([_txn("午餐", 120)], "") == []


def test_amounts_are_ints_when_whole():
    """按鈕 label 不要出現 120.0。"""
    txns = [_txn("午餐", 120.0)]

    assert fr.frequent_amounts(txns, "午餐", pad=False) == [120]


# ── 三態分流 ─────────────────────────────────────────────

class FakeNotion:
    def __init__(self, txns=None, configured=True, write_ok=True):
        self._txns = txns or []
        self.added = []
        self._configured = configured
        self._write_ok = write_ok

    def is_configured(self):
        return self._configured

    def transactions_load(self, limit=200):
        return list(self._txns)

    def transaction_add(self, txn):
        if not self._write_ok:
            return None
        self.added.append(txn)
        return "page-fake"


@pytest.fixture
def fake_notion(monkeypatch):
    def _install(**kwargs):
        import sys
        fake = FakeNotion(**kwargs)
        monkeypatch.setitem(sys.modules, "notion_db", fake)
        return fake
    return _install


def _ctx():
    return {"source_type": "user", "user_id": "U1"}


def test_bare_command_returns_item_buttons(fake_notion):
    fake_notion(txns=[_txn("午餐", 120), _txn("午餐", 100), _txn("咖啡", 55)])

    reply = cr.handle("記一筆", _ctx())

    labels = [i["action"]["label"] for i in reply["quickReply"]["items"]]
    assert labels[0] == "午餐"


def test_item_buttons_send_parseable_commands(fake_notion):
    """按鈕送出的字串必須自己 parse 得回來，不然點了沒反應。"""
    fake_notion(txns=[_txn("午餐", 120)])

    reply = cr.handle("記一筆", _ctx())

    for item in reply["quickReply"]["items"]:
        sent = item["action"]["text"]
        assert cr.parse(sent) == ("fin_manual", sent.split(" ", 1)[1])


def test_item_only_returns_amount_buttons(fake_notion):
    fake_notion(txns=[_txn("午餐", 120), _txn("午餐", 120), _txn("午餐", 100)])

    reply = cr.handle("記一筆 午餐", _ctx())

    labels = [i["action"]["label"] for i in reply["quickReply"]["items"]]
    assert labels[:2] == ["120", "100"]


def test_amount_buttons_send_complete_commands(fake_notion):
    fake_notion(txns=[_txn("午餐", 120)])

    reply = cr.handle("記一筆 午餐", _ctx())

    for item in reply["quickReply"]["items"]:
        sent = item["action"]["text"]
        assert sent.startswith("記一筆 午餐 ")
        assert cr.parse(sent)[0] == "fin_manual"


def test_full_flow_actually_writes(fake_notion):
    """整條線走完：點品項 → 點金額 → 真的進 Notion。"""
    fake = fake_notion(txns=[_txn("午餐", 120)])

    step1 = cr.handle("記一筆", _ctx())
    item_cmd = step1["quickReply"]["items"][0]["action"]["text"]
    step2 = cr.handle(item_cmd, _ctx())
    amount_cmd = step2["quickReply"]["items"][0]["action"]["text"]

    cr.handle(amount_cmd, _ctx())

    assert len(fake.added) == 1
    assert fake.added[0]["shop"] == "午餐"
    assert fake.added[0]["amount"] == 120
    assert fake.added[0]["category"] == "餐飲"


def test_unknown_item_falls_back_to_text(fake_notion):
    """沒種子金額的品項只給文字提示 —— 空 quickReply 會被 LINE 整則退回。"""
    fake_notion(txns=[])

    reply = cr.handle("記一筆 搭車", _ctx())

    assert isinstance(reply, str)
    assert "記一筆 搭車" in reply


def test_complete_command_is_unchanged(fake_notion):
    """有帶完整參數時行為照舊，不要因為加按鈕改掉主要路徑。"""
    fake = fake_notion()

    reply = cr.handle("記一筆 午餐 120", _ctx())

    assert isinstance(reply, str)
    assert "已記錄" in reply
    assert len(fake.added) == 1


def test_reply_shows_category(fake_notion):
    fake_notion()

    reply = cr.handle("記一筆 午餐 120", _ctx())

    assert "餐飲" in reply


def test_income_still_detected(fake_notion):
    fake = fake_notion()

    cr.handle("記一筆 薪水 50000", _ctx())

    assert fake.added[0]["direction"] == "收入"


def test_falls_back_to_text_when_notion_down(fake_notion):
    fake_notion(configured=False)

    reply = cr.handle("記一筆", _ctx())

    assert isinstance(reply, str) and "記一筆" in reply


def test_write_failure_is_readable(fake_notion):
    fake_notion(write_ok=False)

    reply = cr.handle("記一筆 午餐 120", _ctx())

    assert isinstance(reply, str) and "失敗" in reply


def test_bare_command_still_explains_typed_form(fake_notion):
    """按鈕只能點常見的，特殊金額還是要打字 —— 用法不能消失。"""
    fake_notion(txns=[_txn("午餐", 120)])

    reply = cr.handle("記一筆", _ctx())

    assert "記一筆" in reply["text"]


# ── Rich Menu ────────────────────────────────────────────

def test_record_cell_sends_bare_command():
    """選單的「記一筆」要送裸指令才會跳品項按鈕；prompt 只會開鍵盤。"""
    import setup_richmenu as rm

    cells = rm.MENUS["finance"]["cells"]
    kind, param = next(c[3] for c in cells if c[0] == "記一筆")

    assert (kind, param) == ("message", "記一筆")
    assert cr.parse(param) == ("fin_manual", None)


def test_record_cell_does_not_fall_through_to_paid_ai():
    """裸指令沒被 command_router 認得會掉進 free_query —— 按一次付一次
    Anthropic 的錢，而且不會壞、不會有紅字（HANDOFF 4.4）。"""
    import setup_richmenu as rm

    cells = rm.MENUS["finance"]["cells"]
    _, param = next(c[3] for c in cells if c[0] == "記一筆")

    assert cr.parse(param)[0] != "free_query"
