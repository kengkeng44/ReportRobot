"""財政部「消費發票彙整通知」→ 食材庫存的自動同步。

跟 finance_sync 同一個形狀(Gmail → parser → 去重 → Notion),差別在
國泰那封是 **HTML 內文**,財政部這封是 **CSV 附件** —— 附件要多一次
attachments().get() 才拿得到內容。

⚠️ 寄件人位址與主旨**尚未以真實信件驗證**(使用者還沒開通該服務)。
查詢條件先用寬鬆的主旨關鍵字 + has:attachment,收到第一封後要回來校正 ——
查詢寫太死會靜默抓不到信,而「沒抓到」跟「沒有新信」在 log 上長得一樣。
"""

import base64

import pytest

import einvoice_sync


def _b64(text):
    """Gmail 的附件內容是 base64url。"""
    raw = text.encode("utf-8") if isinstance(text, str) else text
    return base64.urlsafe_b64encode(raw).decode("ascii")


CSV_TEXT = (
    "載具自訂名稱,發票日期,發票號碼,發票金額,發票狀態,折讓,賣方統一編號,"
    "賣方名稱,賣方地址,買方統編,消費明細_數量,消費明細_單價,消費明細_金額,消費明細_品名\n"
    "手機條碼,20260901,AA11111111,50,開立已確認,否,11111111,測試超市,"
    "台北市測試路1號,,2,25,50,青江菜產銷履歷\n"
)


class FakeAttachments:
    def __init__(self, store):
        self._store = store

    def get(self, userId, messageId, id):
        return _Exec({"data": self._store[id]})


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class FakeMessages:
    def __init__(self, listing, messages, attachments):
        self._listing = listing
        self._messages = messages
        self._attachments = attachments

    def list(self, **kwargs):
        return _Exec(self._listing)

    def get(self, **kwargs):
        return _Exec(self._messages[kwargs["id"]])

    def attachments(self):
        return FakeAttachments(self._attachments)


class FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class FakeService:
    def __init__(self, listing, messages, attachments):
        self._users = FakeUsers(FakeMessages(listing, messages, attachments))

    def users(self):
        return self._users


def _service_with_csv(filename="einvoice.csv", csv_text=CSV_TEXT):
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>您的消費彙整</p>")}},
            {"mimeType": "text/csv", "filename": filename,
             "body": {"attachmentId": "att1"}},
        ],
    }
    return FakeService(
        listing={"messages": [{"id": "m1"}]},
        messages={"m1": {"payload": payload}},
        attachments={"att1": _b64(csv_text)},
    )


# ── 找附件 ─────────────────────────────────────────────────

def test_finds_csv_attachment():
    payload = _service_with_csv()._users.messages()._messages["m1"]["payload"]

    parts = list(einvoice_sync._csv_parts(payload))

    assert len(parts) == 1
    assert parts[0]["filename"] == "einvoice.csv"


def test_ignores_non_csv_attachment():
    """信裡可能附 PDF 說明,不要拿去餵 CSV parser。"""
    payload = {"parts": [
        {"filename": "說明.pdf", "body": {"attachmentId": "a"}},
        {"filename": "readme.txt", "body": {"attachmentId": "b"}},
    ]}

    assert list(einvoice_sync._csv_parts(payload)) == []


def test_finds_csv_nested_in_multipart():
    """附件常包在巢狀 multipart 裡,要遞迴找。"""
    payload = {"parts": [
        {"parts": [
            {"filename": "data.CSV", "body": {"attachmentId": "deep"}},
        ]},
    ]}

    parts = list(einvoice_sync._csv_parts(payload))

    assert len(parts) == 1, "副檔名大小寫不該影響"


def test_attachment_without_id_is_skipped():
    """沒有 attachmentId 的節點抓不到內容,略過而不是炸掉。"""
    payload = {"parts": [{"filename": "x.csv", "body": {}}]}

    assert list(einvoice_sync._csv_parts(payload)) == []


# ── 解碼 ───────────────────────────────────────────────────

def test_decodes_utf8_with_bom():
    """平台匯出帶 BOM;109/6 起彙整通知也是 UTF-8。"""
    data = _b64("﻿名稱,數量\n青江菜,1\n")

    text = einvoice_sync._decode_attachment(data)

    assert text.startswith("名稱") or text.startswith("﻿名稱")
    assert "青江菜" in text


def test_falls_back_to_big5():
    """109/6 之前的舊檔是 BIG5。解不開就整封信報廢太可惜。"""
    data = base64.urlsafe_b64encode("青江菜".encode("big5")).decode("ascii")

    text = einvoice_sync._decode_attachment(data)

    assert "青江菜" in text


def test_undecodable_returns_empty():
    """兩種編碼都不通就回空字串,讓呼叫端跳過該封,不要丟例外。"""
    data = base64.urlsafe_b64encode(b"\xff\xfe\x00\x01\x80\x81").decode("ascii")

    assert einvoice_sync._decode_attachment(data) == ""


# ── 整合 ───────────────────────────────────────────────────

def test_fetch_rows_from_service():
    """一封信 → 可寫入的食材列。"""
    rows = einvoice_sync.fetch_rows(_service_with_csv())

    assert len(rows) == 1
    assert rows[0]["name"] == "青江菜產銷履歷"
    assert rows[0]["source"] == "載具發票"


def test_no_messages_returns_empty():
    service = FakeService(listing={}, messages={}, attachments={})

    assert einvoice_sync.fetch_rows(service) == []


def test_broken_message_does_not_stop_the_batch():
    """一封信壞掉不該讓整批中斷 —— 這個工作跟每日推播共用 process。"""
    class Exploding(FakeMessages):
        def get(self, **kwargs):
            if kwargs["id"] == "bad":
                raise RuntimeError("boom")
            return super().get(**kwargs)

    good = _service_with_csv()
    inner = good._users.messages()
    service = FakeService(
        listing={"messages": [{"id": "bad"}, {"id": "m1"}]},
        messages=inner._messages,
        attachments=inner._attachments,
    )
    service._users._messages = Exploding(
        service._users._messages._listing,
        inner._messages,
        inner._attachments,
    )

    rows = einvoice_sync.fetch_rows(service)

    assert len(rows) == 1, "壞的那封略過,好的那封照常解析"
