"""
個人 Gmail 財務查詢：自動化記帳。

權限：admin-only（command_router 比對 ADMIN_LINE_USER_ID）。

流程：
1. 用 Gmail search 撈本月 / 近 35 天的「交易類」email（嚴格排除廣告 / 登入通知 /
   職位推薦 / 安全性快訊 / 優惠推廣）
2. 撈完整 body（不是 metadata）給 AI 看
3. Sonnet 分類成 3 區（信用卡刷卡 / 訂閱 / 證券交易）+ 月度加總
4. 國泰 Cube 卡的「消費彙整通知」內文有列每筆消費，AI 直接抽

回傳格式：
- 信用卡刷卡：日期 / 商家 / 金額 / 是否定期
- 訂閱：服務 / 每 N 個月金額 / 下次續訂
- 證券：台幣/美金手續費總計
- 月度加總：本月實際扣款合計（NT$ + USD 分開）
"""

import os

from gmail_reader import _get_email_body, get_gmail_service


# ─────────────────────────────────────────────────────────
# Gmail search query：嚴格排除雜訊
# ─────────────────────────────────────────────────────────

# 主旨「應該要有」的關鍵字（含一個就保留）
_INCLUDE_SUBJECT = (
    "消費 OR 結帳 OR 帳單 OR 對帳單 OR 付款 OR 已付 OR 收據 OR 發票 "
    "OR receipt OR invoice OR billing OR payment OR charged "
    "OR 已扣款 OR 自動扣繳 OR 已成交 OR 交易明細"
)

# 寄件人 whitelist（銀行、券商、訂閱服務 — 含一個就保留）
_INCLUDE_FROM = (
    "cathay OR ctbc OR esun OR fubon OR taishin OR sinopac OR megabank "
    "OR 富邦 OR 國泰 OR 中信 OR 中國信託 OR 玉山 OR 台新 OR 永豐 OR 兆豐 "
    "OR fbs.com.tw "
    "OR netflix OR spotify OR apple OR adobe OR openai OR anthropic "
    "OR notion OR microsoft OR github OR aws OR amazon OR youtube "
    "OR figma OR zoom OR cursor"
)

# 主旨「絕對不要」的關鍵字（含一個就排除 — 這是 user 抱怨的雜訊類）
_EXCLUDE_SUBJECT = (
    "安全 OR 登入 OR alert OR security OR sign-in OR login "
    "OR 職位 OR 求職 OR 求才 OR job OR jobs OR career OR digest "
    "OR 推廣 OR 行銷 OR 優惠 OR promotion OR newsletter OR sale "
    "OR 推薦 OR 推送 OR 訂閱通知 "
    "OR 公告 OR 警示 OR 提醒 OR 通知您 OR 廣告"
)

# 寄件人 blacklist
_EXCLUDE_FROM = (
    "linkedin OR quora OR jobs OR career OR newsletter OR no-reply "
    "OR noreply OR 行銷 OR marketing OR ads"
)


def _build_query(days):
    return (
        f"newer_than:{days}d AND "
        f"(subject:({_INCLUDE_SUBJECT}) OR from:({_INCLUDE_FROM})) "
        f"AND -subject:({_EXCLUDE_SUBJECT}) "
        f"AND -from:({_EXCLUDE_FROM})"
    )


# ─────────────────────────────────────────────────────────
# 抓 email + body
# ─────────────────────────────────────────────────────────

def _fetch_finance_emails(days=35, max_results=40):
    try:
        service = get_gmail_service()
    except Exception as e:
        print(f"[finance] Gmail service 失敗：{e}")
        return []

    query = _build_query(days)
    try:
        results = service.users().messages().list(
            userId='me', q=query, maxResults=max_results,
        ).execute()
    except Exception as e:
        print(f"[finance] Gmail list 失敗：{e}")
        return []

    msgs = results.get('messages', [])
    print(f"[finance] query 命中 {len(msgs)} 封 (近 {days} 天)")

    out = []
    for msg in msgs:
        try:
            m = service.users().messages().get(
                userId='me', id=msg['id'], format='full',
            ).execute()
        except Exception as e:
            print(f"[finance] Gmail get 失敗：{e}")
            continue
        payload = m.get('payload', {})
        headers = {
            h['name'].lower(): h['value']
            for h in payload.get('headers', [])
        }
        body = _get_email_body(payload)
        out.append({
            'subject': headers.get('subject', '')[:120],
            'from': headers.get('from', '')[:80],
            'date': headers.get('date', ''),
            # 內文取前 2500 字（已含金額/日期欄位通常在表頭）給 AI
            'body': (body or '')[:2500],
        })
    return out


# ─────────────────────────────────────────────────────────
# 對外 entry：format_overview()
# ─────────────────────────────────────────────────────────

def _portfolio_brief():
    """精簡持股概況：台股 NTD 市值 + 美股 USD 市值 + 折算淨值。失敗回 None。"""
    try:
        from gmail_reader import get_portfolio_from_gmail
        from portfolio import get_live_price, _is_tw_ticker, _get_usd_twd
    except Exception as e:
        print(f"[finance] portfolio import 失敗：{e}")
        return None

    try:
        portfolio = get_portfolio_from_gmail()
    except Exception as e:
        print(f"[finance] 持股抓取失敗：{e}")
        return None

    if not portfolio:
        return None

    tw_cost = us_cost = tw_value = us_value = 0.0
    for ticker, p in portfolio.items():
        current = get_live_price(ticker)
        if current is None:
            continue
        cost = p["shares"] * p["avg_cost"]
        mv = p["shares"] * current
        # AU9901 黃金存摺也歸入台幣（user 提供 TWD 報價）
        is_tw_like = _is_tw_ticker(ticker) or (ticker or "").upper().startswith("AU")
        if is_tw_like:
            tw_cost += cost
            tw_value += mv
        else:
            us_cost += cost
            us_value += mv

    def _row(flag, label, c, v, prefix):
        pnl = v - c
        pct = pnl / c * 100 if c > 0 else 0
        sign = "+" if pnl >= 0 else ""
        return (
            f"  {flag} {label}｜成本 {prefix}{c:,.0f}"
            f"｜市值 {prefix}{v:,.0f}"
            f"｜{sign}{pct:.1f}% ({sign}{prefix}{pnl:,.0f})"
        )

    rate = _get_usd_twd()
    lines = ["<b>💼 持股概況</b>"]
    if tw_cost > 0:
        lines.append(_row("🇹🇼", "台股", tw_cost, tw_value, "NT$"))
    if us_cost > 0:
        lines.append(_row("🇺🇸", "美股", us_cost, us_value, "US$"))
    if rate and tw_value > 0 and us_value > 0:
        net = tw_value + us_value * rate
        total_cost = tw_cost + us_cost * rate
        total_pnl = net - total_cost
        total_pct = total_pnl / total_cost * 100 if total_cost > 0 else 0
        sign = "+" if total_pnl >= 0 else ""
        lines.append(
            f"  💎 淨值合計：NT$ {net:,.0f}"
            f"｜{sign}{total_pct:.1f}% ({sign}NT${total_pnl:,.0f})"
            f"｜USD/TWD={rate:.2f}"
        )
    return "\n".join(lines) if len(lines) > 1 else None


# ─────────────────────────────────────────────────────────
# Prompt 兩版：精簡（預設）vs 詳細（/財務詳細）
# ─────────────────────────────────────────────────────────

_FILTER_RULES = """═══════════════════════════════
過濾「實際付款」（非常重要）
═══════════════════════════════
**只**保留「真的有付錢」的紀錄。下列**不算付款**，全部丟掉：
- 任何「安全性快訊 / 登入通知 / 帳號活動」
- 任何「廣告 / 推廣 / 優惠 / 行銷 / 信用貸款推銷」
- 任何「職位推薦 / 求職通知 / LinkedIn 通知」
- 任何「OAuth 應用授權通知 / GitHub 通知」
- 任何只是「你做了交易」的 email 但沒實際金額
- 「房屋稅開徵推廣」等政府公告（除非已扣款）"""


def _build_prompt(today, month_str, raw, n_items, detailed):
    if detailed:
        return _build_prompt_detailed(today, month_str, raw, n_items)
    return _build_prompt_concise(today, month_str, raw, n_items)


def _build_prompt_concise(today, month_str, raw, n_items):
    """精簡版：只給信用卡分類加總 + ≥NT$3000 大筆。
    訂閱、手續費、逐筆細項全部不顯示。所有日期一律 YYYY-MM-DD。"""
    today_iso = today.strftime("%Y-%m-%d")
    return f"""你是個人財務記帳助理。今天是 {today_iso}（嚴格依此日期判斷「本月」）。

以下是使用者過去 35 天 Gmail「金融/付款相關」email 完整內容。

任務：

{_FILTER_RULES}

═══════════════════════════════
精簡輸出（純文字繁體中文，禁止 Markdown）
═══════════════════════════════

💳 信用卡分類（{month_str}）
  分類用：🍱 餐飲 / 🚗 交通 / 🛒 購物 / 🎬 娛樂 / 💊 醫療 / 💄 美妝
         / 🏠 居家 / 🛫 旅遊 / 📚 教育 / 💼 訂閱費 / ⚡ 其他
  每行格式（**只列分類總和，不列單筆**）：
    🍱 餐飲：NT$X,XXX (N 筆)
    🚗 交通：NT$X,XXX (N 筆)
    ...
  沒金額的分類整行省略；外幣消費另起一行 (US$XX.XX)。

⚡ 大筆消費（≥ NT$3,000）
  逐筆列出（**日期一律用 YYYY-MM-DD 格式**）：
  • YYYY-MM-DD｜商家名｜NT$X,XXX｜[分類 emoji]
  外幣 ≥ US$100 也列：
  • YYYY-MM-DD｜商家名｜US$XX.XX｜[分類 emoji]
  沒有就寫一行：「本月無 ≥ NT$3,000 單筆消費」

📊 信用卡支出小計：NT$X,XXX (+ US$XX.XX 如有外幣)

═══════════════════════════════
規則
═══════════════════════════════
- 純文字繁體中文，emoji 直接打、禁止 Markdown
- **所有日期一律 YYYY-MM-DD（例 2026-05-07），禁用 5/7、05/07、5月7日 等格式**
- 訂閱服務、證券手續費**不要顯示**（已折疊到詳細版）
- 不列每筆刷卡細節（除非 ≥ NT$3,000 的大筆）
- 找不到金額的整筆跳過，禁止編造
- 直接從「💳 信用卡分類」開始，禁止開場白與結語

═══════════════════════════════
原始 email 資料（{n_items} 封）：
═══════════════════════════════
{raw}
"""


def _build_prompt_detailed(today, month_str, raw, n_items):
    """詳細版：信用卡逐筆 + 訂閱 + 手續費（含 web 估算 fallback）+ 月加總 + 大筆。
    所有日期一律 YYYY-MM-DD。"""
    today_iso = today.strftime("%Y-%m-%d")
    return f"""你是個人財務記帳助理。今天是 {today_iso}（嚴格依此日期判斷「本月」）。

以下是使用者過去 35 天 Gmail「金融/付款相關」email 完整內容。

任務：

{_FILTER_RULES}

═══════════════════════════════
詳細輸出（純文字繁體中文，禁止 Markdown）
═══════════════════════════════

💳 信用卡刷卡（按分類分區，區內依日期由近到遠）
  分類：🍱 餐飲 / 🚗 交通 / 🛒 購物 / 🎬 娛樂 / 💊 醫療 / 💄 美妝
       / 🏠 居家 / 🛫 旅遊 / 📚 教育 / 💼 訂閱費 / ⚡ 其他
  每筆（日期用 YYYY-MM-DD）：• YYYY-MM-DD｜商家名｜NT$X,XXX 或 US$XX.XX｜[卡別末四碼]
  ≥ NT$3,000 或 ≥ US$100 → 行首加 ⚡

  國泰世華 Cube 卡「消費彙整通知」內文有逐筆消費明細，**全部抽出**。

🔁 訂閱服務（每月／每年定期）
  • 服務名｜每 N 月/年 NT$XXX 或 US$XX｜下次續訂 YYYY-MM-DD（若可推算）
  • 只列「明確有扣費」的，不列免費試用 / 通知

📈 證券交易手續費（富邦 / 國泰證券）
  從成交回報內文抓「手續費」「佣金」欄位加總；
  若內文沒列出明確金額，用富邦標準估算（並標明「(估)」）：
    - 台股：成交金額 × 0.1425% × 0.6（電子下單 6 折常見）
    - 美股複委託：成交金額 × 0.5%（最低 USD 25 / 筆）
  輸出兩行總計：
    台幣證券手續費合計：NT$XXX (含 N 筆估算)
    美金證券手續費合計：US$XX.XX (含 N 筆估算)

📊 {month_str} 已扣款合計
  💳 信用卡：NT$X,XXX (+ US$XX.XX 如有外幣消費)
  🔁 訂閱：NT$XXX + US$XX.XX
  📈 證券手續費：NT$XXX + US$XX.XX
  ━━━━━━━━━━
  本月總支出：NT$X,XXX + US$XX.XX

⚡ 大筆消費（≥ NT$3,000）
  • YYYY-MM-DD｜商家｜NT$X,XXX｜[類型]
  列所有 ≥ NT$3,000 的單筆（信用卡 / 訂閱 / 扣款都算）
  沒有就寫：「本月無 ≥ NT$3,000 單筆消費」

═══════════════════════════════
規則
═══════════════════════════════
- 純文字繁體中文、禁止 Markdown
- **所有日期一律 YYYY-MM-DD（例 2026-05-07），禁用 5/7、05/07、5月7日 等格式**
- 找不到金額的整筆跳過，禁止編造
- 月加總只算當月（{month_str}）已扣款的
- 沒任何一類資料時該段寫「（本月無）」一行
- 直接從「💳 信用卡刷卡」開始，禁止開場白與結語

═══════════════════════════════
原始 email 資料（{n_items} 封）：
═══════════════════════════════
{raw}
"""


def format_overview(detailed=False):
    """主指令 /財務（精簡）/ /財務詳細（詳細）。
    精簡：持股概況 + 信用卡分類總和 + 大筆消費（≥NT$3,000）
    詳細：持股概況 + 信用卡逐筆 + 訂閱 + 證券手續費 + 月加總 + 大筆"""
    portfolio_block = _portfolio_brief()
    items = _fetch_finance_emails(days=35)
    if not items:
        head = portfolio_block + "\n\n" if portfolio_block else ""
        return head + "📭 近 35 天沒找到財務相關交易信件"

    raw = "\n\n━━━━━━━━━━\n\n".join(
        f"[{i + 1}] FROM: {it['from']}\n"
        f"SUBJECT: {it['subject']}\n"
        f"DATE: {it['date']}\n"
        f"BODY:\n{it['body']}"
        for i, it in enumerate(items)
    )

    from datetime import date
    today = date.today()
    month_str = today.strftime("%Y/%m")

    prompt = _build_prompt(today, month_str, raw, len(items), detailed)

    try:
        import anthropic
        import usage_tracker
        from premarket import ANTHROPIC_API_KEY
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=3500,
            messages=[{"role": "user", "content": prompt}],
        )
        usage_tracker.track("claude-sonnet-4-5", msg)
        text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        text = text.strip()
        print(f"[finance] sonnet stop_reason={msg.stop_reason} chars={len(text)} "
              f"input={msg.usage.input_tokens} output={msg.usage.output_tokens}")
        if not text:
            return "📭 AI 無回應，可能本月確實沒交易"
        head = (portfolio_block + "\n\n") if portfolio_block else ""
        suffix = ""
        if not detailed:
            suffix = "\n\nℹ️ 想看每筆細項 / 訂閱 / 手續費請輸入：/財務詳細"
        return f"{head}{text}{suffix}"
    except Exception as e:
        print(f"[finance] AI 整理失敗：{e}")
        # AI 失敗 fallback：列原始 email 主旨（含持股概況頭）
        lines = []
        if portfolio_block:
            lines.append(portfolio_block)
            lines.append("")
        lines.append(f"<b>💳 財務 raw 列表（{len(items)} 封，AI 整理失敗）</b>")
        for it in items[:20]:
            lines.append(f"• {it['subject']}")
            lines.append(f"  └ {it['from']}")
        return "\n".join(lines)
