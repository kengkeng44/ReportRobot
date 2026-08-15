"""「買了什麼」指令：把發票品項送到 LINE。

沒設定財政部 API 時要回「怎麼設定」，不能靜默失敗也不能丟例外 ——
這個功能的門檻是使用者去申請 AppID，講不清楚就等於沒做。
"""

import sys
from datetime import date

import pytest

import command_router as cr


def _ctx(source_type="user"):
    return {"source_type": source_type, "user_id": "U1"}


class FakeEInvoice:
    """只換掉會碰網路的部分，format_purchases 用真的。"""

    def __init__(self, invoices=None, configured=True, error=None):
        self._invoices = invoices or []
        self._configured = configured
        self._error = error
        self.calls = []

    def is_configured(self):
        return self._configured

    def fetch_month(self, year, month, **kw):
        self.calls.append((year, month))
        if self._error:
            raise self._error
        return self._invoices


@pytest.fixture
def fake_einvoice(monkeypatch):
    import einvoice as real

    def _install(**kwargs):
        fake = FakeEInvoice(**kwargs)
        # format_purchases / EInvoiceError 用真的，只換取數與設定判斷
        fake.format_purchases = real.format_purchases
        fake.EInvoiceError = real.EInvoiceError
        monkeypatch.setitem(sys.modules, "einvoice", fake)
        return fake

    return _install


def _inv(seller, day, amount, *items):
    return {
        "inv_num": "X", "seller": seller, "amount": amount, "date": day,
        "items": [{"name": n, "qty": 1, "unit_price": None, "amount": None}
                  for n in items],
    }


# ── parse ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["/買了什麼", "買了什麼", "/品項", "/發票", "/明細"])
def test_command_parses(text):
    assert cr.parse(text) == ("einvoice_items", None)


def test_does_not_collide_with_pantry_add():
    """「買了 高麗菜」是入庫，「買了什麼」是查發票 —— 不能互相吃掉。"""
    assert cr.parse("買了 高麗菜1顆")[0] == "pantry_add"
    assert cr.parse("買了什麼")[0] == "einvoice_items"
    assert cr.parse("買了")[0] == "pantry_add"


def test_is_personal_only():
    """買了什麼菜是個人消費資料，不該在家人群組被查。"""
    assert "einvoice_items" in cr._PERSONAL_KINDS


def test_blocked_in_group(fake_einvoice):
    fake_einvoice(invoices=[])

    reply = cr.handle("/買了什麼", _ctx(source_type="group"))

    assert reply == cr.PERSONAL_ONLY_MSG


# ── handle ─────────────────────────────────────────────────

def test_shows_items(fake_einvoice):
    fake_einvoice(invoices=[
        _inv("全聯福利中心－板橋板新", date(2026, 8, 13), 361, "高麗菜", "雞蛋"),
    ])

    reply = cr.handle("/買了什麼", _ctx())

    assert "全聯" in reply and "高麗菜" in reply


def test_uses_current_month(fake_einvoice, monkeypatch):
    fake = fake_einvoice(invoices=[])
    monkeypatch.setattr(cr, "_today", lambda: date(2026, 8, 16))

    cr.handle("/買了什麼", _ctx())

    assert fake.calls == [(2026, 8)]


def test_unconfigured_explains_how_to_set_up(fake_einvoice):
    fake_einvoice(configured=False)

    reply = cr.handle("/買了什麼", _ctx())

    assert "手機條碼" in reply
    assert "einvoice.nat.gov.tw" in reply, "要給申請網址，不然講了也做不到"


def test_api_error_is_readable(fake_einvoice):
    import einvoice as real
    fake_einvoice(error=real.EInvoiceError("手機條碼驗證碼錯誤,檢查 EINVOICE_CARD_ENCRYPT"))

    reply = cr.handle("/買了什麼", _ctx())

    assert "驗證碼" in reply


def test_unexpected_error_does_not_leak_stack(fake_einvoice):
    fake_einvoice(error=RuntimeError("boom"))

    reply = cr.handle("/買了什麼", _ctx())

    assert isinstance(reply, str)
    assert "Traceback" not in reply


def test_empty_month_explains_the_carrier_requirement(fake_einvoice):
    """查無資料最可能的原因是結帳沒掃載具，要講出來。"""
    fake_einvoice(invoices=[])

    reply = cr.handle("/買了什麼", _ctx())

    assert "載具" in reply


# ── 說明文 ─────────────────────────────────────────────────

def test_help_documents_the_command():
    assert "買了什麼" in cr.HELP_TEXT
