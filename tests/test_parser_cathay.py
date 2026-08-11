"""國泰世華「消費彙整通知」解析。

這是整套財務自動化最有價值的資料源：每天一封、逐筆、有金額商店類別。
結構取自真實信件（見 fixtures/cathay_daily.html 的說明）。
"""

import pathlib

import pytest

from parsers import cathay_daily

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "cathay_daily.html"


@pytest.fixture
def txns():
    return cathay_daily.parse(FIXTURE.read_text(encoding="utf-8"))


def test_parses_all_transactions(txns):
    assert len(txns) == 3


def test_fields_of_first_transaction(txns):
    t = txns[0]
    assert t["date"] == "2026-08-09"
    assert t["time"] == "19:42"
    assert t["amount"] == 20
    assert t["shop"] == "統一超商－貿捷"
    assert t["category"] == "超市∕量販"
    assert t["region"] == "TW"
    assert t["card_last4"] == "1234"


def test_amount_strips_prefix_and_thousands_separator(txns):
    assert txns[1]["amount"] == 1271


def test_empty_shop_is_empty_not_missing(txns):
    """非特約商店沒有店名。要留空字串，不能把類別擠上來當店名。"""
    assert txns[1]["shop"] == ""
    assert txns[1]["category"] == "旅遊"


def test_empty_mobile_card_does_not_shift_columns(txns):
    """行動卡號為空時，日期時間不能整排左移。"""
    assert txns[1]["date"] == "2026-08-09"
    assert txns[1]["time"] == "09:18"


def test_overseas_region_kept(txns):
    assert txns[2]["region"] == "US"
    assert txns[2]["shop"] == "Amazon web services"


def test_card_last4_applies_to_all(txns):
    """卡號在另一個 table，要正確套用到每一筆。"""
    assert {t["card_last4"] for t in txns} == {"1234"}


def test_status_is_pending_authorization(txns):
    """消費彙整是授權，不是最終入帳金額（外幣結匯、退款會變）。"""
    assert all(t["status"] == "授權中" for t in txns)


def test_source_is_tagged(txns):
    assert all(t["source"] == "國泰消費彙整" for t in txns)


# ── 去重指紋 ──────────────────────────────────────────────

def test_fingerprint_is_stable(txns):
    again = cathay_daily.parse(FIXTURE.read_text(encoding="utf-8"))
    assert [t["fingerprint"] for t in txns] == [t["fingerprint"] for t in again]


def test_fingerprint_differs_between_transactions(txns):
    prints = [t["fingerprint"] for t in txns]
    assert len(set(prints)) == len(prints)


def test_same_shop_same_day_different_amount_differs():
    """同一天同一間店刷兩次不同金額，是兩筆，不能被去重吃掉。"""
    a = cathay_daily.make_fingerprint("1234", "2026-08-09", 20, "全家")
    b = cathay_daily.make_fingerprint("1234", "2026-08-09", 50, "全家")
    assert a != b


def test_same_shop_same_amount_different_time_is_same_fingerprint():
    """同日同店同金額視為同一筆 —— 重跑排程不該產生重複。"""
    a = cathay_daily.make_fingerprint("1234", "2026-08-09", 20, "全家")
    b = cathay_daily.make_fingerprint("1234", "2026-08-09", 20, "全家")
    assert a == b


# ── 韌性 ──────────────────────────────────────────────────

def test_empty_html_returns_nothing():
    assert cathay_daily.parse("") == []


def test_unrelated_html_returns_nothing():
    assert cathay_daily.parse("<html><body><p>行銷信</p></body></html>") == []


def test_malformed_row_is_skipped_not_crashed():
    """欄位數不對就跳過那一筆，不要整封信解析失敗。"""
    html = """<html><body><table>
      <tr><td>卡號後4碼： 1234</td></tr></table>
      <table>
      <tr><td>卡別</td><td>行動卡號後4碼</td><td>授權日期</td><td>授權時間</td><td>消費地區</td></tr>
      <tr><td>正卡</td><td>5678</td></tr>
      <tr><td>消費金額</td><td>商店名稱</td><td>消費類別</td><td>備註</td></tr>
      <tr><td>NT$20</td><td>全家</td><td>超市∕量販</td><td></td></tr>
      </table></body></html>"""
    assert cathay_daily.parse(html) == []


def test_bad_amount_is_skipped():
    html = FIXTURE.read_text(encoding="utf-8").replace("NT$20", "NT$--")
    got = cathay_daily.parse(html)
    assert len(got) == 2, "壞掉的那筆跳過，其餘照解"
