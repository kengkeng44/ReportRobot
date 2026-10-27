"""/admin/transfer-ocr —— 本機 LINE 掃描把截圖直接 POST 進來，跟私訊截圖走同一條路。

server.py 本機 import 不了（見 test_admin_personal.py），端點只讀原始碼守住驗證；
共用邏輯 transfer_ocr.handle_image 直接測。
"""

from pathlib import Path

import transfer_ocr

SERVER = Path(__file__).resolve().parent.parent / "server.py"


def _handler():
    src = SERVER.read_text(encoding="utf-8")
    start = src.index("async def transfer_ocr_upload(")
    return src[start: src.index("\n@app.", start)]


def test_endpoint_requires_admin_token():
    body = _handler()
    assert '@app.post("/admin/transfer-ocr")' in SERVER.read_text(encoding="utf-8")
    assert 'request.headers.get("X-Admin-Token") != admin_token' in body
    assert "status_code=403" in body


def test_endpoint_reuses_handle_image():
    assert "transfer_ocr.handle_image" in _handler()


def test_line_image_delegates_to_handle_image(monkeypatch):
    seen = {}
    monkeypatch.setattr(transfer_ocr, "download_line_image", lambda mid: (b"img", "image/png"))
    monkeypatch.setattr(transfer_ocr, "handle_image",
                        lambda img, media: seen.setdefault("args", (img, media)) and "ok")
    assert transfer_ocr.handle_line_image("123") == "ok"
    assert seen["args"] == (b"img", "image/png")


def test_handle_image_writes_and_reports(monkeypatch):
    rows = [{"date": "2026-10-01", "time": "12:30", "direction": "in", "amount": 500,
             "counterparty": "小螢", "channel": "iPASS MONEY", "memo": "", "skip": ""},
            {"date": "2026-10-01", "time": "", "direction": "in", "amount": 10,
             "counterparty": "", "channel": "", "memo": "", "skip": "只是聊天"}]
    monkeypatch.setattr(transfer_ocr, "extract", lambda img, media: rows)
    monkeypatch.setattr(transfer_ocr, "record", lambda txns: (txns, []))
    text = transfer_ocr.handle_image(b"img", "image/png")
    assert "記了 1 筆轉帳" in text
    assert "↙️ 收到 小螢 500" in text


def test_handle_image_survives_ai_failure(monkeypatch):
    def boom(img, media):
        raise RuntimeError("down")
    monkeypatch.setattr(transfer_ocr, "extract", boom)
    assert "辨識失敗" in transfer_ocr.handle_image(b"img", "image/png")


def test_prompt_ignores_chat_claims():
    assert "只是聊天" in transfer_ocr._PROMPT
