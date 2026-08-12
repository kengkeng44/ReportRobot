"""財務同步：Gmail → parser → 去重 → Notion。

排在台灣時間 15:30 —— 實測國泰「消費彙整通知」每天固定 14:2x–14:5x 送達
（彙整前一日授權），富邦證券成交回報盤後約 14:25。那時該到的信都到齊了。
**一天跑一次即足夠**，這些信一天只來一封，每小時跑有 23 次是空轉。

去重靠 parser 產出的 fingerprint：先把 Notion 既有指紋一次撈進記憶體再比對，
不對每筆各查一次（Notion 有 3 req/s 限流）。

失敗策略：任何一步失敗都只影響該封信 / 該筆交易，不讓整批中斷 ——
這個工作跟每日推播共用同一個 process，不能拖垮它。
"""

import base64
import os
from datetime import date as _date

from parsers import cathay_daily


# (Gmail 查詢, parser 模組)。之後新增發信人只要往這裡加一列。
SOURCES = [
    ('from:service@pxbillrc01.cathaybk.com.tw subject:"消費彙整通知"', cathay_daily),
]

DEFAULT_LOOKBACK_DAYS = 7


def _decode(data):
    if not data:
        return ""
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", "replace")


def _html_of(payload):
    """從 Gmail payload 遞迴找 text/html。找不到回空字串。"""
    if not payload:
        return ""
    if payload.get("mimeType") == "text/html":
        return _decode((payload.get("body") or {}).get("data"))
    for part in payload.get("parts") or []:
        html = _html_of(part)
        if html:
            return html
    return ""


def fetch_messages(service, query, max_results=50):
    """回 [(msg_id, html), ...]。單封失敗只略過該封。"""
    out = []
    try:
        listed = service.users().messages().list(
            userId="me", q=query, maxResults=max_results).execute()
    except Exception as e:
        print(f"[finance] 列信失敗 {query}：{e}")
        return out

    for meta in listed.get("messages") or []:
        mid = meta.get("id")
        try:
            msg = service.users().messages().get(
                userId="me", id=mid, format="full").execute()
            html = _html_of(msg.get("payload"))
            if html:
                out.append((mid, html))
        except Exception as e:
            print(f"[finance] 讀信失敗 {mid}：{e}")
    return out


def sync(service=None, lookback_days=DEFAULT_LOOKBACK_DAYS, notion=None):
    """主流程。回統計 dict {parsed, written, skipped, sources}。

    lookback 預設 7 天而非 1 天：排程漏跑或 Railway 重啟時能自動補回來，
    重複的部分會被指紋擋掉，多撈幾天沒有代價。
    """
    if notion is None:
        import notion_db as notion

    stats = {"parsed": 0, "written": 0, "skipped": 0, "sources": 0}

    if not notion.is_configured():
        print("[finance] Notion 未設定，跳過")
        return stats

    if service is None:
        # token 過期時 google-auth 會丟 RefreshError。這個工作跟每日推播
        # 共用同一個 process，不能讓它把整個排程炸掉。
        try:
            from gmail_reader import get_gmail_service
            service = get_gmail_service()
        except Exception as e:
            print(f"[finance] 取得 Gmail service 失敗（授權可能過期）：{e}")
            return stats
    if service is None:
        print("[finance] 無法取得 Gmail service，跳過")
        return stats

    seen = notion.transactions_existing_fingerprints()
    if seen is None:
        # 讀不到既有指紋就不要寫 —— 寧可這次不同步，也不要製造一堆重複
        print("[finance] 讀不到既有指紋，本次放棄以免寫入重複")
        return stats

    for query, parser in SOURCES:
        stats["sources"] += 1
        full_query = f"{query} newer_than:{lookback_days}d"
        for mid, html in fetch_messages(service, full_query):
            try:
                txns = parser.parse(html)
            except Exception as e:
                print(f"[finance] 解析失敗 {mid}：{e}")
                continue

            mail_url = f"https://mail.google.com/mail/u/0/#inbox/{mid}"
            for txn in txns:
                stats["parsed"] += 1
                if txn["fingerprint"] in seen:
                    stats["skipped"] += 1
                    continue
                txn["mail_url"] = mail_url
                if notion.transaction_add(txn):
                    seen.add(txn["fingerprint"])
                    stats["written"] += 1

    print(f"[finance] 同步完成：{stats}")
    return stats


def sync_portfolio(portfolio=None, notion=None, today=None):
    """把持倉與淨值快照寫進 Notion。回統計 dict。

    現金與信用卡未繳的資料源還沒接上（帳戶餘額要靠 PDF 對帳單、
    卡費要靠月帳單解析），所以目前的淨值等於股票市值。
    刻意不把它們當 0 硬算進去 —— 那會產生一個看起來精確但其實錯的數字。
    """
    if notion is None:
        import notion_db as notion

    stats = {"holdings": 0, "created": 0, "snapshot": False}

    if not notion.is_configured():
        return stats

    if portfolio is None:
        try:
            from gmail_reader import get_portfolio_from_gmail
            portfolio = get_portfolio_from_gmail()
        except Exception as e:
            print(f"[finance] 取得持倉失敗：{e}")
            return stats

    if not portfolio:
        print("[finance] 無持倉資料，跳過")
        return stats

    from portfolio import _compute_portfolio_data
    data = _compute_portfolio_data(portfolio)

    updated, created = notion.holdings_sync(data["rows"])
    stats["holdings"] = updated + created
    stats["created"] = created

    stock_value = data.get("net_value_ntd")
    if stock_value is None:
        # 美股有部位但匯率抓不到時會是 None。寧可不寫，也不要寫一個少算美股的淨值。
        print("[finance] 淨值無法換算（缺匯率），略過快照")
        return stats

    day = (today or _date.today()).isoformat()
    if notion.networth_upsert(day, stock=stock_value, net=stock_value):
        stats["snapshot"] = True

    print(f"[finance] 持倉同步完成：{stats}")
    return stats


def format_summary(stats):
    """給推播或 admin endpoint 用的一行摘要。"""
    if not stats.get("parsed"):
        return "💳 財務同步：沒有新交易"
    return (f"💳 財務同步：新增 {stats['written']} 筆"
            f"（解析 {stats['parsed']}、已存在 {stats['skipped']}）")
