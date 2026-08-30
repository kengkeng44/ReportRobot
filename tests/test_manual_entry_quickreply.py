"""「記一筆」不帶參數時的兩段式 Quick Reply：點品項 → 點金額 → 記完。

原本要打「記一筆 午餐 120」，手機上打中文是最大的摩擦。
改成按「記一筆」→ 跳常記品項 → 點「午餐」→ 跳常用金額 → 點「120」。

按鈕送出的文字本身攜帶進度（記一筆 / 記一筆 午餐 / 記一筆 午餐 120），
所以三種狀態靠 arg 內容判斷，不需要對話狀態機。
"""

import pytest
from datetime import date, timedelta

import command_router as cr
import finance_report as fr


# 測試用的「今天」。統計函式一律傳這個進去 —— 寫死日期加上 90 天窗
# 等於埋一顆定時炸彈，會在某個沒人動過程式碼的日子突然全部變紅。
TODAY = date(2026, 8, 30)


def _txn(shop, amount, source="手動", days_ago=0, split_type="個人", total=None):
    day = TODAY - timedelta(days=days_ago)
    return {"date": day.isoformat(), "amount": amount, "shop": shop,
            "category": "餐飲", "direction": "支出", "currency": "TWD",
            "status": "已結帳", "source": source,
            "split_type": split_type,
            "total": total if total is not None else amount}


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

    assert fr.frequent_expense_items(txns, limit=3, pad=False, today=TODAY) == [
        "午餐", "咖啡", "搭車"]


def test_frequent_items_ignores_auto_synced():
    """信用卡同步的店名放按鈕上沒意義，還會被 LINE 截成半截。"""
    txns = [_txn("全聯福利中心－板橋板新", 361, source="國泰消費彙整"),
            _txn("全聯福利中心－板橋板新", 210, source="國泰消費彙整"),
            _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False, today=TODAY) == ["午餐"]


def test_frequent_items_ties_keep_first_seen_order():
    """同次數時位置要穩定：按鈕每次都在跳比排序不準更難用。"""
    txns = [_txn("咖啡", 55), _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False, today=TODAY) == ["咖啡", "午餐"]


def test_frequent_items_ignores_blank_names():
    txns = [_txn("", 100), _txn("   ", 100), _txn("午餐", 120)]

    assert fr.frequent_expense_items(txns, limit=6, pad=False, today=TODAY) == ["午餐"]


def test_frequent_items_pads_with_defaults():
    """第一天沒歷史，給空按鈕列等於這個功能不存在。"""
    assert fr.frequent_expense_items([], limit=6, today=TODAY) == [
        "午餐", "晚餐", "早餐", "咖啡", "飲料", "點心"]


def test_padding_never_duplicates_history():
    txns = [_txn("咖啡", 55)]

    out = fr.frequent_expense_items(txns, limit=6, today=TODAY)

    assert out[0] == "咖啡"
    assert out.count("咖啡") == 1
    assert len(out) == 6


def test_history_always_outranks_padding():
    txns = [_txn("搭車", 30)]

    assert fr.frequent_expense_items(txns, limit=6, today=TODAY)[0] == "搭車"


def test_frequent_items_respects_limit():
    txns = [_txn(f"品項{i}", 100) for i in range(20)]

    assert len(fr.frequent_expense_items(txns, limit=6, today=TODAY)) == 6


def test_frequent_items_ignores_records_older_than_90_days():
    """物價會漲，兩年前那個 65 元的咖啡不該還卡在按鈕上。"""
    txns = [_txn("舊品項", 100, days_ago=120),
            _txn("舊品項", 100, days_ago=200),
            _txn("午餐", 120, days_ago=5)]

    assert fr.frequent_expense_items(
        txns, limit=6, pad=False, today=TODAY) == ["午餐"]


def test_frequent_items_weight_recent_records_higher():
    """各記一次，近的排前面。沒有權重的話兩者同分，順序只看誰先出現。"""
    txns = [_txn("舊愛", 100, days_ago=75),      # ×1
            _txn("新歡", 200, days_ago=3)]       # ×3

    assert fr.frequent_expense_items(
        txns, limit=6, pad=False, today=TODAY) == ["新歡", "舊愛"]


def test_frequent_items_boundary_at_exactly_90_days():
    """剛好 90 天算數，91 天不算 —— 邊界寫清楚，日後才不會各自解讀。"""
    txns = [_txn("剛好", 100, days_ago=90), _txn("過期", 100, days_ago=91)]

    assert fr.frequent_expense_items(
        txns, limit=6, pad=False, today=TODAY) == ["剛好"]


# ── 常用金額 ─────────────────────────────────────────────

def test_amounts_rank_by_count():
    txns = [_txn("午餐", 120), _txn("午餐", 120), _txn("午餐", 100)]

    assert fr.frequent_amounts(txns, "午餐", limit=5, pad=False, today=TODAY) == [120, 100]


def test_amounts_are_per_item():
    """共用一份全域金額清單會讓咖啡的按鈕上出現 200 元。"""
    txns = [_txn("午餐", 120), _txn("咖啡", 55)]

    assert fr.frequent_amounts(txns, "咖啡", limit=5, pad=False, today=TODAY) == [55]


def test_amounts_ignore_auto_synced():
    txns = [_txn("午餐", 999, source="國泰消費彙整"), _txn("午餐", 120)]

    assert fr.frequent_amounts(txns, "午餐", limit=5, pad=False, today=TODAY) == [120]


def test_amounts_pad_with_seeds():
    """第一天沒歷史，金額按鈕不能是空的。"""
    assert fr.frequent_amounts([], "午餐", today=TODAY) == [100, 120, 150]


def test_seeds_never_duplicate_history():
    txns = [_txn("午餐", 120)]

    out = fr.frequent_amounts(txns, "午餐", today=TODAY)

    assert out[0] == 120
    assert out.count(120) == 1


def test_unknown_item_has_no_seed_amounts():
    """使用者自己打的品項沒有種子金額 —— 呼叫端要據此不放 quickReply，
    空的 quickReply 物件會被 LINE 當格式錯誤整則退回。"""
    assert fr.frequent_amounts([], "搭車", today=TODAY) == []


def test_unknown_item_still_learns_from_history():
    txns = [_txn("搭車", 30), _txn("搭車", 30), _txn("搭車", 45)]

    assert fr.frequent_amounts(txns, "搭車", today=TODAY) == [30, 45]


def test_blank_item_returns_empty():
    assert fr.frequent_amounts([_txn("午餐", 120)], "", today=TODAY) == []


def test_amounts_are_ints_when_whole():
    """按鈕 label 不要出現 120.0。"""
    txns = [_txn("午餐", 120.0)]

    assert fr.frequent_amounts(txns, "午餐", pad=False, today=TODAY) == [120]


def test_amounts_use_gross_not_my_share():
    """按鈕上的數字是使用者要打進去的錢（整桌 600），不是分攤額（300）。
    用金額欄統計的話，共同消費的按鈕每次砍半，愈跳愈小。"""
    txns = [_txn("晚餐", 300, split_type="共同", total=600),
            _txn("晚餐", 300, split_type="共同", total=600)]

    assert fr.frequent_amounts(
        txns, "晚餐", pad=False, today=TODAY) == [600]


def test_amounts_prefer_the_usual_split_type_of_that_item():
    """「個人/共同」問在最後一段，跳金額按鈕時還不知道這筆屬於哪種。
    用品項自己的歷史推斷：晚餐幾乎都是共同的就跳共同價位。"""
    txns = [_txn("晚餐", 300, split_type="共同", total=600),
            _txn("晚餐", 300, split_type="共同", total=600),
            _txn("晚餐", 300, split_type="共同", total=620),
            _txn("晚餐", 150, split_type="個人", total=150)]

    out = fr.frequent_amounts(txns, "晚餐", pad=False, today=TODAY)

    assert 150 not in out
    assert out[0] == 600


def test_amounts_fall_back_to_all_records_when_sample_too_small():
    """一兩筆推不出習慣，硬推會讓按鈕少到不夠用。"""
    txns = [_txn("宵夜", 100, split_type="共同", total=200),
            _txn("宵夜", 80, split_type="個人", total=80)]

    out = fr.frequent_amounts(txns, "宵夜", pad=False, today=TODAY)

    assert sorted(out) == [80, 200]


def test_amounts_ignore_records_older_than_90_days():
    txns = [_txn("午餐", 95, days_ago=150), _txn("午餐", 130, days_ago=2)]

    assert fr.frequent_amounts(
        txns, "午餐", pad=False, today=TODAY) == [130]


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
    """整條線走完：點品項 → 點金額 → 點個人/共同 → 真的進 Notion。

    2026-08-30 起多了第三段。這是設計變更（分攤類型放在最後一段），
    不是回歸 —— 見 docs/superpowers/specs/2026-08-30-shared-expense-split-design.md
    """
    fake = fake_notion(txns=[_txn("午餐", 120)])

    step1 = cr.handle("記一筆", _ctx())
    item_cmd = step1["quickReply"]["items"][0]["action"]["text"]
    step2 = cr.handle(item_cmd, _ctx())
    amount_cmd = step2["quickReply"]["items"][0]["action"]["text"]
    step3 = cr.handle(amount_cmd, _ctx())
    split_cmd = step3["quickReply"]["items"][0]["action"]["text"]

    cr.handle(split_cmd, _ctx())

    assert len(fake.added) == 1
    assert fake.added[0]["shop"] == "午餐"
    assert fake.added[0]["amount"] == 120
    assert fake.added[0]["split_type"] == "個人"
    assert fake.added[0]["category"] == "餐飲"


def test_unknown_item_falls_back_to_text(fake_notion):
    """沒種子金額的品項只給文字提示 —— 空 quickReply 會被 LINE 整則退回。"""
    fake_notion(txns=[])

    reply = cr.handle("記一筆 搭車", _ctx())

    assert isinstance(reply, str)
    assert "記一筆 搭車" in reply


def test_complete_command_is_unchanged(fake_notion):
    """帶滿品項/金額/分攤類型時行為照舊，不要因為加按鈕改掉主要路徑。

    2026-08-30 起「完整參數」多了分攤類型這一項 —— 這是設計變更
    （第四態把個人/共同放到最後一段問），不是回歸。
    """
    fake = fake_notion()

    reply = cr.handle("記一筆 午餐 120 個人", _ctx())

    assert isinstance(reply, str)
    assert "已記錄" in reply
    assert len(fake.added) == 1


def test_reply_shows_category(fake_notion):
    fake_notion()

    reply = cr.handle("記一筆 午餐 120 個人", _ctx())

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
    """要真的走到寫入那一步才測得到失敗訊息 —— 分攤類型不給的話
    現在（設計變更後）會先跳個人/共同按鈕，不會碰 Notion。"""
    fake_notion(write_ok=False)

    reply = cr.handle("記一筆 午餐 120 個人", _ctx())

    assert isinstance(reply, str) and "失敗" in reply


def test_bare_command_still_explains_typed_form(fake_notion):
    """按鈕只能點常見的，特殊金額還是要打字 —— 用法不能消失。"""
    fake_notion(txns=[_txn("午餐", 120)])

    reply = cr.handle("記一筆", _ctx())

    assert "記一筆" in reply["text"]


# ── 第四態：個人 / 共同 ───────────────────────────────────

def test_amount_without_split_type_returns_split_buttons(fake_notion):
    fake_notion(txns=[_txn("晚餐", 300, split_type="共同", total=600)])

    reply = cr.handle("記一筆 晚餐 600", _ctx())

    labels = [i["action"]["label"] for i in reply["quickReply"]["items"]]
    assert labels == ["個人", "共同"]


def test_split_buttons_send_complete_commands(fake_notion):
    """按鈕送出的字串必須自己 parse 得回來，不然點了沒反應。"""
    fake_notion(txns=[])

    reply = cr.handle("記一筆 晚餐 600", _ctx())

    sent = [i["action"]["text"] for i in reply["quickReply"]["items"]]
    assert sent == ["記一筆 晚餐 600 個人", "記一筆 晚餐 600 共同"]
    for s in sent:
        assert cr.parse(s)[0] == "fin_manual"


def test_split_buttons_work_without_notion(fake_notion):
    """走到這一段的人已經把品項與金額打完了，不該卡在最後一步。
    兩顆靜態按鈕不碰 Notion。"""
    fake_notion(configured=False)

    reply = cr.handle("記一筆 晚餐 600", _ctx())

    assert reply["quickReply"]["items"][1]["action"]["text"] == "記一筆 晚餐 600 共同"


def test_shared_entry_writes_only_my_share(fake_notion):
    fake = fake_notion(txns=[])

    reply = cr.handle("記一筆 晚餐 600 共同", _ctx())

    assert len(fake.added) == 1
    assert fake.added[0]["amount"] == 300      # 金額欄 = 我實際負擔
    assert fake.added[0]["total"] == 600
    assert fake.added[0]["split_type"] == "共同"
    assert "300" in reply and "600" in reply    # 兩個數字都要看得到


def test_personal_entry_writes_full_amount(fake_notion):
    fake = fake_notion(txns=[])

    cr.handle("記一筆 午餐 120 個人", _ctx())

    assert fake.added[0]["amount"] == 120
    assert fake.added[0]["total"] == 120
    assert fake.added[0]["split_type"] == "個人"


def test_income_skips_the_split_question(fake_notion):
    """薪水不用跟人分 —— 不該多問一段。"""
    fake = fake_notion(txns=[])

    cr.handle("記一筆 薪水 50000", _ctx())

    assert len(fake.added) == 1
    assert fake.added[0]["direction"] == "收入"


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
