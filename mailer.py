"""每日個人報走 Gmail SMTP 寄信。

為什麼不繼續用 LINE push：push 每月只有 200 則（line_quota.py），
群組版每天已經吃掉一則。自己一個人要看的東西不該再吃掉家人的份 ——
email 免費、沒有則數上限，而 LINE 的 reply 本來就免費，
想即時看照樣在 LINE 問「快過期」「最新消費」。

為什麼是應用程式密碼而不是 OAuth：現有 token.pickle 只有 gmail.readonly
（gmail_reader.SCOPES），要寄信得加 scope、重跑授權、換掉線上那顆 token。
財務同步、發票、Gmail 警示全都靠它，換壞了是連鎖故障。
多一個 env var 的爆炸半徑小得多。

設定：
- GMAIL_USER：寄件者（既有 env，對帳單那側也在用）
- GMAIL_APP_PASSWORD：Google 帳號 → 安全性 → 兩步驟驗證 → 應用程式密碼
- REPORT_EMAIL_TO：收件者，沒設就寄給自己
"""

import os
import smtplib
from email.message import EmailMessage
from html import escape

# 跟 LINE 共用同一份 strip 規則 —— 各寫一份遲早會漂移
from line_sender import _strip_html as strip_html

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
TIMEOUT = 20


def _app_password():
    """從 Google 複製過來長這樣：'abcd efgh ijkl mnop' —— 空白要去掉。"""
    return os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()


def sender():
    return os.environ.get("GMAIL_USER", "").strip()


def recipient():
    return os.environ.get("REPORT_EMAIL_TO", "").strip() or sender()


def is_configured():
    return bool(sender() and _app_password())


def to_html(text):
    """純文字 → HTML：整段 escape，再把 <b> 放回來，換行轉 <br>。

    報告內容本來就帶 <b>標題</b>（給 Flex 用的），email 這邊剛好直接
    當粗體，不用再解析一次格式。escape 在前是為了讓內容裡真的出現
    「<」（例如股價區間）不會變成壞掉的標籤。
    """
    out = escape(text or "")
    out = out.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    return out.replace("\n", "<br>")


def send_email(subject, body):
    """寄一封信，成功回 True。

    沒設定就回 False 而不是丟例外 —— 呼叫端（每日排程）當作沒這功能，
    不該因為少一個 env var 就讓整個 job 進 error listener。
    """
    if not is_configured():
        print("[mailer] 沒設 GMAIL_USER / GMAIL_APP_PASSWORD，跳過寄信")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender()
    msg["To"] = recipient()
    msg.set_content(strip_html(body))
    msg.add_alternative(
        f'<div style="font-family:sans-serif;line-height:1.7">{to_html(body)}</div>',
        subtype="html",
    )

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT) as smtp:
        smtp.login(sender(), _app_password())
        smtp.send_message(msg)
    print(f"[mailer] 已寄出：{subject} → {recipient()}")
    return True
