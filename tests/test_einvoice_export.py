r"""平台「消費明細」匯出 CSV(登入電子發票整合服務平台自己下載的那個)。

跟彙整通知 email 是**兩種不同格式**,不要混淆:

| 來源 | 格式 | 分隔 | 結構 |
|------|------|------|------|
| 平台手動匯出(本檔) | 14 欄有表頭 | 逗號 | 扁平,一列一個品項 |
| 彙整通知 email | M/D 兩種行別 | `\|` | 主檔＋明細 |

本檔的欄位取自 2026-08-30 使用者實際下載的檔案(檔名格式
`{載具流水號}_{yyyymmddHHMMSS}.csv`),編碼 UTF-8-BOM、換行 LF。

匯出格式比彙整通知**多了數量與單價** —— M/D 只給小計。

實測發現的地雷(fixture 都有重現):
1. 品名裡的逗號**沒有被引號包住**,會撐出第 15 欄 —— csv 模組也救不了,
   得把第 14 欄之後的碎片黏回去
2. 檔尾有兩行單欄註解(「捐贈或作廢之發票…」「注意:本功能…」)
3. 賣方名稱有前導空白
4. 折價券那類列的金額是 0
"""

from datetime import date
from pathlib import Path

import pytest

import einvoice_csv


FIXTURE = Path(__file__).parent / "fixtures" / "einvoice_export.csv"


@pytest.fixture
def invoices():
    return einvoice_csv.parse(FIXTURE.read_text(encoding="utf-8-sig"))


# ── 聚合 ───────────────────────────────────────────────────

def test_flat_rows_group_into_invoices(invoices):
    """扁平列要依發票號碼聚合 —— 同一張發票的兩個品項不能變成兩張發票。"""
    first = invoices[0]

    assert first["inv_num"] == "AA11111111"
    assert [i["name"] for i in first["items"]] == [
        "測試蘋果80(顆)", "測試香蕉", "會員折扣",
    ]


def test_invoice_amount_is_summed_from_items(invoices):
    """發票總額要自己加總 —— 這個格式**沒有**發票總額欄位。

    第 3 欄雖然叫「發票金額」,但實測 167/167 列都等於第 12 欄
    「消費明細_金額」,而且同一張發票內會變動(46 張裡 37 張) ——
    它其實是品項金額,欄名會騙人。直接拿它當總額的話,
    一張多品項的發票只會記到第一個品項的錢。
    """
    first = invoices[0]

    assert first["amount"] == 59, "50 + 20 + (-11) 折扣"


def test_negative_discount_row_is_included(invoices):
    """折扣列是負數(實測有 -11),要算進總額而不是跳過。"""
    first = invoices[0]

    assert any(i["amount"] == -11 for i in first["items"])


def test_keeps_quantity_and_unit_price(invoices):
    """匯出格式有數量與單價,彙整通知沒有 —— 有就要留住。"""
    item = invoices[0]["items"][0]

    assert item["qty"] == 2
    assert item["unit_price"] == 25
    assert item["amount"] == 50


def test_same_shape_as_fetch_month(invoices):
    """跟 API 路徑同形,format_purchases 才能共用。"""
    inv = invoices[0]

    assert set(inv) >= {"inv_num", "seller", "amount", "date", "items"}
    assert isinstance(inv["date"], date)


# ── 真實地雷 ───────────────────────────────────────────────

def test_item_name_containing_comma_is_rejoined(invoices):
    """品名裡的逗號沒被引號包住,會多撐出一欄。

    實測案例:「松露野菇歐姆蛋-薯條,軟法」被切成第 14、15 欄。
    黏不回去的話品名會被截斷成「松露野菇歐姆蛋-薯條」,靜默失真。
    """
    inv = [i for i in invoices if i["inv_num"] == "CC33333333"][0]

    assert inv["items"][0]["name"] == "松露野菇歐姆蛋-薯條,軟法"


def test_trailing_notice_lines_are_skipped(invoices):
    """檔尾兩行單欄註解不是資料,不能變成發票。"""
    nums = [i["inv_num"] for i in invoices]

    assert not any(n.startswith("捐贈") or n.startswith("注意") for n in nums)
    assert all(len(n) == 10 for n in nums), "發票號碼應該都是 10 碼"


def test_header_row_is_not_treated_as_data(invoices):
    assert all(i["inv_num"] != "發票號碼" for i in invoices)


def test_seller_name_leading_space_is_stripped(invoices):
    """實測賣方名稱有前導空白,不 strip 的話同一家店會被當成兩家。"""
    assert invoices[0]["seller"] == "測試鮮果股份有限公司"


def test_zero_amount_item_is_kept(invoices):
    """折價券那列金額是 0 —— 0 是有效值,不能跟「解析失敗」混為一談。"""
    inv = [i for i in invoices if i["inv_num"] == "BB22222222"][0]

    assert inv["amount"] == 0
    assert inv["items"][0]["amount"] == 0


def test_semicolon_in_address_does_not_break_row(invoices):
    """實測地址含分號(「信義路1段;252號」),不該影響切欄。"""
    inv = [i for i in invoices if i["inv_num"] == "BB22222222"][0]

    assert inv["items"][0]["name"] == "折價券折抵"


# ── 作廢 ───────────────────────────────────────────────────

def test_voided_invoice_excluded_by_default(invoices):
    assert "DD44444444" not in [i["inv_num"] for i in invoices]


def test_voided_invoice_available_when_asked():
    kept = einvoice_csv.parse(
        FIXTURE.read_text(encoding="utf-8-sig"), include_voided=True
    )

    assert "DD44444444" in [i["inv_num"] for i in kept]


# ── 格式自動判別 ───────────────────────────────────────────

def test_detects_export_format_by_header():
    """兩種格式共用同一個 parse() 入口,靠內容判別而不是靠檔名。"""
    export = FIXTURE.read_text(encoding="utf-8-sig")

    assert einvoice_csv.detect_format(export) == "export"


def test_detects_notification_format_by_pipe():
    notification = "M|開立|ZZ00000050|20130111|97162640|某店|手機條碼|/EK56VW|97\n"

    assert einvoice_csv.detect_format(notification) == "notification"


# ── 與既有顯示層整合 ───────────────────────────────────────

def test_renders_through_format_purchases(invoices):
    import einvoice

    text = einvoice.format_purchases(invoices)

    assert "測試鮮果股份有限公司" in text
    assert "測試蘋果80(顆)" in text
    assert "×2" in text, "數量大於 1 要顯示出來"
