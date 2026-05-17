"""
即時警示模組。
- CWA 颱風警報（W-C0033-001，新一筆 typhoon advisory 就推卡片式 Flex）
- 重要 Gmail 即時轉發（特定寄件人，env GMAIL_FORWARD_FROM 逗號分隔）

機制：scheduler 每 5 分鐘呼叫 check_and_push_all()，逐項比對狀態，新的推送。
in-memory state，redeploy 後重置；首次跑只記錄不推（避免推一筆很久之前的）。
"""

import os

import http_utils


CWA_TYPHOON_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0033-001"


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


CWA_API_KEY = _env("CWA_API_KEY")

_LAST_TYPHOON_KEY = None  # 颱風警報唯一識別（typhoon name + advisory bulletin no.）
_GMAIL_FORWARD_SEEN = set()  # 已轉發過的 Gmail message id


def _push_to_group_and_admin(message, label):
    """message 可以是 str 或 Flex dict（line_sender 會自動辨識）。"""
    try:
        from line_sender import push_message_sync
        push_message_sync(message)
    except Exception as e:
        print(f"[alert/{label}] 群組 push 失敗：{e}")
    admin_id = os.environ.get("ADMIN_LINE_USER_ID", "")
    if admin_id:
        try:
            from line_sender import push_to_user_sync
            push_to_user_sync(admin_id, message)
        except Exception as e:
            print(f"[alert/{label}] admin push 失敗：{e}")


# ─────────────────────────────────────────────────────────
# 颱風警報（CWA W-C0033-001）
# ─────────────────────────────────────────────────────────

def check_typhoon():
    """抓 CWA 現有颱風警報。新一筆 advisory 就回 (key, flex_dict)；否則 None。"""
    global _LAST_TYPHOON_KEY
    if not CWA_API_KEY:
        return None

    try:
        r = http_utils.get(
            CWA_TYPHOON_URL,
            params={"Authorization": CWA_API_KEY},
            timeout=10,
        )
        records = r.json().get("records", {}) or {}
        info_list = records.get("tropicalCyclones", {}).get("tropicalCyclone") or []
        if not info_list:
            # 沒颱風 → reset state，下次有颱風時當「新警報」推
            if _LAST_TYPHOON_KEY is not None:
                print(f"[alert/typhoon] 無颱風警報，清除 state（前次 key={_LAST_TYPHOON_KEY}）")
            _LAST_TYPHOON_KEY = None
            return None

        # 取第一個颱風的最新一筆 analysis data
        ty = info_list[0]
        name_zh = ty.get("typhoonName") or ty.get("cwaTyphoonName") or "?"
        analysis_list = (ty.get("analysisData", {}) or {}).get("fix") or []
        if not analysis_list:
            return None
        latest = analysis_list[-1]
        bulletin_time = latest.get("fixTime", "?")
        key = f"{name_zh}|{bulletin_time}"

        # 第一次跑只記錄
        if _LAST_TYPHOON_KEY is None:
            _LAST_TYPHOON_KEY = key
            print(f"[alert/typhoon] 初始化 _LAST_TYPHOON_KEY={key}（不推送）")
            return None

        if key == _LAST_TYPHOON_KEY:
            return None
        _LAST_TYPHOON_KEY = key

        from flex_builder import typhoon_alert_flex
        flex = typhoon_alert_flex(
            name=name_zh,
            time=bulletin_time,
            location=latest.get("coordinate", "?"),
            pressure=latest.get("pressure", "?"),
            wind=latest.get("maxWindSpeed", "?"),
            gust=latest.get("maxGustSpeed", "?"),
            moving_dir=latest.get("movingDirection", "?"),
            moving=latest.get("movingSpeed", "?"),
        )
        return (key, flex)
    except Exception as e:
        print(f"[alert/typhoon] CWA 颱風抓取失敗：{e}")
        return None


# ─────────────────────────────────────────────────────────
# 重要 Gmail 即時轉發（特定寄件人）
# ─────────────────────────────────────────────────────────

def check_gmail_forward(max_per_run=5):
    """env var GMAIL_FORWARD_FROM (逗號分隔) 列出的寄件人，新信 push 給 admin。
    回 push 數（log 用）。"""
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

    # 只看過去 1 天 + 還沒推過的
    from_query = " OR ".join(f"from:{s}" for s in senders)
    query = f"newer_than:1d ({from_query})"

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


# ─────────────────────────────────────────────────────────
# 統一 entry：scheduler 每 5 分鐘呼叫
# ─────────────────────────────────────────────────────────

def check_and_push_all():
    """scheduler 入口：颱風 + Gmail 一次檢查。"""
    ty = check_typhoon()
    if ty:
        key, msg = ty
        print(f"[alert/typhoon] 推送颱風 {key}")
        _push_to_group_and_admin(msg, "typhoon")

    check_gmail_forward()


# 舊名相容（server.py 既有 import）
def check_and_push():
    check_and_push_all()
