"""
重要 Gmail 轉發（特定寄件人，env GMAIL_FORWARD_FROM 逗號分隔）。

機制：jobs.py 每個時段呼叫 check_gmail_forward(since=上一班開始時間)，
只轉發兩班之間進來的信。不靠記憶體記「轉發過哪些」—— 定時任務每次都是新的 process。

颱風警報 2026-11-13 拿掉（使用者：查不到也不重要）。
"""

import os


_GMAIL_FORWARD_SEEN = set()  # 同一個 process 裡已轉發過的 Gmail message id（手動重跑時防重複）


# ─────────────────────────────────────────────────────────
# 重要 Gmail 即時轉發（特定寄件人）
# ─────────────────────────────────────────────────────────

def check_gmail_forward(since=None, max_per_run=5):
    """env var GMAIL_FORWARD_FROM (逗號分隔) 列出的寄件人，新信 push 給 admin。
    since（含時區 datetime）：只看這之後收到的信；沒給就看過去 1 天。回 push 數（log 用）。"""
    forward_from = os.environ.get("GMAIL_FORWARD_FROM", "").strip()
    admin_id = os.environ.get("ADMIN_LINE_USER_ID", "")
    if not forward_from or not admin_id:
        return 0

    senders = [s.strip() for s in forward_from.split(",") if s.strip()]
    if not senders:
        return 0

    try:
        from gmail_reader import get_gmail_service
        service = get_gmail_service()
    except Exception as e:
        print(f"[alert/gmail] Gmail service 失敗：{e}")
        return 0

    from_query = " OR ".join(f"from:{s}" for s in senders)
    window = f"after:{int(since.timestamp())}" if since else "newer_than:1d"
    query = f"{window} ({from_query})"

    try:
        res = service.users().messages().list(
            userId='me', q=query, maxResults=10,
        ).execute()
    except Exception as e:
        print(f"[alert/gmail] list 失敗：{e}")
        return 0

    msgs = res.get("messages", []) or []
    if not msgs:
        return 0

    pushed = 0
    for msg in msgs:
        mid = msg.get("id")
        if mid in _GMAIL_FORWARD_SEEN:
            continue
        try:
            m = service.users().messages().get(
                userId='me', id=mid,
                format='metadata',
                metadataHeaders=['Subject', 'From', 'Date'],
            ).execute()
        except Exception as e:
            print(f"[alert/gmail] get 失敗：{e}")
            continue
        headers = {
            h['name'].lower(): h['value']
            for h in m.get('payload', {}).get('headers', [])
        }
        snippet = (m.get('snippet', '') or '')[:200]
        text = (
            f"📨 Gmail 重要信件\n"
            f"From：{headers.get('from', '?')[:80]}\n"
            f"日期：{headers.get('date', '?')}\n"
            f"主旨：{headers.get('subject', '?')[:120]}\n\n"
            f"{snippet}"
        )
        try:
            from line_sender import push_to_user_sync
            push_to_user_sync(admin_id, text)
            _GMAIL_FORWARD_SEEN.add(mid)
            pushed += 1
            if pushed >= max_per_run:
                break
        except Exception as e:
            print(f"[alert/gmail] push 失敗 mid={mid[:8]}：{e}")

    if pushed:
        print(f"[alert/gmail] 推送 {pushed} 封新信")
    return pushed
