"""財政部「消費發票彙整通知」CSV 解析。

為什麼需要這條路(2026-08-30 調查結論):
`einvoice.py` 的 API 路徑已經寫好了,但**個人拿不到 AppID** ——
依「電子發票應用程式介面使用規範」,112/3/31 起個人不再列入開發者範圍,
申請門檻是 CNS27001／ISO27001 認證,只有企業與組織適用。

所以資料改走財政部主動寄送的「消費發票彙整通知」:每月一封信,
附件是 CSV,內容含品項明細 —— 正是 API 拿不到後唯一剩下的合法自動化來源。

格式(實測範例見 tests/fixtures/einvoice_carrier.csv):
    M|發票狀態|發票號碼|發票日期|商店統編|商店店名|載具名稱|載具號碼|總金額
    D|發票號碼|小計|品項名稱
分隔符是 `|` 而非逗號 —— 店名裡本來就常有逗號。
編碼 109/6 起為 UTF-8 + CRLF(在那之前是 BIG5)。

⚠️ 欄位順序尚未以真實信件驗證。使用者匯出第一份真檔後要回頭對欄位。
"""

from datetime import date
from pathlib import Path

import pytest

import einvoice_csv


FIXTURE = Path(__file__).parent / "fixtures" / "einvoice_carrier.csv"


@pytest.fixture
def invoices():
    return einvoice_csv.parse(FIXTURE.read_text(encoding="utf-8"))


# ── 結構相容 ───────────────────────────────────────────────

def test_returns_same_shape_as_fetch_month(invoices):
    """回傳結構必須跟 einvoice.fetch_month() 一致。

    這樣 format_purchases() 那套 LINE 顯示邏輯可以直接重用,
    不必為了 CSV 這條路再寫一份。
    """
    inv = invoices[0]

    assert set(inv) >= {"inv_num", "seller", "amount", "date", "items"}
    assert isinstance(inv["date"], date)
    assert isinstance(inv["items"], list)


def test_item_shape_matches_api_path(invoices):
    """品項欄位跟 _parse_items() 對齊。

    CSV 沒有數量與單價(只給小計),所以 qty/unit_price 一律 None ——
    format_purchases 的 `if qty and qty > 1` 接得住。
    """
    item = invoices[0]["items"][0]

    assert set(item) == {"name", "qty", "unit_price", "amount"}
    assert item["qty"] is None
    assert item["unit_price"] is None


# ── 主檔解析 ───────────────────────────────────────────────

def test_parses_invoice_header(invoices):
    inv = invoices[0]

    assert inv["inv_num"] == "ZZ00000050"
    assert inv["seller"] == "新北市第1000號門市"
    assert inv["amount"] == 97
    assert inv["date"] == date(2013, 1, 11)


def test_keeps_carrier_and_seller_id(invoices):
    """統編與載具號碼 API 路徑沒有,但 CSV 有 —— 留著別丟。

    統編之後要拿來把「同一家店的不同分店」歸成一類。
    """
    inv = invoices[0]

    assert inv["seller_id"] == "97162640"
    assert inv["carrier_id"] == "/WYY+.,HG"


def test_pipe_delimiter_not_comma():
    """分隔符是 `|`。用逗號切會把店名裡的逗號拆爛。"""
    csv = "M|開立|AA00000001|20260801|12345678|某某店，好吃|手機條碼|/EK56VW|100\r\n"

    inv = einvoice_csv.parse(csv)[0]

    assert inv["seller"] == "某某店，好吃"


# ── 明細歸屬 ───────────────────────────────────────────────

def test_detail_rows_attach_to_preceding_invoice(invoices):
    """D 列屬於前一張 M —— CSV 沒有巢狀結構,靠順序。"""
    first, second = invoices[0], invoices[1]

    assert [i["name"] for i in first["items"]] == ["拿鐵熱咖啡(中)", "拿鐵冰咖啡(大)"]
    assert [i["name"] for i in second["items"]] == ["鮮乳 1000ml", "豬絞肉"]


def test_detail_amount_is_numeric(invoices):
    """'42.00' → 42,不是字串,也不留無意義的小數點。"""
    assert invoices[0]["items"][0]["amount"] == 42


def test_invoice_without_details_gets_empty_items(invoices):
    """店家沒上傳品項時 items 是空 list,不是缺鍵。

    format_purchases 靠這個印「(店家未上傳品項)」。
    """
    no_detail = [i for i in invoices if i["inv_num"] == "CD87654321"][0]

    assert no_detail["items"] == []


def test_orphan_detail_without_invoice_is_dropped():
    """開頭就是 D(前面沒有 M)的話直接丟掉,不要炸掉整批。"""
    csv = "D|XX00000000|50.00|沒有主檔的品項\r\n"

    assert einvoice_csv.parse(csv) == []


# ── 作廢 ───────────────────────────────────────────────────

def test_voided_invoice_is_excluded_by_default(invoices):
    """作廢發票不該算進消費 —— 錢已經退了。"""
    nums = [i["inv_num"] for i in invoices]

    assert "EF11112222" not in nums


def test_voided_invoice_available_when_asked():
    """但要對帳時看得到,所以留一個開關而不是直接丟棄。"""
    csv = ("M|作廢|EF11112222|20260817|11112222|退貨測試店|手機條碼|/EK56VW|200\r\n"
           "D|EF11112222|200.00|退掉的東西\r\n")

    kept = einvoice_csv.parse(csv, include_voided=True)

    assert len(kept) == 1
    assert kept[0]["status"] == "作廢"


# ── 壞資料 ─────────────────────────────────────────────────

def test_empty_input_returns_empty_list():
    assert einvoice_csv.parse("") == []
    assert einvoice_csv.parse(None) == []


def test_bad_date_row_is_skipped():
    """日期壞掉的那筆跳過就好,不要讓整個月的資料一起消失。

    跟 einvoice._parse_invoice_list 同一個原則。
    """
    csv = ("M|開立|BAD0000001|not-a-date|12345678|壞日期店|手機條碼|/EK56VW|100\r\n"
           "M|開立|OK00000001|20260801|12345678|正常店|手機條碼|/EK56VW|50\r\n")

    out = einvoice_csv.parse(csv)

    assert [i["inv_num"] for i in out] == ["OK00000001"]


def test_short_row_is_skipped():
    """欄位數不足代表格式改版或那列殘缺 —— 跳過,不要猜著填。"""
    csv = "M|開立|SHORT00001|20260801\r\n"

    assert einvoice_csv.parse(csv) == []


def test_unknown_row_kind_is_ignored():
    """財政部之後加新行別(例如 H 表頭)不該讓解析整個失敗。"""
    csv = ("H|這是未來才有的表頭列|whatever\r\n"
           "M|開立|OK00000001|20260801|12345678|正常店|手機條碼|/EK56VW|50\r\n")

    assert len(einvoice_csv.parse(csv)) == 1


def test_handles_lf_only_line_endings():
    """CRLF 是官方格式,但經過 email/雲端硬碟轉一手常會變 LF。"""
    csv = "M|開立|OK00000001|20260801|12345678|正常店|手機條碼|/EK56VW|50\n"

    assert len(einvoice_csv.parse(csv)) == 1


def test_ignores_utf8_bom():
    """Excel 另存的 CSV 開頭會多一個 BOM,不處理的話第一列的 M 讀不到。"""
    csv = "﻿M|開立|OK00000001|20260801|12345678|正常店|手機條碼|/EK56VW|50\r\n"

    assert len(einvoice_csv.parse(csv)) == 1


# ── 與既有顯示層整合 ───────────────────────────────────────

def test_output_renders_through_format_purchases(invoices):
    """真正的驗收:CSV 解出來的東西餵給既有 LINE 排版不會爆。"""
    import einvoice

    text = einvoice.format_purchases(invoices)

    assert "全聯福利中心－板橋板新" in text
    assert "鮮乳 1000ml" in text
    assert "(店家未上傳品項)" in text or "（店家未上傳品項）" in text
