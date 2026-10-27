"""載具發票補進交易明細：只補明細，不重複記帳。"""

from datetime import date

import pytest

import einvoice_ledger as L
import sources


def _inv(num, day, amount, seller, items=(("鮮奶", 2), ("高麗菜", 1))):
    return {"inv_num": num, "date": day, "amount": amount, "seller": seller,
            "status": "開立",
            "items": [{"name": n, "qty": q, "amount": 10} for n, q in items]}


def _txn(pid, day, amount, shop, **kw):
    return {"page_id": pid, "date": day, "amount": amount, "total": amount,
            "shop": shop, "direction": "支出", **kw}


@pytest.mark.parametrize("seller,shop,same", [
    ("全聯實業股份有限公司", "全聯福利中心－板橋", True),
    ("統一超商股份有限公司", "7-ELEVEN 貿捷", True),
    ("統一超商股份有限公司", "統一超商－貿捷", True),
    ("家福股份有限公司", "家樂福桂林店", True),
    ("全聯實業股份有限公司", "麥當勞", False),
    ("冠誠生活股份有限公司", "ＧｌｏｂａｌＭａｌｌ－板橋", True),     # 全形
    ("酷澎股份有限公司", "ＣＯＵＰＡＮＧ", True),
    ("和德昌股份有限公司中和中山分公司", "麥當勞中和", True),
    ("台灣屈臣氏個人用品商店股份有限公司", "台灣大車隊", False),   # 只共用「台灣」不算
])
def test_same_brand(seller, shop, same):
    assert L.same_brand(seller, shop) is same


def test_attach_when_everything_matches():
    acts = L.plan([_inv("AB1", date(2026, 10, 12), 356, "全聯實業股份有限公司")],
                  [_txn("p1", "2026-10-11", 356, "全聯福利中心")])
    assert [(k, t and t["page_id"]) for k, _, t in acts] == [("attach", "p1")]


def test_shared_txn_matches_on_total_not_my_half():
    t = _txn("p1", "2026-10-12", 178, "全聯福利中心", total=356)
    acts = L.plan([_inv("AB1", date(2026, 10, 12), 356, "全聯實業")], [t])
    assert acts[0][0] == "attach"


def test_same_invoice_twice_is_skipped():
    acts = L.plan([_inv("AB1", date(2026, 10, 12), 356, "全聯")],
                  [_txn("p1", "2026-10-12", 356, "全聯", invoice_no="AB1")])
    assert acts[0][0] == "skip"


def test_amount_and_same_day_counts_as_match():
    # 2026-10-12 使用者決定：金額一樣、同一天就算是，不用店名
    acts = L.plan([_inv("AB1", date(2026, 10, 12), 120, "小林餐飲有限公司")],
                  [_txn("p1", "2026-10-12", 120, "午餐")])
    assert acts[0][0] == "attach"


def test_amount_only_one_day_apart_is_unsure():
    acts = L.plan([_inv("AB1", date(2026, 10, 12), 120, "小林餐飲有限公司")],
                  [_txn("p1", "2026-10-11", 120, "午餐")])
    assert acts[0][0] == "unsure"


def test_brand_only_is_unsure():
    acts = L.plan([_inv("AB1", date(2026, 10, 12), 500, "全聯實業")],
                  [_txn("p1", "2026-10-12", 470, "全聯福利中心")])
    assert acts[0][0] == "unsure"


def test_no_card_means_new_row():
    acts = L.plan([_inv("AB1", date(2026, 10, 12), 80, "小吃店")], [])
    assert acts[0][0] == "new"
    row = L.new_txn(acts[0][1])
    assert row["invoice_no"] == "AB1" and row["source"] == "載具發票"
    assert sources.txn_display(row["source"]) == "🧾 載具發票"


def test_old_invoice_without_card_is_not_added():
    acts = L.plan([_inv("AB1", date(2026, 8, 1), 80, "小吃店")], [])
    assert acts[0][0] == "old"


def test_two_days_apart_is_not_the_same():
    acts = L.plan([_inv("AB1", date(2026, 10, 12), 356, "全聯")],
                  [_txn("p1", "2026-10-10", 356, "全聯")])
    assert acts[0][0] == "new"


def test_exact_match_wins_over_half_match():
    # 發票 B 金額跟刷卡一樣但店名不同；發票 A 完全對上 —— 刷卡要給 A
    invs = [_inv("B", date(2026, 10, 12), 55, "小林餐飲"),
            _inv("A", date(2026, 10, 12), 55, "統一超商股份有限公司")]
    acts = {i["inv_num"]: (k, t and t["page_id"])
            for k, i, t in L.plan(invs, [_txn("p1", "2026-10-12", 55, "7-ELEVEN")])}
    assert acts == {"A": ("attach", "p1"), "B": ("new", None)}


def test_voided_invoice_ignored():
    inv = _inv("AB1", date(2026, 10, 12), 80, "小吃店")
    inv["status"] = "作廢"
    assert L.plan([inv], []) == []


def test_items_text():
    inv = _inv("A", date(2026, 10, 12), 1, "全聯",
               items=(("鮮奶", 2), ("高麗菜", 1), ("折扣", 1), ("999999", 1)))
    assert L.items_text(inv) == "鮮奶 x2、高麗菜"
    assert L.items_text(inv, unsure=True).startswith("❓可能是 全聯 的發票：")


class _Notion:
    def __init__(self, review_rows=(), txns=()):
        self.attached, self.added, self.cleared, self.deleted = [], [], [], []
        self._review = list(review_rows)
        self._txns = list(txns)

    def transaction_attach_invoice(self, pid, num, items, data=None, clear_action=False):
        self.attached.append((pid, num, items))
        return True

    def transaction_add(self, txn):
        self.added.append(txn)
        return "new-id"

    def transaction_clear_invoice_action(self, pid):
        self.cleared.append(pid)
        return True

    def transaction_delete(self, pid):
        self.deleted.append(pid)
        return True

    def transactions_invoice_review(self):
        return self._review

    def transactions_load(self, **kw):
        return self._txns


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)


def test_apply_writes():
    n = _Notion()
    acts = L.plan([_inv("A", date(2026, 10, 12), 356, "全聯實業"),
                   _inv("B", date(2026, 10, 12), 80, "小吃店")],
                  [_txn("p1", "2026-10-12", 356, "全聯福利中心")])
    out = L.apply(acts, n)
    assert n.attached == [("p1", "A", "鮮奶 x2、高麗菜")]
    assert n.added[0]["invoice_no"] == "B"
    assert L.unpack(n.added[0]["invoice_data"])[0]["inv_num"] == "B"
    assert out.startswith("補明細 1、待確認 0、新增 1")


def test_pack_roundtrip_and_size_limit():
    inv = _inv("A", date(2026, 10, 12), 356, "全聯",
               items=[(f"品項{i}" * 5, 1) for i in range(300)])
    text = L.pack(inv, skip=["p9"])
    assert len(text) <= 1900
    back, skip = L.unpack(text)
    assert back["inv_num"] == "A" and back["amount"] == 356 and skip == ["p9"]
    assert L.unpack("壞掉") == (None, [])


def _review_row(pid, choice, inv, skip=(), source="國泰消費彙整"):
    return {"page_id": pid, "invoice_action": choice, "invoice_data": L.pack(inv, skip),
            "source": source}


INV = _inv("A", date(2026, 10, 12), 120, "小林餐飲")


def test_review_ok_removes_question_mark():
    n = _Notion([_review_row("p1", L.OK, INV)])
    assert L.review(n) == 1
    assert n.attached == [("p1", "A", "鮮奶 x2、高麗菜")]


def test_review_next_moves_to_next_candidate():
    others = [_txn("p1", "2026-10-12", 120, "午餐"),       # 被否決的那筆
              _txn("p2", "2026-10-13", 120, "便當")]
    n = _Notion([_review_row("p1", L.NEXT, INV)], others)
    L.review(n)
    assert n.attached[0] == ("p1", None, None)               # 先從原本那筆拿掉
    pid, num, items = n.attached[1]
    assert (pid, num) == ("p2", "A") and items.startswith("❓")
    assert L.unpack(L.pack(INV, ["p1"]))[1] == ["p1"]


def test_review_next_with_nothing_left_adds_new_row():
    n = _Notion([_review_row("p1", L.NEXT, INV)], [_txn("p1", "2026-10-12", 120, "午餐")])
    L.review(n)
    assert n.added and n.added[0]["invoice_no"] == "A"


def test_review_cash_adds_new_row():
    n = _Notion([_review_row("p1", L.CASH, INV)])
    L.review(n)
    assert n.attached == [("p1", None, None)] and n.added[0]["amount"] == 120


def test_review_next_on_invoice_own_row_deletes_it():
    n = _Notion([_review_row("p1", L.NEXT, INV, source="載具發票")],
                [_txn("p2", "2026-10-12", 120, "便當")])
    L.review(n)
    assert n.deleted == ["p1"] and n.attached[0][0] == "p2"


def test_review_without_data_just_clears():
    n = _Notion([{"page_id": "p1", "invoice_action": L.OK, "invoice_data": ""}])
    L.review(n)
    assert n.cleared == ["p1"] and not n.attached


def test_review_ok_checkbox_works_like_menu():
    row = _review_row("p1", None, INV)
    row["invoice_ok"] = True
    n = _Notion([row])
    assert L.review(n) == 1
    assert n.attached == [("p1", "A", "鮮奶 x2、高麗菜")]


def test_branch_place_name_is_not_a_brand():
    # 2026-10-27：兩邊都有「板橋」被配成同一家
    assert not L.same_brand("遠東百貨股份有限公司板橋新站分公司", "全家便利商店－板橋廣榮店")
    assert L.same_brand("全家便利商店股份有限公司新北市第一○五九分公司", "全家便利商店－板橋廣榮店")


@pytest.mark.parametrize("card,inv,kind", [
    (290, 288, "attach"),     # 差 2 元：購物袋這類零頭
    (527, 522, "attach"),     # 差 5 元
    (39, 35, "unsure"),       # 小額差 4 元超過 3%，不敢算是
    (300, 290, "unsure"),     # 差 10 元
])
def test_small_difference_same_brand(card, inv, kind):
    acts = L.plan([_inv("AB1", date(2026, 5, 31), inv, "全家便利商店股份有限公司")],
                  [_txn("p1", "2026-05-31", card, "全家便利商店－板橋廣榮店")])
    assert acts[0][0] == kind


def test_find_duplicates_groups_same_invoice():
    rows = [_txn("card", "2026-09-30", 138, "全家", invoice_no="EM1", source="cathay"),
            _txn("own", "2026-09-30", 138, "全家", invoice_no="EM1", source=L.SOURCE),
            _txn("a", "2026-07-04", 65, "義美", invoice_no="DB1", source="cathay"),
            _txn("b", "2026-07-04", 65, "嘉楓", invoice_no="DQ1", source="cathay")]
    dups = L.find_duplicates(rows)
    assert {n: [r["page_id"] for r in rs] for n, rs in dups.items()} == {"EM1": ["card", "own"]}


def test_dedup_only_flags_never_deletes():
    calls = []
    rows = [_txn("card", "2026-09-30", 138, "全家", invoice_no="EM1", source="cathay"),
            _txn("own", "2026-09-30", 138, "全家", invoice_no="EM1", source=L.SOURCE),
            # 上次勾過、另一列已經被你刪掉 → 勾要拿掉
            dict(_txn("left", "2026-09-01", 50, "A", invoice_no="ZZ1", source="cathay"), dup_flag=True)]

    class FakeNotion:
        def transactions_load(self, limit):
            return rows

        def transaction_set_duplicate(self, pid, flag):
            calls.append((pid, flag))

        def transaction_delete(self, pid):
            raise AssertionError("不該自動刪")

    report = L.dedup(FakeNotion())
    assert calls == [("card", True), ("own", True), ("left", False)]
    assert "EM1" in report and "ZZ1" not in report
    calls.clear()
    assert L.dedup(FakeNotion(), dry_run=True) and calls == []


def test_dedup_already_flagged_not_reported_again():
    rows = [dict(_txn("card", "2026-09-30", 138, "全家", invoice_no="EM1"), dup_flag=True),
            dict(_txn("own", "2026-09-30", 138, "全家", invoice_no="EM1"), dup_flag=True)]

    class FakeNotion:
        def transactions_load(self, limit):
            return rows

        def transaction_set_duplicate(self, pid, flag):
            raise AssertionError("勾過的不用再寫")

    assert L.dedup(FakeNotion()) == ""
