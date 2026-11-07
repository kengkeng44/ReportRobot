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

import finance_report
import sources as _sources   # 參數 sources 會遮住模組名稱
from parsers import bank_transfer, cathay_daily


# (Gmail 查詢, parser 模組)。之後新增發信人只要往這裡加一列。
SOURCES = [
    ('from:service@pxbillrc01.cathaybk.com.tw subject:"消費彙整通知"', cathay_daily),
    # 轉給別人的錢（2026-10-24 加）：方向＝轉帳，不算花費；繳卡費、轉給自己會被 parser 跳過
    # 富邦這個寄件人只寄行動銀行通知；不是轉帳成功的信 parser 會回空，不用靠主旨過濾
    ('from:mbank@dfm.taipeifubon.com.tw', bank_transfer.fubon),
    ('from:service@pxbillrc01.cathaybk.com.tw subject:"CUBE App轉帳通知"', bank_transfer.cathay_cube),
    # 郵局通知寄到另一個信箱，sync 會改用 _post_service() 讀
    ('from:mpost@mail.post.gov.tw subject:行動郵局交易通知', bank_transfer.post),
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


TRANSFER_SOURCES = [s for s in SOURCES
                    if s[1] in (bank_transfer.fubon, bank_transfer.cathay_cube, bank_transfer.post)]

# 寄到第二個信箱的寄件來源：用 POST_TOKEN_PICKLE_B64（setup_post_token.py 產生）讀
SECOND_INBOX_PARSERS = (bank_transfer.post,)


def _post_service():
    """第二個信箱（郵局通知寄到的那個）的 Gmail service。沒設定或授權壞掉回 None。"""
    import pickle
    b64 = os.environ.get("POST_TOKEN_PICKLE_B64", "").strip()
    try:
        if b64:
            creds = pickle.loads(base64.b64decode(b64))
        elif os.path.exists("token_post.pickle"):
            with open("token_post.pickle", "rb") as f:
                creds = pickle.load(f)
        else:
            return None
        if not creds.valid and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        from googleapiclient.discovery import build
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"[finance] 第二信箱授權失敗（POST_TOKEN_PICKLE_B64）：{e}")
        return None


_account_cache = {}


def _mail_url(svc, mid):
    """原信連結。u/0 是瀏覽器第一個登入的帳號，不一定是收信的那個；
    #inbox 遇到已封存的信會落回收件匣首頁。改用 authuser 指定帳號＋#all。"""
    key = id(svc)
    if key not in _account_cache:
        try:
            _account_cache[key] = svc.users().getProfile(userId="me").execute()["emailAddress"]
        except Exception:
            _account_cache[key] = None
    email = _account_cache[key]
    if email:
        return f"https://mail.google.com/mail/?authuser={email}#all/{mid}"
    return f"https://mail.google.com/mail/u/0/#all/{mid}"


def sync(service=None, lookback_days=DEFAULT_LOOKBACK_DAYS, notion=None, sources=None, max_results=50):
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

    if sources is not None and all(s in TRANSFER_SOURCES for s in sources):
        seen = notion.transactions_existing_fingerprints(limit=20000, source_names=_sources.TRANSFER_DISPLAY)
    else:
        seen = notion.transactions_existing_fingerprints()
    if seen is None:
        # 讀不到既有指紋就不要寫 —— 寧可這次不同步，也不要製造一堆重複
        print("[finance] 讀不到既有指紋，本次放棄以免寫入重複")
        return stats

    post_service = False   # 用到才建；None = 沒設定
    for query, parser in (sources or SOURCES):
        svc = service
        if parser in SECOND_INBOX_PARSERS:
            if post_service is False:
                post_service = _post_service()
            if post_service is None:
                continue
            svc = post_service
        stats["sources"] += 1
        full_query = f"{query} newer_than:{lookback_days}d"
        for mid, html in fetch_messages(svc, full_query, max_results=max_results):
            try:
                txns = parser.parse(html)
            except Exception as e:
                print(f"[finance] 解析失敗 {mid}：{e}")
                continue

            mail_url = _mail_url(svc, mid)
            for txn in txns:
                stats["parsed"] += 1
                if txn["fingerprint"] in seen:
                    stats["skipped"] += 1
                    continue
                txn["mail_url"] = mail_url
                # 共同消費的商店（全聯等）自動改記成共同並只留我那半。
                # 套在寫入前而不是 parser 裡：這是「錢怎麼分」的政策，
                # 不是「信件怎麼讀」的解析，混在一起之後兩邊都難改。
                txn = finance_report.apply_shared_rule(txn)
                txn = finance_report.apply_personal_rule(txn)
                if notion.transaction_add(txn):
                    seen.add(txn["fingerprint"])
                    stats["written"] += 1

    print(f"[finance] 同步完成：{stats}")
    return stats


def backfill_personal_morning(dry_run=True, service=None, client=None):
    """舊資料補套「平日上午全家 = 個人」。

    交易明細的日期欄沒存時間，所以靠原信連結回去重讀國泰通知信、用 Fingerprint
    對到同一筆拿授權時間。只動分攤類型還空著的（已經標共同的是人判斷過的，不蓋掉）。
    回 {checked, marked, no_time, rows: [(日期, 時間, 店, 金額)]}。
    """
    import notion_db
    client = client or notion_db._get_client()
    db_id = notion_db.get_or_create_db("交易明細")
    service = service or __import__("gmail_reader").get_gmail_service()
    filt = {"and": [
        {"property": "方向", "select": {"equals": "支出"}},
        {"property": "分攤類型", "select": {"is_empty": True}},
        {"property": "商店", "rich_text": {"contains": "全家"}},
        {"property": "原信連結", "url": {"is_not_empty": True}},
    ]}
    pages, cursor = [], None
    while True:
        kw = {"database_id": db_id, "filter": filt, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        resp = client.databases.query(**kw)
        pages += resp["results"]
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]

    stats = {"checked": len(pages), "marked": 0, "no_time": 0, "rows": []}
    parsed = {}   # mid → {fingerprint: txn}
    for p in pages:
        props = p["properties"]
        mid = (props["原信連結"].get("url") or "").rsplit("/", 1)[-1]
        fp = "".join(x.get("plain_text", "") for x in props["Fingerprint"].get("rich_text") or [])
        if mid not in parsed:
            try:
                msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
                parsed[mid] = {t["fingerprint"]: t for t in cathay_daily.parse(_html_of(msg.get("payload")))}
            except Exception as e:
                print(f"[backfill_personal] 讀信失敗 {mid}：{e}")
                parsed[mid] = {}
        txn = parsed[mid].get(fp)
        if not txn or not txn.get("time"):
            stats["no_time"] += 1
            continue
        if finance_report.apply_personal_rule(txn).get("split_type") != "個人":
            continue
        stats["marked"] += 1
        stats["rows"].append((txn["date"], txn["time"], txn["shop"], txn["amount"]))
        if not dry_run:
            client.pages.update(page_id=p["id"], properties={
                "分攤類型": {"select": {"name": "個人"}},
                "算個人": {"checkbox": True},
            })
    return stats


def backfill_personal_shops(dry_run=True, client=None):
    """舊資料補套「一律個人的店」。只動分攤類型還空著的支出。"""
    import notion_db
    client = client or notion_db._get_client()
    db_id = notion_db.get_or_create_db("交易明細")
    filt = {"and": [{"property": "方向", "select": {"equals": "支出"}},
                    {"property": "分攤類型", "select": {"is_empty": True}}]}
    stats = {"checked": 0, "marked": 0, "rows": []}
    cursor = None
    while True:
        kw = {"database_id": db_id, "filter": filt, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        resp = client.databases.query(**kw)
        for p in resp["results"]:
            stats["checked"] += 1
            props = p["properties"]
            shop = "".join(x.get("plain_text", "") for x in props["商店"].get("rich_text") or []) \
                or "".join(x.get("plain_text", "") for x in props["摘要"].get("title") or [])
            if not finance_report.is_personal_shop(shop):
                continue
            stats["marked"] += 1
            stats["rows"].append(((props["日期"].get("date") or {}).get("start"), shop,
                                  props["金額"].get("number")))
            if not dry_run:
                client.pages.update(page_id=p["id"], properties={
                    "分攤類型": {"select": {"name": "個人"}}, "算個人": {"checkbox": True}})
        if not resp.get("has_more"):
            return stats
        cursor = resp["next_cursor"]


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
