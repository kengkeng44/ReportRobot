"""個人版每日報寄信 —— 走 Gmail API (HTTPS)，不是 SMTP。

2026-08-25 實測：Railway 把對外 SMTP 埠整個擋掉了（465 / 587 的 IPv4
都是 timeout，見 docs/HANDOFF.md 4.6），所以 smtplib 在這個平台上
永遠不會通。改用 Gmail API，它走 443 —— 跟 LINE push 同一條路，
而那條路每天都在用。

授權刻意用「另一顆只有 gmail.send 的獨立 token」：現有 token.pickle
只有 gmail.readonly，財務同步 / 發票 / Gmail 警示三個功能全靠它，
在那顆上面加 scope 得重跑授權換掉線上那顆，換壞了是連鎖故障。
兩顆各管各的，爆炸半徑從三個功能縮到一個。
"""

import base64
import email
import email.policy

import pytest

import mailer


def _decode(body):
    """把 Gmail API 的 {'raw': ...} 還原成 EmailMessage。"""
    raw = base64.urlsafe_b64decode(body["raw"])
    return email.message_from_bytes(raw, policy=email.policy.default)


@pytest.fixture
def gmail_spy(monkeypatch):
    """攔下 Gmail API service，把送出去的 body 收起來看。"""
    sent = []

    class _FakeRequest:
        def __init__(self, body):
            self.body = body

        def execute(self):
            sent.append(self.body)
            return {"id": "fake-message-id"}

    class _FakeMessages:
        def send(self, userId, body):
            assert userId == "me"
            return _FakeRequest(body)

    class _FakeUsers:
        def messages(self):
            return _FakeMessages()

    class _FakeService:
        def users(self):
            return _FakeUsers()

    monkeypatch.setattr(mailer, "_service", lambda: _FakeService())
    return sent


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.setenv("SEND_TOKEN_PICKLE_B64", "ZmFrZS10b2tlbg==")
    monkeypatch.delenv("REPORT_EMAIL_TO", raising=False)


# ── 設定 ──────────────────────────────────────────────────

def test_not_configured_without_send_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.delenv("SEND_TOKEN_PICKLE_B64", raising=False)
    monkeypatch.chdir(tmp_path)  # 確保本機也沒有 token_send.pickle

    assert not mailer.is_configured()


def test_configured_with_send_token(configured):
    assert mailer.is_configured()


def test_recipient_defaults_to_self(configured):
    assert mailer.recipient() == "me@gmail.com"


def test_recipient_can_be_overridden(configured, monkeypatch):
    monkeypatch.setenv("REPORT_EMAIL_TO", "other@example.com")

    assert mailer.recipient() == "other@example.com"


def test_send_email_skips_when_not_configured(monkeypatch, tmp_path, gmail_spy):
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.delenv("SEND_TOKEN_PICKLE_B64", raising=False)
    monkeypatch.chdir(tmp_path)

    assert mailer.send_email("x", "y") is False
    assert gmail_spy == []


# ── 信件內容 ──────────────────────────────────────────────

def test_send_email_delivers_both_plain_and_html(configured, gmail_spy):
    assert mailer.send_email("每日個人報", "<b>天氣</b>\n晴") is True

    msg = _decode(gmail_spy[0])
    assert msg["Subject"] == "每日個人報"
    assert msg["To"] == "me@gmail.com"

    plain = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    # 純文字版不該露出標籤，HTML 版要真的粗體
    assert "<b>" not in plain and "天氣" in plain
    assert "<b>天氣</b>" in html


def test_html_escapes_real_angle_brackets(configured):
    """內容裡真的出現「<」（股價區間之類）不能變成壞掉的標籤。"""
    out = mailer.to_html("跌破 <5%")

    assert "&lt;5%" in out


# ── 護欄 ──────────────────────────────────────────────────

def test_scope_is_send_only():
    """絕不能把 readonly 或 modify 加進來。

    多要一個 scope 就得重跑授權，而重跑授權的誘惑是「順便共用同一顆
    token」—— 那正是要避免的連鎖故障。
    """
    assert mailer.SEND_SCOPES == ["https://www.googleapis.com/auth/gmail.send"]


def test_readonly_token_alone_is_not_enough(monkeypatch, tmp_path):
    """只有 gmail_reader 那顆 readonly token 時，寄信必須視為「沒設定」。

    兩顆 token 一旦綁在一起，換掉其中一顆會讓兩邊一起倒 —— 這正是
    當初不共用的理由。順便確認 token.pickle 這個檔名也不被誤用。
    """
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.setenv("TOKEN_PICKLE_B64", "ZmFrZS1yZWFkb25seQ==")
    monkeypatch.delenv("SEND_TOKEN_PICKLE_B64", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "token.pickle").write_bytes(b"readonly")

    assert not mailer.is_configured()


def test_no_smtp_fallback():
    """不准留 SMTP 退路 —— Railway 擋埠，留著只會在半夜卡 timeout。

    2026-08-25 實測 465 / 587 IPv4 都是 timeout（封包被靜默丟棄）。
    「試 SMTP 失敗再走 API」這種寫法會讓每日報固定慢 20 秒起跳。
    """
    assert not hasattr(mailer, "smtplib")


# ── 卡片版型要能原樣寄出（2026-08-26）──────────────────────
# _build_message 原本一律 to_html(body) 再包一層 div，那會把 digest.py
# 產的完整 HTML 版型包壞。給了 html 就原樣用，沒給維持原行為。

def test_prebuilt_html_is_used_verbatim():
    msg = mailer._build_message("主旨", "純文字備援", html="<div id='card'>卡片</div>")

    payload = msg.get_payload()[1].get_payload(decode=True).decode()
    assert "<div id='card'>卡片</div>" in payload


def test_plain_text_alternative_still_present():
    """收信端不吃 HTML 時要有純文字版 —— 不能只寄 HTML。"""
    msg = mailer._build_message("主旨", "純文字備援", html="<div>卡片</div>")

    plain = msg.get_payload()[0].get_payload(decode=True).decode()
    assert "純文字備援" in plain


def test_without_html_behaviour_unchanged():
    """沒給 html 時維持原本行為，既有每日信不受影響。"""
    msg = mailer._build_message("主旨", "一般內容")

    payload = msg.get_payload()[1].get_payload(decode=True).decode()
    assert "一般內容" in payload


# ── 內嵌圖片（2026-09-01）──────────────────────────────────

def _png(tmp_path):
    """最小的合法 PNG（1x1 透明）。不用真的畫圖就能驗 MIME 結構。"""
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4"
        "nGNgAAIAAAUAAY27m/MAAAAASUVORK5CYII="
    )
    path = tmp_path / "pie.png"
    path.write_bytes(data)
    return str(path)


def test_no_images_keeps_the_original_structure(configured, gmail_spy):
    """不給 images 時行為必須跟改動前一模一樣。"""
    mailer.send_email("主旨", "內文")

    msg = _decode(gmail_spy[0])

    assert msg.get_body(("plain",)).get_content().strip() == "內文"
    assert msg.get_body(("html",)) is not None
    assert not list(msg.iter_attachments())


def test_image_is_embedded_with_content_id(configured, gmail_spy, tmp_path):
    mailer.send_email(
        "主旨", "內文",
        html='<div><img src="cid:spending"></div>',
        images={"spending": _png(tmp_path)},
    )

    msg = _decode(gmail_spy[0])
    cids = [p["Content-ID"] for p in msg.walk()
            if p.get_content_type() == "image/png"]

    assert cids == ["<spending>"]
    # walk() 掃的是整棵樹，圖掛錯層級（沒進 multipart/related，而是跟
    # text/html 平輩掛在 alternative 底下）上面那行照樣會綠，信到了
    # Gmail 卻變成信末的下載圖示而不是文中的圖。iter_attachments()
    # 是唯一能分辨這兩種形狀的斷言。
    assert not list(msg.iter_attachments())


def test_html_and_plain_survive_alongside_the_image(configured, gmail_spy, tmp_path):
    """加了圖不能把純文字版擠掉 —— 不吃 HTML 的收信端還要有東西看。"""
    mailer.send_email(
        "主旨", "本月合計 NT$1,000",
        html='<div><img src="cid:spending">本月合計 NT$1,000</div>',
        images={"spending": _png(tmp_path)},
    )

    msg = _decode(gmail_spy[0])

    assert "NT$1,000" in msg.get_body(("plain",)).get_content()
    assert "cid:spending" in msg.get_body(("html",)).get_content()


def test_missing_image_file_still_sends(configured, gmail_spy, tmp_path):
    """圖檔不見了要照寄 —— 少一張圖遠好過整封信不見。"""
    ok = mailer.send_email(
        "主旨", "內文",
        html='<div><img src="cid:spending"></div>',
        images={"spending": str(tmp_path / "not-there.png")},
    )

    assert ok is True
    assert len(gmail_spy) == 1


# ── 寄信重試（2026-09-04）───────────────────
def test_send_retries_on_transient_failure(configured, monkeypatch):
    """前兩次失敗第三次成功 —— 一次網路抖動不該讓整天的信不見。"""
    attempts = []

    class _FlakyRequest:
        def execute(self):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("temporary failure")
            return {"id": "ok"}

    class _M:
        def send(self, userId, body):
            return _FlakyRequest()

    class _U:
        def messages(self):
            return _M()

    class _S:
        def users(self):
            return _U()

    monkeypatch.setattr(mailer, "_service", lambda: _S())
    monkeypatch.setattr(mailer.time, "sleep", lambda s: None)

    assert mailer.send_email("主旨", "內文") is True
    assert len(attempts) == 3


def test_send_gives_up_after_max_attempts(configured, monkeypatch):
    """三次都失敗要丟例外 —— 呼叫端的 try 會把它送進 admin 通知。

    安靜地回 False 才是真的壞：信沒寄出去而且沒有人知道。
    """
    attempts = []

    class _DeadRequest:
        def execute(self):
            attempts.append(1)
            raise RuntimeError("gmail down")

    class _M:
        def send(self, userId, body):
            return _DeadRequest()

    class _U:
        def messages(self):
            return _M()

    class _S:
        def users(self):
            return _U()

    monkeypatch.setattr(mailer, "_service", lambda: _S())
    monkeypatch.setattr(mailer.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError):
        mailer.send_email("主旨", "內文")

    assert len(attempts) == mailer.SEND_ATTEMPTS
