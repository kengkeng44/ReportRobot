"""
個人 Gmail 財務查詢：信用卡帳單、訂閱、扣款通知。

權限：只有 ADMIN_LINE_USER_ID（user 本人 LINE userId）能觸發；
其他人即使 1 對 1 跟 bot 對話也擋下，因為 Gmail 內容是私人。
"""

import os

from gmail_reader import get_gmail_service


def _query_emails(query, max_results=30):
    """通用：query Gmail 回 list of {subject, from, date, snippet}。
    用 metadata format 抓 headers，不下載完整 body 省成本與 PII 風險。"""
    try:
        service = get_gmail_service()
    except Exception as e:
        print(f"[finance] Gmail service 失敗：{e}")
        return []

    try:
        results = service.users().messages().list(
            userId='me', q=query, maxResults=max_results,
        ).execute()
    except Exception as e:
        print(f"[finance] Gmail list 失敗 (q={query[:80]})：{e}")
        return []

    out = []
    for msg in results.get('messages', []):
        try:
            m = service.users().messages().get(
                userId='me', id=msg['id'],
                format='metadata',
                metadataHeaders=['Subject', 'From', 'Date'],
            ).execute()
        except Exception as e:
            print(f"[finance] Gmail get 失敗：{e}")
            continue
        headers = {
            h['name'].lower(): h['value']
            for h in m.get('payload', {}).get('headers', [])
        }
        out.append({
            'subject': headers.get('subject', '')[:90],
            'from': headers.get('from', '')[:60],
            'date': headers.get('date', ''),
            'snippet': m.get('snippet', '')[:140],
        })
    return out


# ─────────────────────────────────────────────────────────
# 三類查詢
# ─────────────────────────────────────────────────────────

def get_bills(days=35):
    """信用卡帳單 / 銀行對帳單。台灣常見銀行寄件人 + 主旨關鍵字。"""
    q = (
        f'newer_than:{days}d AND ('
        f'subject:(信用卡 OR 帳單 OR 對帳單 OR 應繳 OR 帳款 OR statement OR billing OR 結帳) '
        f'OR from:(cathay OR ctbc OR esun OR fubon OR taishin OR sinopac OR megabank '
        f'OR 富邦 OR 國泰 OR 中信 OR 中國信託 OR 玉山 OR 台新 OR 永豐 OR 兆豐 OR 北富銀)'
        f')'
    )
    return _query_emails(q)


def get_subscriptions(days=60):
    """訂閱 / 月費 / 年費 receipt。"""
    q = (
        f'newer_than:{days}d AND ('
        f'subject:(subscription OR receipt OR 訂閱 OR 收據 OR 發票 '
        f'OR auto-renew OR renewal OR 續訂 OR 續約 OR 自動續訂) '
        f'OR from:(netflix OR spotify OR apple OR google OR adobe '
        f'OR openai OR anthropic OR notion OR microsoft OR github '
        f'OR youtube OR disney OR linkedin OR figma OR zoom)'
        f')'
    )
    return _query_emails(q)


def get_autopay(days=35):
    """自動扣款 / 電信 / 水電瓦斯 / 房租。"""
    q = (
        f'newer_than:{days}d AND '
        f'subject:(扣款 OR 自動扣繳 OR 已扣款 OR 已收款 OR direct\\ debit OR payment OR 繳費 OR 已繳)'
    )
    return _query_emails(q)


# ─────────────────────────────────────────────────────────
# 格式化（直接列）
# ─────────────────────────────────────────────────────────

def _format_list(items, title, empty_msg, limit=15):
    if not items:
        return f"📭 {empty_msg}"
    lines = [f"<b>📬 {title}（{len(items)} 筆）</b>"]
    for it in items[:limit]:
        lines.append(f"• {it['subject']}")
        lines.append(f"  └ {it['from']}")
    if len(items) > limit:
        lines.append(f"…還有 {len(items) - limit} 筆未列出")
    return "\n".join(lines)


def format_bills():
    return _format_list(
        get_bills(), "信用卡帳單 / 對帳單（近 35 天）",
        "近 35 天沒找到帳單信件",
    )


def format_subscriptions():
    return _format_list(
        get_subscriptions(), "訂閱 / 月費收據（近 60 天）",
        "近 60 天沒找到訂閱相關信件",
    )


def format_autopay():
    return _format_list(
        get_autopay(), "自動扣款 / 繳費通知（近 35 天）",
        "近 35 天沒找到扣款通知",
    )


# ─────────────────────────────────────────────────────────
# AI 整理：把三類合一給 Haiku 摘要成「總覽」
# ─────────────────────────────────────────────────────────

def format_overview():
    """三類合一 + AI 摘要。Haiku 看 subject + from + snippet 抽金額/分類。"""
    bills = get_bills(days=35)
    subs = get_subscriptions(days=60)
    auto = get_autopay(days=35)

    if not (bills or subs or auto):
        return "📭 近期沒找到財務相關信件（帳單 / 訂閱 / 扣款）"

    # 直接列前若干筆給 AI 看
    raw = []
    for label, items in [("帳單", bills), ("訂閱", subs), ("扣款", auto)]:
        for it in items[:20]:
            raw.append(f"[{label}] {it['from']} | {it['subject']} | {it['snippet']}")
    raw_text = "\n".join(raw)

    prompt = (
        "以下是使用者過去 35-60 天的財務相關 email metadata（含寄件人、主旨、預覽摘要）。\n"
        "請整理成 3 段：信用卡帳單、訂閱服務、自動扣款；每段列 5-10 條要點，"
        "盡量從 subject / snippet 抓出金額（NT$ / NTD / TWD / USD / $）、到期日、銀行/服務名。\n"
        "格式：純文字繁體中文、不要 Markdown、每點 1 行。\n"
        "找不到金額就只寫服務名 + 通知類型。禁止編造金額。\n\n"
        "資料：\n" + raw_text + "\n\n"
        "輸出："
    )

    try:
        import anthropic
        import usage_tracker
        from premarket import ANTHROPIC_API_KEY
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        usage_tracker.track("claude-haiku-4-5-20251001", msg)
        text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                text = block.text
        return f"<b>💳 財務總覽</b>\n\n{text.strip()}"
    except Exception as e:
        print(f"[finance] AI 摘要失敗 fallback list：{e}")
        # AI 失敗 fallback：list 三類
        return "\n\n".join([format_bills(), format_subscriptions(), format_autopay()])
