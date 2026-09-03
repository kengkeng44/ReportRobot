"""每日個人報寄信 —— 走 Gmail API (HTTPS)。

為什麼不是 SMTP：2026-08-25 06:01 首跑就炸 `OSError [Errno 101]`。
`/admin/net-check` 從容器內實測（commit a7a9fc8）：

    smtp.gmail.com:465  IPv4 → TimeoutError    IPv6 → Errno 101
    smtp.gmail.com:587  IPv4 → TimeoutError    IPv6 → Errno 101
    api.line.me:443     IPv4 → ok（控制組）

IPv4 是 timeout 而不是 refused，代表封包被防火牆靜默丟棄 ——
**Railway 擋 SMTP 對外埠**，不是 IPv6 沒路由。所以 smtplib 在這個
平台上怎麼寫都不會通：換埠、換 timeout、強制 IPv4 全部無效。
Gmail API 走 443，跟 LINE push 同一條路，而那條路每天都在用。

為什麼另開一顆 token 而不是共用：現有 `TOKEN_PICKLE_B64` 只有
gmail.readonly（gmail_reader.SCOPES），財務同步、發票、Gmail 警示
三個功能全靠它。要在那顆上面加 send scope 就得重跑授權、換掉線上
那顆，換壞了是三個功能一起倒。兩顆各管各的，爆炸半徑縮到一個。

設定：
- GMAIL_USER：寄件者（既有 env，對帳單那側也在用）
- SEND_TOKEN_PICKLE_B64：只有 gmail.send 的 token，跑 setup_send_token.py 產生
- REPORT_EMAIL_TO：收件者，沒設就寄給自己
"""

import base64
import os
import pickle
from email.message import EmailMessage
from html import escape

# 跟 LINE 共用同一份 strip 規則 —— 各寫一份遲早會漂移
from line_sender import _strip_html as strip_html

SEND_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
# 本機備援；雲端一律走 env。刻意跟 gmail_reader 的檔名分開。
SEND_TOKEN_FILE = "token_send.pickle"


def _send_token_b64():
    return os.environ.get("SEND_TOKEN_PICKLE_B64", "").strip()


def sender():
    return os.environ.get("GMAIL_USER", "").strip()


def recipient():
    return os.environ.get("REPORT_EMAIL_TO", "").strip() or sender()


def is_configured():
    """只檢查「有沒有」，不檢查「能不能用」。

    要真的驗授權得打一次 API，那是網路往返 —— 放在每日排程的
    gate 上等於每天多一次可能逾時的呼叫。授權壞掉的話 send 會
    丟例外，由呼叫端的 try 接住並進 admin 通知，該吵的時候會吵。
    """
    return bool(sender()) and bool(
        _send_token_b64() or os.path.exists(SEND_TOKEN_FILE)
    )


def _load_creds():
    """env 優先；沒有才退回本機檔案（本機測試用）。"""
    b64 = _send_token_b64()
    if b64:
        return pickle.loads(base64.b64decode(b64))
    with open(SEND_TOKEN_FILE, "rb") as f:
        return pickle.load(f)


def _service():
    """建 Gmail API client。抽成函式是為了讓測試能整個換掉。

    刻意不把 refresh 後的 creds 寫回檔案：Railway 檔案系統每次
    部署就重置，寫了也留不住；refresh token 本身不會過期，每次
    多一次 refresh 呼叫的成本遠低於維護一份寫不回去的快取。
    """
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = _load_creds()
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def to_html(text):
    """純文字 → HTML：整段 escape，再把 <b> 放回來，換行轉 <br>。

    報告內容本來就帶 <b>標題</b>（給 Flex 用的），email 這邊剛好直接
    當粗體，不用再解析一次格式。escape 在前是為了讓內容裡真的出現
    「<」（例如股價區間）不會變成壞掉的標籤。
    """
    out = escape(text or "")
    out = out.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    return out.replace("\n", "<br>")


def _attach_images(msg, images):
    """把圖片掛成 multipart/related 的一部分，讓 HTML 用 cid: 引用。

    掛在 html part 上而不是整封信上：掛在最外層會變成「附件」，
    Gmail 會在信末多出一排下載圖示，而不是在文中顯示。

    單張圖讀不到就跳過那張，不中斷整封信 —— 少一張圖遠好過信不見。
    """
    html_part = msg.get_body(("html",))
    if html_part is None:
        return
    for cid, path in (images or {}).items():
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            print(f"[mailer] 讀不到圖片 {path}：{e}，這張跳過")
            continue
        html_part.add_related(
            data, maintype="image", subtype="png", cid=f"<{cid}>",
        )


def _build_message(subject, body, html=None, images=None):
    """html 給了就原樣寄（digest.py 產的卡片版型已經是完整 HTML，
    再包一層 div 會把版面弄壞）；沒給就維持原本的簡易轉換。

    純文字版一律保留 —— 收信端不吃 HTML 時還有東西可看，而且純文字版
    看不到圖，所以圖裡的數字必須也出現在文字裡（見 spending_chart 的
    summary）。
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender()
    msg["To"] = recipient()
    msg.set_content(strip_html(body))
    msg.add_alternative(
        html or f'<div style="font-family:sans-serif;line-height:1.7">{to_html(body)}</div>',
        subtype="html",
    )
    if images:
        _attach_images(msg, images)
    return msg


def send_email(subject, body, html=None, images=None):
    """寄一封信，成功回 True。

    images 是 {cid: 檔案路徑}；HTML 裡用 <img src="cid:那個 cid"> 引用。
    不給就跟改動前完全一樣。

    沒設定就回 False 而不是丟例外 —— 呼叫端（每日排程）當作沒這功能，
    不該因為少一個 env var 就讓整個 job 進 error listener。
    """
    if not is_configured():
        print("[mailer] 沒設 GMAIL_USER / SEND_TOKEN_PICKLE_B64，跳過寄信")
        return False

    msg = _build_message(subject, body, html=html, images=images)
    # Gmail API 收的是 RFC822 全文的 urlsafe base64，不是 MIME 物件
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _service().users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"[mailer] 已寄出：{subject} → {recipient()}")
    return True
