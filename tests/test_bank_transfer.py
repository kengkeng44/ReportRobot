"""轉帳通知信解析 + 轉帳截圖辨識。信件內容照 2026-10 實際收到的格式縮寫。"""

from datetime import date

import pytest

import transfer_ocr
from parsers import bank_transfer as bt

FUBON_RENT = """<table>
<tr><td>臺幣轉帳 成功</td></tr>
<tr><td>交易結果</td><td>交易成功</td></tr>
<tr><td>交易時間</td><td>2026/07/06 09:51:40</td></tr>
<tr><td>轉出帳號</td><td>淡水分行 816800****9279 即時餘額 $15,274</td><td>轉出金額</td><td>TWD 12,870</td></tr>
<tr><td>轉入帳號</td><td>005(土地銀行)-00001370****0594 洪○芳</td><td>手續費</td><td>TWD 15</td></tr>
<tr><td>存摺留言（給自己）</td><td>鄭仁和 租屋</td><td>存摺留言(給對方)</td><td>鄭仁和 租屋</td></tr>
</table>"""

FUBON_CARD = FUBON_RENT.replace(
    "005(土地銀行)-00001370****0594 洪○芳",
    "013(國泰世華)-42843008****6120 虛擬帳號收款－○泰世華商業銀行信用卡作業○")

CUBE_CARD = """<table>
<tr><td>親愛的客戶 您好： 您於2026/10/09 17:55:31 之即時非約定轉帳結果如下：</td></tr>
<tr><td>轉出帳號</td><td>0000239****80811</td></tr>
<tr><td>轉入銀行</td><td>013國泰世華</td></tr>
<tr><td>轉入帳號</td><td>4284300****16120</td></tr>
<tr><td>轉帳金額</td><td>8000</td></tr>
<tr><td>手續費</td><td>0</td></tr>
<tr><td>備註</td><td></td></tr>
<tr><td>轉帳結果</td><td>交易成功</td></tr>
</table>"""

CUBE_FRIEND = (CUBE_CARD.replace("013國泰世華", "822中國信託")
               .replace("4284300****16120", "1234567****8899")
               .replace("<td>8000</td>", "<td>1,250</td>")
               .replace("<td>備註</td><td></td>", "<td>備註</td><td>火鍋</td>"))


def test_fubon_rent_parsed():
    [t] = bt.parse_fubon(FUBON_RENT)
    assert t["date"] == "2026-07-06" and t["time"] == "09:51"
    assert t["amount"] == 12870 and t["direction"] == "轉帳"
    assert t["shop"] == "↗️ 轉給 洪○芳"
    assert "租屋" in t["items"] and "手續費 15" in t["items"]
    assert t["source"] == "富邦轉帳"


def test_card_payments_skipped():
    assert bt.parse_fubon(FUBON_CARD) == []
    assert bt.parse_cathay_cube(CUBE_CARD) == []


def test_cube_transfer_to_friend():
    [t] = bt.parse_cathay_cube(CUBE_FRIEND)
    assert t["amount"] == 1250 and t["time"] == "17:55"
    assert t["shop"] == "↗️ 轉給 中國信託 8899"
    assert "留言：火鍋" in t["items"]


def test_failed_transfer_ignored():
    assert bt.parse_fubon(FUBON_RENT.replace("交易成功", "交易失敗")) == []
    assert bt.parse_cathay_cube(CUBE_FRIEND.replace("交易成功", "交易失敗")) == []


def test_unrelated_fubon_mail_ignored():
    assert bt.parse_fubon("<p>您已登入台北富邦行動銀行</p>") == []


def test_self_transfer_skipped(monkeypatch):
    monkeypatch.setenv("OWNER_REAL_NAME", "王小明")
    html = FUBON_RENT.replace("洪○芳", "王○明")
    assert bt.parse_fubon(html) == []
    monkeypatch.delenv("OWNER_REAL_NAME")
    assert len(bt.parse_fubon(html)) == 1     # 沒設名字就不判斷


# ── 截圖 ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(transfer_ocr, "today_tpe", lambda: date(2026, 10, 24))


def test_rows_to_txns():
    rows = [
        {"date": "2026-10-20", "time": "12:03", "direction": "in", "amount": 500,
         "counterparty": "小螢", "channel": "iPASS MONEY", "memo": "晚餐", "skip": ""},
        {"date": "2026-10-21", "time": "", "direction": "out", "amount": "320",
         "counterparty": "", "channel": "郵局", "memo": "", "skip": ""},
        {"date": "2026-10-22", "direction": "out", "amount": 999, "skip": "繳信用卡費"},
        {"date": "2026-11-30", "direction": "in", "amount": 10, "skip": ""},      # 未來
        {"date": "10/22", "direction": "in", "amount": 10, "skip": ""},           # 格式壞
    ]
    txns, skipped = transfer_ocr.to_txns(rows)
    assert [t["shop"] for t in txns] == ["↙️ 收到 小螢", "↗️ 轉給 （未知）"]
    assert txns[0]["items"] == "iPASS MONEY｜留言：晚餐"
    assert txns[1]["amount"] == 320 and txns[1]["time"] == ""
    assert len(skipped) == 3


def test_screenshot_matches_email_fingerprint():
    """截圖讀到的轉出跟富邦信是同一筆 → 指紋一樣 → 不會重複記。"""
    [mail] = bt.parse_fubon(FUBON_RENT)
    txns, _ = transfer_ocr.to_txns([{"date": "2026-07-06", "time": "9:51", "direction": "out",
                                     "amount": 12870, "counterparty": "洪O芳", "skip": ""}])
    assert txns[0]["fingerprint"] == mail["fingerprint"]


class _FakeNotion:
    def __init__(self, seen):
        self.seen, self.added = set(seen), []

    def transactions_existing_fingerprints(self, limit=400, source_names=None):
        assert source_names                     # 一定要用來源篩，不然舊的轉帳會讀不到
        return set(self.seen)

    def transaction_add(self, t):
        self.added.append(t)
        return "page"


def test_record_dedupes_exact_and_timeless():
    [mail] = bt.parse_fubon(FUBON_RENT)
    txns, _ = transfer_ocr.to_txns([
        {"date": "2026-07-06", "time": "", "direction": "out", "amount": 12870, "skip": ""},  # 沒時間，同日同額
        {"date": "2026-07-06", "time": "", "direction": "in", "amount": 12870, "skip": ""},   # 方向不同
        {"date": "2026-07-07", "time": "08:00", "direction": "out", "amount": 50, "skip": ""},
    ])
    fake = _FakeNotion({mail["fingerprint"]})
    written, dup = transfer_ocr.record(txns, notion=fake)
    assert len(dup) == 1 and len(written) == 2
    # 同一批再送一次：全部重複
    fake2 = _FakeNotion({mail["fingerprint"]} | {t["fingerprint"] for t in written})
    w2, d2 = transfer_ocr.record(txns, notion=fake2)
    assert w2 == [] and len(d2) == 3


def test_extract_parses_json_from_model():
    class Block:
        text = '好的：\n[{"date":"2026-10-20","time":"12:03","direction":"in","amount":500,"counterparty":"小螢","skip":""}]'

    class Msg:
        content = [Block()]

    rows = transfer_ocr.extract(b"img", "image/png", api_key="k", create=lambda *a, **k: Msg())
    assert rows[0]["amount"] == 500


def test_extract_garbage_returns_empty():
    class Msg:
        content = [type("B", (), {"text": "看不出來"})()]
    assert transfer_ocr.extract(b"x", "image/png", api_key="k", create=lambda *a, **k: Msg()) == []


def test_handle_image_reply(monkeypatch):
    monkeypatch.setattr(transfer_ocr, "download_line_image", lambda mid: (b"x", "image/jpeg"))
    monkeypatch.setattr(transfer_ocr, "extract", lambda img, media: [
        {"date": "2026-10-20", "time": "12:03", "direction": "in", "amount": 500,
         "counterparty": "小螢", "channel": "iPASS MONEY", "skip": ""}])
    monkeypatch.setattr(transfer_ocr, "record", lambda txns: (txns, []))
    reply = transfer_ocr.handle_line_image("m1")
    assert "記了 1 筆" in reply and "10/20 ↙️ 收到 小螢 500" in reply


def test_handle_image_nothing_found(monkeypatch):
    monkeypatch.setattr(transfer_ocr, "download_line_image", lambda mid: (b"x", "image/jpeg"))
    monkeypatch.setattr(transfer_ocr, "extract", lambda img, media: [])
    assert "沒看到" in transfer_ocr.handle_line_image("m1")


def test_transfer_sources_registered():
    import finance_sync
    parsers = [p for _, p in finance_sync.TRANSFER_SOURCES]
    assert bt.fubon in parsers and bt.cathay_cube in parsers
    assert len(finance_sync.TRANSFER_SOURCES) == 2


def test_webhook_routes_images_to_ocr():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "server.py").read_text(encoding="utf-8")
    assert 'msg.get("type") == "image"' in src and "transfer_ocr.handle_line_image" in src
    assert "_is_admin(img_ctx)" in src            # 只有本人私訊的圖才處理
    assert 'id="transfer_backfill_boot"' in src
