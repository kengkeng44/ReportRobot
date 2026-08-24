"""個人版每日報改寄 Gmail，不再吃 LINE push 配額。

push 每月 200 則，群組版每天已經佔一則；個人版再佔一則等於一半配額
花在自己身上。email 免費，所以個人版整段搬過來。
"""

import pytest

import mailer


@pytest.fixture
def smtp_spy(monkeypatch):
    """攔下 SMTP_SSL，把寄出去的 EmailMessage 收起來看。"""
    sent = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host, self.port = host, port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            self.user, self.password = user, password

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _FakeSMTP)
    return sent


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.delenv("REPORT_EMAIL_TO", raising=False)


# ── 設定 ──────────────────────────────────────────────────

def test_not_configured_without_app_password(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)

    assert not mailer.is_configured()


def test_app_password_spaces_are_stripped(configured):
    """Google 給的是 'abcd efgh ijkl mnop'，直接貼上會登入失敗。"""
    assert mailer._app_password() == "abcdefghijklmnop"


def test_recipient_defaults_to_self(configured):
    assert mailer.recipient() == "me@gmail.com"


def test_recipient_can_be_overridden(configured, monkeypatch):
    monkeypatch.setenv("REPORT_EMAIL_TO", "other@example.com")

    assert mailer.recipient() == "other@example.com"


def test_send_email_skips_when_not_configured(monkeypatch, smtp_spy):
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)

    assert mailer.send_email("x", "y") is False
    assert smtp_spy == []


# ── 信件內容 ──────────────────────────────────────────────

def test_send_email_delivers_both_plain_and_html(configured, smtp_spy):
    assert mailer.send_email("每日個人報", "<b>天氣</b>\n晴") is True

    msg = smtp_spy[0]
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


# 每日報怎麼組段落、哪些情況不寄 —— 在 test_personal_report.py，
# 那邊是流程測試，這裡只管 mailer 這顆模組本身。
