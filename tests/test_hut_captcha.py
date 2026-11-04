import base64
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import command_router as cr
import hut_captcha as hc
import server

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
T0 = datetime(2026, 11, 4, 7, 0)


@pytest.fixture(autouse=True)
def _clean():
    hc.reset()
    yield
    hc.reset()


def _open(now=T0, step="apply"):
    sent = []
    out = hc.open_challenge(PNG, "排雲山莊 12/12 2人", step, now=now, push=sent.append)
    return out["id"], sent


def test_open_pushes_summary_and_image():
    cid, sent = _open()
    text, img = sent[0]
    assert "排雲山莊 12/12 2人" in text and "驗證碼 取消" in text
    assert img["type"] == "image" and "/hut-img/" in img["originalContentUrl"]
    name = img["originalContentUrl"].rsplit("/", 1)[1]
    assert hc.get_image(name, now=T0) == (PNG, "image/png")
    assert hc.poll(cid, now=T0) == {"state": "waiting"}


def test_answer_then_poll_gets_code():
    cid, _ = _open()
    assert "收到 AB12" in hc.answer_from_line("驗證碼 AB 12", now=T0)
    assert hc.poll(cid, now=T0) == {"state": "answered", "code": "AB12"}
    assert "處理過" in hc.answer_from_line("驗證碼 9999", now=T0)


def test_cancel():
    cid, _ = _open()
    assert "取消" in hc.answer_from_line("驗證碼 取消", now=T0)
    assert hc.poll(cid, now=T0)["state"] == "cancelled"


def test_expired():
    cid, _ = _open()
    later = T0 + hc.CAPTCHA_TTL + timedelta(seconds=1)
    assert "過期" in hc.answer_from_line("驗證碼 1234", now=later)
    assert hc.poll(cid, now=later)["state"] == "expired"
    assert hc.get_image("x.png", now=later) is None


def test_new_challenge_replaces_old():
    old, _ = _open()
    new, _ = _open()
    assert hc.poll(old, now=T0)["state"] == "unknown"
    assert hc.poll(new, now=T0)["state"] == "waiting"


def test_no_pending():
    assert "沒有在等" in hc.answer_from_line("驗證碼 1234", now=T0)


def test_rejects_non_image_and_empty_summary():
    with pytest.raises(ValueError):
        hc.open_challenge(b"GIF89a", "x", now=T0, push=lambda m: None)
    with pytest.raises(ValueError):
        hc.open_challenge(PNG, "  ", now=T0, push=lambda m: None)


def test_report_result():
    cid, _ = _open()
    sent = []
    hc.report_result(cid, "送出成功", PNG, now=T0, push=sent.append)
    assert sent[0][0] == "送出成功" and sent[0][1]["type"] == "image"
    with pytest.raises(ValueError):
        hc.report_result("nope", "x", now=T0, push=sent.append)


def test_router_admin_only(monkeypatch):
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")
    cid, _ = _open(now=hc.now_tpe())
    other = {"source_type": "user", "user_id": "Uother"}
    assert "🔒" in cr.handle("驗證碼 1234", other)
    assert hc.poll(cid)["state"] == "waiting"
    admin = {"source_type": "user", "user_id": "Uadmin"}
    assert "收到 1234" in cr.handle("驗證碼 1234", admin)
    assert hc.poll(cid) == {"state": "answered", "code": "1234"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "t0k")
    monkeypatch.setattr(hc, "_push", lambda messages: None)
    return TestClient(server.app)


H = {"X-Admin-Token": "t0k"}


def test_endpoints_require_token(client):
    assert client.post("/admin/hut-captcha", json={}).status_code == 403
    assert client.get("/admin/hut-captcha/x").status_code == 403
    assert client.post("/admin/hut-captcha/x/result", json={}).status_code == 403
    assert client.post("/admin/hut-notify", json={}).status_code == 403


def test_endpoint_flow(client):
    body = {"image_b64": base64.b64encode(PNG).decode(), "summary": "九九 12/5 2人"}
    r = client.post("/admin/hut-captcha", json=body, headers=H)
    assert r.status_code == 200
    cid = r.json()["id"]
    assert client.get(f"/admin/hut-captcha/{cid}", headers=H).json() == {"state": "waiting"}
    hc.answer_from_line("驗證碼 5678")
    assert client.get(f"/admin/hut-captcha/{cid}", headers=H).json()["code"] == "5678"
    r = client.post(f"/admin/hut-captcha/{cid}/result", json={"text": "ok"}, headers=H)
    assert r.status_code == 200


def test_endpoint_bad_input(client):
    assert client.post("/admin/hut-captcha", json={"summary": "x"}, headers=H).status_code == 400
    bad = {"image_b64": "!!", "summary": "x"}
    assert client.post("/admin/hut-captcha", json=bad, headers=H).status_code == 400
    assert client.post("/admin/hut-captcha", json=[1], headers=H).status_code == 400


def test_image_route(client):
    sent = []
    hc.open_challenge(PNG, "x", push=sent.append)
    url = sent[0][1]["originalContentUrl"]
    r = client.get("/hut-img/" + url.rsplit("/", 1)[1])
    assert r.status_code == 200 and r.content == PNG
    assert client.get("/hut-img/nope.png").status_code == 404


def test_push_failure_cancels():
    def boom(messages):
        raise RuntimeError("LINE 推播失敗")
    with pytest.raises(RuntimeError):
        hc.open_challenge(PNG, "x", now=T0, push=boom)
    assert "處理過" in hc.answer_from_line("驗證碼 1234", now=T0)


def test_batch_push_counts_once(monkeypatch):
    import line_sender
    posts = []
    monkeypatch.setattr(line_sender, "LINE_CHANNEL_TOKEN", "x")
    monkeypatch.setattr(line_sender, "_post", lambda url, payload: posts.append(payload) or True)
    bumps = []
    monkeypatch.setattr(line_sender, "_bump_quota", lambda: bumps.append(1))
    assert line_sender.push_batch_to_user_sync("U1", ["hi", {"type": "image"}])
    assert len(posts) == 1 and len(posts[0]["messages"]) == 2 and bumps == [1]
