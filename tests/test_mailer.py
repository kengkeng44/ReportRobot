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
