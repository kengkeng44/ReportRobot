"""財政部電子發票載具 API：把「買了什麼菜」真的抓出來。

為什麼需要這個(2026-08-16 調查結論):
信用卡授權電文只傳「商店代號 + 總金額」給發卡行,所以國泰彙整信永遠只有
「全聯福利中心－板橋板新 NT$361」,買了什麼銀行根本收不到。換張卡也一樣。

但使用者結帳時出示手機條碼載具的話,完整品項明細就在財政部平台上。
財政部只寄中獎通知,不寄明細 —— 要自己用 API 撈。

規格來源:電子發票應用 API 規格 v1.9(財政部財政資訊中心)第二章五、六節。
⚠️ 網路上的 openapi.yaml 寫 application/json 是錯的,官方規格第一章四節
明訂 CONTENT-TYPE 為 application/x-www-form-urlencoded。
"""

from datetime import date, datetime

import pytest

import einvoice


# ── 設定 ───────────────────────────────────────────────────

def test_not_configured_without_credentials(monkeypatch):
    for k in ("EINVOICE_APP_ID", "EINVOICE_CARD_NO", "EINVOICE_CARD_ENCRYPT"):
        monkeypatch.delenv(k, raising=False)

    assert einvoice.is_configured() is False


def test_configured_needs_all_three(monkeypatch):
    monkeypatch.setenv("EINVOICE_APP_ID", "app")
    monkeypatch.setenv("EINVOICE_CARD_NO", "/EK1234")
    monkeypatch.delenv("EINVOICE_CARD_ENCRYPT", raising=False)

    assert einvoice.is_configured() is False, "少驗證碼就不算設定好"

    monkeypatch.setenv("EINVOICE_CARD_ENCRYPT", "abcd")
    assert einvoice.is_configured() is True


# ── 時間戳記 ───────────────────────────────────────────────

def test_timestamp_is_within_spec_offset():
    """規格第一章七:時間戳記要比現在多 10~180 秒,超出範圍平台會拒收。"""
    now = 1334499000

    ts = int(einvoice._timestamp(now=now))

    assert 1334499010 <= ts <= 1334499180


# ── 送出的參數 ─────────────────────────────────────────────

@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("EINVOICE_APP_ID", "APP123")
    monkeypatch.setenv("EINVOICE_CARD_NO", "/EK56VW")
    monkeypatch.setenv("EINVOICE_CARD_ENCRYPT", "secret")


def test_header_payload_has_every_required_field(configured):
    payload = einvoice._carrier_chk_payload("2026/08/01", "2026/08/31")

    # 規格第二章五、需求參數
    for key in ("version", "cardType", "cardNo", "expTimeStamp", "action",
                "timeStamp", "startDate", "endDate", "onlyWinningInv",
                "uuid", "appID", "cardEncrypt"):
        assert key in payload, f"缺必填參數 {key}"

    assert payload["action"] == "carrierInvChk"
    assert payload["cardType"] == "3J0002", "手機條碼的卡別"
    assert payload["onlyWinningInv"] == "N", "要全部發票，不是只要中獎的"
    assert payload["version"] == "0.6", "表頭查詢 113/1/1 起是 0.6"


def test_detail_payload_has_every_required_field(configured):
    payload = einvoice._carrier_detail_payload("AB12345678", "2026/08/13")

    for key in ("version", "cardType", "cardNo", "expTimeStamp", "action",
                "timeStamp", "invNum", "invDate", "uuid", "appID", "cardEncrypt"):
        assert key in payload, f"缺必填參數 {key}"

    assert payload["action"] == "carrierInvDetail"
    assert payload["version"] == "0.5", "明細查詢仍是 0.5"
    assert payload["invNum"] == "AB12345678"


def test_payload_never_leaks_credentials_into_logs(configured, capsys):
    """驗證碼等同密碼，不能印出來。"""
    einvoice._carrier_chk_payload("2026/08/01", "2026/08/31")

    assert "secret" not in capsys.readouterr().out


# ── 回應處理 ───────────────────────────────────────────────

def _hdr(inv_num, seller, amount, y=2026, m=8, d=13):
    return {
        "invNum": inv_num, "sellerName": seller, "amount": amount,
        "invDate": {"year": y, "month": m, "date": d},
    }


def test_reads_invoice_list(configured):
    body = {"code": "200", "details": [
        _hdr("AB11111111", "全聯福利中心－板橋板新", "361"),
        _hdr("AB22222222", "全家便利商店", "35"),
    ]}

    rows = einvoice._parse_invoice_list(body)

    assert [r["inv_num"] for r in rows] == ["AB11111111", "AB22222222"]
    assert rows[0]["seller"] == "全聯福利中心－板橋板新"
    assert rows[0]["amount"] == 361
    assert rows[0]["date"] == date(2026, 8, 13)


def test_invoice_date_month_is_not_zero_based(configured):
    """invDate 是 JS Date 序列化來的，month 有可能 0-based。
    實際規格範例是 1-based，這裡釘死避免哪天全部差一個月。"""
    rows = einvoice._parse_invoice_list(
        {"code": "200", "details": [_hdr("A", "店", "1", y=2026, m=1, d=5)]})

    assert rows[0]["date"] == date(2026, 1, 5)


def test_bad_date_does_not_kill_the_batch(configured):
    body = {"code": "200", "details": [
        {"invNum": "A", "sellerName": "壞的", "amount": "1", "invDate": {}},
        _hdr("B", "好的", "2"),
    ]}

    rows = einvoice._parse_invoice_list(body)

    assert [r["inv_num"] for r in rows] == ["B"], "壞資料跳過，不要整批炸掉"


def test_reads_line_items(configured):
    body = {"code": "200", "details": [
        {"rowNum": "1", "description": "高麗菜", "quantity": "1",
         "unitPrice": "49", "amount": "49"},
        {"rowNum": "2", "description": "雞蛋 10入", "quantity": "2",
         "unitPrice": "65", "amount": "130"},
    ]}

    items = einvoice._parse_items(body)

    assert [i["name"] for i in items] == ["高麗菜", "雞蛋 10入"]
    assert items[1]["qty"] == 2
    assert items[1]["amount"] == 130


def test_item_with_unparseable_numbers_still_shows_the_name(configured):
    """品名才是重點。數字爛掉不該讓整筆消失。"""
    body = {"code": "200", "details": [
        {"description": "手工麵包", "quantity": "", "unitPrice": "-", "amount": ""},
    ]}

    items = einvoice._parse_items(body)

    assert items[0]["name"] == "手工麵包"
    assert items[0]["amount"] is None


# ── 錯誤碼 ─────────────────────────────────────────────────

@pytest.mark.parametrize("code,hint", [
    ("919", "驗證碼"),
    ("950", "查詢次數"),
    ("998", "AppID"),
    ("903", "參數"),
])
def test_error_codes_become_readable(code, hint):
    """規格第一章六節的訊息碼。回「code 919」對使用者沒有意義。"""
    msg = einvoice.explain_code(code)

    assert hint in msg


def test_unknown_code_still_returns_something():
    assert einvoice.explain_code("12345")


def test_non_200_raises_with_explanation(configured):
    with pytest.raises(einvoice.EInvoiceError) as e:
        einvoice._parse_invoice_list({"code": "919", "msg": "參數驗證碼錯誤"})

    assert "驗證碼" in str(e.value)


# ── 分頁(996) ──────────────────────────────────────────────

def test_pagination_follows_996(configured, monkeypatch):
    """996 = 這頁滿了還有下一頁。不跟的話會默默少算後面所有發票。"""
    pages = {
        1: {"code": "996", "details": [_hdr("A1", "店", "1")]},
        2: {"code": "996", "details": [_hdr("A2", "店", "2")]},
        3: {"code": "200", "details": [_hdr("A3", "店", "3")]},
    }
    seen = []

    def fake_post(payload):
        page = int(payload.get("page", 1))
        seen.append(page)
        return pages[page]

    monkeypatch.setattr(einvoice, "_post", fake_post)

    rows = einvoice.list_invoices(2026, 8)

    assert [r["inv_num"] for r in rows] == ["A1", "A2", "A3"]
    assert seen == [1, 2, 3]


def test_pagination_has_a_hard_stop(configured, monkeypatch):
    """平台一直回 996 的話不能無限打下去 —— 那是 DoS 自己也 DoS 財政部。"""
    monkeypatch.setattr(einvoice, "_post",
                        lambda payload: {"code": "996", "details": [_hdr("A", "店", "1")]})

    rows = einvoice.list_invoices(2026, 8, max_pages=5)

    assert len(rows) == 5


def test_query_range_is_one_month(configured, monkeypatch):
    """規格限制 startDate/endDate 必須同月份。"""
    captured = {}

    def fake_post(payload):
        captured.update(payload)
        return {"code": "200", "details": []}

    monkeypatch.setattr(einvoice, "_post", fake_post)

    einvoice.list_invoices(2026, 2)

    assert captured["startDate"] == "2026/02/01"
    assert captured["endDate"] == "2026/02/28", "閏年以外的二月是 28 天"


# ── 組起來 ─────────────────────────────────────────────────

def test_fetch_month_attaches_items(configured, monkeypatch):
    monkeypatch.setattr(einvoice, "list_invoices", lambda y, m, **kw: [
        {"inv_num": "AB1", "seller": "全聯", "amount": 361, "date": date(2026, 8, 13)},
    ])
    monkeypatch.setattr(einvoice, "invoice_detail", lambda num, day: [
        {"name": "高麗菜", "qty": 1, "unit_price": 49, "amount": 49},
    ])

    rows = einvoice.fetch_month(2026, 8)

    assert rows[0]["items"][0]["name"] == "高麗菜"


def test_one_bad_detail_does_not_lose_the_whole_month(configured, monkeypatch):
    """某張發票明細抓失敗，其他張還是要拿得到。"""
    monkeypatch.setattr(einvoice, "list_invoices", lambda y, m, **kw: [
        {"inv_num": "BAD", "seller": "壞的", "amount": 1, "date": date(2026, 8, 1)},
        {"inv_num": "OK", "seller": "全聯", "amount": 2, "date": date(2026, 8, 2)},
    ])

    def flaky(num, day):
        if num == "BAD":
            raise einvoice.EInvoiceError("查無此發票詳細資料")
        return [{"name": "高麗菜", "qty": 1, "unit_price": 49, "amount": 49}]

    monkeypatch.setattr(einvoice, "invoice_detail", flaky)

    rows = einvoice.fetch_month(2026, 8)

    assert len(rows) == 2
    assert rows[0]["items"] == []
    assert rows[1]["items"][0]["name"] == "高麗菜"


# ── 排版 ───────────────────────────────────────────────────

def _inv(seller, day, amount, *items):
    return {
        "inv_num": "X", "seller": seller, "amount": amount, "date": day,
        "items": [{"name": n, "qty": q, "unit_price": None, "amount": a}
                  for n, q, a in items],
    }


def test_format_shows_shop_and_items():
    text = einvoice.format_purchases([
        _inv("全聯福利中心－板橋板新", date(2026, 8, 13), 361,
             ("高麗菜", 1, 49), ("雞蛋 10入", 2, 130)),
    ])

    assert "全聯" in text
    assert "8/13" in text
    assert "高麗菜" in text
    assert "雞蛋 10入" in text


def test_format_marks_invoices_without_items():
    """有些店只上傳總額不上傳品項。要講明是「店家沒給」，
    不然看起來像程式壞了。"""
    text = einvoice.format_purchases([_inv("某小吃店", date(2026, 8, 13), 120)])

    assert "某小吃店" in text
    assert "店家未上傳" in text


def test_format_says_when_there_is_nothing():
    text = einvoice.format_purchases([])

    assert "沒有" in text or "查無" in text


def test_format_truncates_long_output():
    """LINE 單則 5000 字上限。一個月幾十張發票塞不下。"""
    many = [_inv(f"店{i}", date(2026, 8, 1), 100,
                 *[(f"品項{j}", 1, 10) for j in range(20)])
            for i in range(40)]

    text = einvoice.format_purchases(many)

    assert len(text) <= 4500


def test_format_newest_first():
    text = einvoice.format_purchases([
        _inv("舊的", date(2026, 8, 1), 1, ("A", 1, 1)),
        _inv("新的", date(2026, 8, 20), 1, ("B", 1, 1)),
    ])

    assert text.index("新的") < text.index("舊的")
