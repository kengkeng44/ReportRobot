"""
解析使用者輸入文字，dispatch 到對應的查詢函式。
支援多種觸發：/2330、2330、查2330、AAPL、查AAPL、仁和持股、我的持股、持股 等。
"""

import re


_PORTFOLIO_KEYWORDS = {
    "仁和持股", "我的持股", "持股", "持倉", "我的股票",
    "portfolio", "Portfolio", "PORTFOLIO",
}

_HELP_KEYWORDS = {
    "help", "Help", "HELP", "說明", "指令", "幫助", "教學", "?", "？",
}

_COST_KEYWORDS = {
    "cost", "Cost", "COST", "用量", "費用", "成本", "stats",
}

# 個人指令（只在 1 對 1 chat 觸發）
_REMINDER_RE = re.compile(r"^(?:提醒|remind)\s*(.*)$", re.IGNORECASE)
_REMINDER_LIST_KEYWORDS = {"提醒", "remind", "提醒清單", "我的提醒"}
_REMINDER_CANCEL_RE = re.compile(r"^(?:取消提醒|cancel)\s+(\d+)$")
_TODO_RE = re.compile(r"^(?:待辦|todo)\s*(.*)$", re.IGNORECASE)
_TODO_LIST_KEYWORDS = {"待辦", "todo", "待辦清單"}
_PREVIEW_KEYWORDS = {"預覽", "preview", "Preview", "PREVIEW", "test", "預覽報告"}

# Admin-only（user 本人 LINE userId == ADMIN_LINE_USER_ID 才能用）
# Gmail 含財務 PII 一律歸這類；統一一個 /財務 指令包帳單 / 訂閱 / 扣款三類
_FINANCE_OVERVIEW_KEYWORDS = {
    "財務", "我的財務", "finance", "Finance", "FINANCE", "財務總覽",
    "帳單", "信用卡", "信用卡帳單", "對帳單",
    "訂閱", "訂閱費", "月費", "subscription", "subscriptions",
    "扣款", "自動扣繳", "繳費", "autopay",
}

PERSONAL_ONLY_MSG = (
    "🤫 這是私人指令，請在 1 對 1 chat 跟我說。\n"
    "在 LINE 找「鄭家大總管」單獨對話，不會打擾家庭群組。"
)
ADMIN_ONLY_MSG = (
    "🔒 這個指令會讀取本人 Gmail 財務資訊，只有專案擁有者本人能用。"
)

HELP_TEXT = (
    "🤖 喵管家指令清單\n"
    "\n"
    "📈 查股票（直接打代號）\n"
    "  • 台股：2330 / /2330 / 查2330\n"
    "  • 美股：AAPL / /aapl / 查TSLA\n"
    "  • ETF：00631L / 0050 / SPY / QQQ\n"
    "  • 中文公司名（要加 / 或 查）：/鼎天 / 查台積\n"
    "\n"
    "📊 比較兩檔績效（要加 /）\n"
    "  • /比較 0050 0056 1y\n"
    "  • /比較 加權 櫃買 ytd\n"
    "  • /SPY vs QQQ 3m\n"
    "  • /台積 跟 鴻海 比較 6m\n"
    "  區間：1m / 3m / 6m / 1y / ytd / 5y / max\n"
    "\n"
    "💼 查仁和持倉\n"
    "  • 仁和持股 / 我的持股 / 持股\n"
    "\n"
    "🤖 自由問答（要加 /）\n"
    "  • /Fed 最新利率動向       ← 預設精簡版（200-350 字）\n"
    "  • /詳細 黃金未來價格      ← 詳細版（IC Memo、bull/base/bear 6 塊）\n"
    "  • 詳細版觸發詞：詳細 / 完整 / 深入 / 深度 / detail / full\n"
    "  • 不認得的中文指令會丟給 AI 上網查\n"
    "\n"
    "💰 看 AI 用量與費用\n"
    "  • /cost 或 /用量 或 /費用\n"
    "\n"
    "📋 待辦清單（只在 1 對 1 chat 有效）\n"
    "  • /待辦 加 [內容]    新增\n"
    "  • /待辦              看清單\n"
    "  • /待辦 完成 [編號]  完成（自動移除）\n"
    "  • /待辦 刪 [編號]    刪除\n"
    "  • /待辦 清空         清掉全部\n"
    "  ℹ️ 未完成的會在每天 06/09/12/18 點自動提醒\n"
    "\n"
    "⏰ 提醒（只在 1 對 1 chat 有效）\n"
    "  • /提醒 30 分鐘後 喝水\n"
    "  • /提醒 2 小時後 開會\n"
    "  • /提醒 明天 9:30 會議\n"
    "  • /提醒 今天 18:00 倒垃圾\n"
    "  • /提醒              看所有進行中提醒\n"
    "  • /取消提醒 [編號]\n"
    "\n"
    "🧪 預覽明天的每日情報（只在 1 對 1 chat 有效）\n"
    "  • /預覽 或 /preview\n"
    "  ℹ️ 1-2 分鐘內 push 一份天氣 + 盤前給你（force 強跑、不發群組）\n"
    "\n"
    "💳 個人 Gmail 財務查詢（限本人 LINE 帳號）\n"
    "  • /財務   ← 信用卡帳單 + 訂閱月費 + 自動扣款，AI 整理金額分類\n"
    "  • 同義詞：/帳單 /訂閱 /扣款 都會觸發同一份財務總覽\n"
    "  ℹ️ 要設 ADMIN_LINE_USER_ID 環境變數，其他人用會被擋下\n"
    "\n"
    "🆘 顯示這個說明\n"
    "  • help / 說明 / ?\n"
    "\n"
    "📅 每天 06:00 自動推送\n"
    "  • 🌤️ 淡水區天氣 + 近期活動\n"
    "  • 📊 盤前報告（週末略過）\n"
    "\n"
    "ℹ️ 一般聊天不會被當指令，家人聊天不會被打擾。"
)


def _format_cost_stats(stats):
    """把 usage_tracker.get_stats() 的 dict 轉成 LINE 友善排版。"""
    lines = ["<b>💰 AI 用量統計</b>"]
    started = stats.get("tracking_since", "")[:16].replace("T", " ")
    now = stats.get("now", "")[:16].replace("T", " ")
    lines.append(f"統計區間：{started} ~ {now}")
    lines.append("")

    by_model = stats.get("by_model", {})
    if not by_model:
        lines.append("（尚無 AI 呼叫紀錄）")
    else:
        for model, data in by_model.items():
            short = model.replace("claude-", "").replace("-20251001", "")
            lines.append(f"🤖 {short}")
            lines.append(f"  呼叫 {data['calls']} 次")
            lines.append(
                f"  In {data['input_tokens']:,} / Out {data['output_tokens']:,} tokens"
            )
            lines.append(f"  約 ${data['estimated_cost_usd']:.4f}")
            lines.append("")

    if stats.get("web_search_calls"):
        lines.append(f"🔍 Web Search 共 {stats['web_search_calls']} 次")
        lines.append(f"  約 ${stats['web_search_cost_usd']:.4f}")
        lines.append("")

    lines.append(f"📊 累計：${stats.get('total_estimated_cost_usd', 0):.4f}")
    lines.append("")
    lines.append("ℹ️ 從上次 Railway 重啟到現在的累積，redeploy 會歸零。")
    return "\n".join(lines)

# 偵測前綴：開頭是 / 或「查」
_HAS_PREFIX_RE = re.compile(r"^\s*[/查]")
# 真正去掉前綴 + 內外空白
_STRIP_PREFIX_RE = re.compile(r"^\s*[/查]?\s*")

_TW_RE = re.compile(r"^(\d{4,6}[A-Z]?)$")               # 台股 4-6 位數字（可選一個英文）
_US_LOOSE_RE = re.compile(r"^([A-Za-z]{1,5})$")         # 帶前綴時：放寬大小寫
_US_STRICT_RE = re.compile(r"^([A-Z]{2,5})$")           # 不帶前綴：全大寫且 ≥ 2 字
                                                          # 避免 'hi'/'ok' 等日常字觸發
_CJK_RE = re.compile(r"[一-鿿]")                # 中日韓統一漢字


def _strip_prefix(text):
    if not text:
        return ""
    return _STRIP_PREFIX_RE.sub("", text).strip()


# 比較指令的 regex（支援多種寫法）
_COMPARE_PATTERNS = [
    re.compile(r"^比較\s*[:：]?\s*(\S+)\s+(\S+)(?:\s+(\S+))?$"),         # 比較 X Y [period]
    re.compile(r"^(\S+)\s+vs\s+(\S+)(?:\s+(\S+))?$", re.IGNORECASE),      # X vs Y [period]
    re.compile(r"^(\S+)\s*(?:跟|和|對)\s*(\S+)\s*比較?(?:\s+(\S+))?$"),    # X 跟 Y 比較 [period]
    re.compile(r"^(\S+)\s*(?:跟|和|對)\s*(\S+)\s+(\S+)\s*比較?$"),         # X 跟 Y period 比較
]


def _try_parse_compare(cleaned):
    """試 parse 比較指令；成功回 (sym1, sym2, period_or_None)。"""
    for pat in _COMPARE_PATTERNS:
        m = pat.match(cleaned)
        if m:
            groups = m.groups()
            sym1, sym2 = groups[0], groups[1]
            period = groups[2] if len(groups) >= 3 else None
            return (sym1, sym2, period)
    return None


def _find_tw_ticker_by_name(query):
    """從 twstock 對照表反查包含 query 的 ticker；多個 match 取最短代號（通常是主要的）。
    找不到時印 log 方便 debug typo（如永崴/永葳/永威），但不主動回應使用者。"""
    if not query or not _CJK_RE.search(query):
        return None
    try:
        import twstock
        candidates = [code for code, info in twstock.codes.items()
                      if info.name and query in info.name]
        if not candidates:
            print(f"中文反查無 match: {query!r}（可能是 typo 或 twstock 對照表沒收錄）")
            return None
        # 過濾掉超過 6 位的（權證、特殊金融商品代號通常 6 位以上）
        normal = [c for c in candidates if len(c) <= 6]
        pool = normal or candidates
        return min(pool, key=len)
    except Exception as e:
        print(f"twstock 中文名查詢失敗 ({query}): {e}")
        return None


def parse(text):
    """回 (kind, arg) 或 None。kind ∈ {'help', 'portfolio', 'stock', 'compare', 'free_query'}。"""
    if not text:
        return None
    has_prefix = bool(_HAS_PREFIX_RE.match(text))
    cleaned = _strip_prefix(text)
    if not cleaned:
        return None

    if cleaned in _HELP_KEYWORDS:
        return ("help", None)

    if cleaned in _COST_KEYWORDS:
        return ("cost", None)

    if cleaned in _PORTFOLIO_KEYWORDS:
        return ("portfolio", None)

    if cleaned in _PREVIEW_KEYWORDS:
        return ("preview", None)

    # 個人 Gmail 財務查詢（admin-only）— 帳單 / 訂閱 / 扣款都走同一個 overview
    if cleaned in _FINANCE_OVERVIEW_KEYWORDS:
        return ("finance_overview", None)

    # 個人指令：提醒
    if cleaned in _REMINDER_LIST_KEYWORDS:
        return ("reminder_list", None)
    m = _REMINDER_CANCEL_RE.match(cleaned)
    if m:
        return ("reminder_cancel", int(m.group(1)))
    m = _REMINDER_RE.match(cleaned)
    if m and m.group(1).strip():
        return ("reminder_add", m.group(1).strip())

    # 個人指令：待辦
    if cleaned in _TODO_LIST_KEYWORDS:
        return ("todo_list", None)
    m = _TODO_RE.match(cleaned)
    if m and m.group(1).strip():
        return ("todo", m.group(1).strip())

    # 比較指令（必須要前綴，避免「台積跟鴻海比較」之類聊天誤觸發）
    if has_prefix:
        compare = _try_parse_compare(cleaned)
        if compare:
            return ("compare", compare)

    if _TW_RE.match(cleaned):
        return ("stock", cleaned)

    # 美股：帶前綴接受任意大小寫；不帶前綴必須全大寫且 ≥ 2 字
    if has_prefix:
        m = _US_LOOSE_RE.match(cleaned)
        if m:
            return ("stock", cleaned.upper())
    else:
        m = _US_STRICT_RE.match(cleaned)
        if m:
            return ("stock", cleaned)

    # 中文公司名 → 反查 twstock 拿 ticker（例：/鼎天 → 3306、台積 → 2330）
    # 必須帶前綴 / 或 查 才接受，避免家人講「我有買台積」誤觸發
    if has_prefix and _CJK_RE.search(cleaned):
        ticker = _find_tw_ticker_by_name(cleaned)
        if ticker:
            return ("stock", ticker)
        # 既然有 / 前綴 + 中文 + 找不到對應股票/指令，就丟給 AI 自由發揮
        return ("free_query", cleaned)

    return None  # 不認得就靜默不回應，避免騷擾家人聊天


_PERSONAL_KINDS = {"reminder_add", "reminder_list", "reminder_cancel",
                   "todo", "todo_list", "preview"}

# Admin-only kinds（要 1 對 1 + LINE userId == ADMIN_LINE_USER_ID）
_ADMIN_KINDS = {"finance_overview"}


def _is_personal_chat(ctx):
    return bool(ctx) and ctx.get("source_type") == "user"


def _is_admin(ctx):
    """admin = 在 1 對 1 chat + user_id 對到 ADMIN_LINE_USER_ID。
    沒設 ADMIN_LINE_USER_ID 一律 deny（不要因 misconfig 變全開）。"""
    import os
    if not _is_personal_chat(ctx):
        return False
    admin_id = os.environ.get("ADMIN_LINE_USER_ID", "")
    if not admin_id:
        return False
    return ctx.get("user_id") == admin_id


def _handle_todo_subcmd(user_id, body):
    """處理 /待辦 加|完成|刪|清完成 子命令；用 regex 支援「加X」黏在一起。"""
    import personal
    body = body.strip()

    # 加 / 新增（中間可有可無空白）
    m = re.match(r"^(?:加|新增|add)\s*(.+)$", body, re.IGNORECASE)
    if m:
        item = m.group(1).strip()
        if not item:
            return "用法：/待辦 加 [內容]"
        tid = personal.add_todo(user_id, item)
        return f"✅ 已新增待辦 [{tid}] {item}"

    # 完成（= 直接刪除，不留勾在清單裡）
    m = re.match(r"^(?:完成|做完|done)\s*(\d+)$", body, re.IGNORECASE)
    if m:
        tid = int(m.group(1))
        if personal.delete_todo(user_id, tid):
            return f"✅ 待辦 [{tid}] 完成（已自動移除）"
        return f"找不到編號 {tid} 的待辦"

    # 刪
    m = re.match(r"^(?:刪|刪除|del|remove)\s*(\d+)$", body, re.IGNORECASE)
    if m:
        tid = int(m.group(1))
        if personal.delete_todo(user_id, tid):
            return f"🗑️ 待辦 [{tid}] 已刪除"
        return f"找不到編號 {tid} 的待辦"

    # 清空全部
    if body in ("清空", "全清", "clear", "clear all"):
        items = personal.list_todos(user_id)
        for t in items:
            personal.delete_todo(user_id, t["id"])
        return f"🧹 已清空 {len(items)} 筆待辦"

    # 不認得 → 列清單
    return personal.format_todos(user_id)


def _handle_preview(user_id):
    """/預覽：背景跑天氣 + 盤前 (force) 並 push 給該 user，立即 reply 確認訊息。
    避免在 reply 路徑內阻塞 30 秒以上把 replyToken 用爆。"""
    import threading
    from datetime import date

    def _bg():
        from line_sender import push_to_user_sync
        try:
            today = date.today().strftime("%Y-%m-%d")
            try:
                from weather import get_weather_report
                weather_msg, _ = get_weather_report()
                push_to_user_sync(
                    user_id,
                    f"<b>🧪 預覽 - 每日情報</b>  {today}\n\n"
                    f"<b>🌤️ 天氣報告</b>\n\n{weather_msg}",
                )
            except Exception as e:
                push_to_user_sync(user_id, f"⚠️ 預覽天氣失敗：{e}")

            try:
                from premarket import build_premarket_report
                pre = build_premarket_report(force=True)  # force=週末也產
                if pre:
                    push_to_user_sync(user_id, pre)
                else:
                    push_to_user_sync(user_id, "（盤前報告無內容）")
            except Exception as e:
                push_to_user_sync(user_id, f"⚠️ 預覽盤前失敗：{e}")
        except Exception as e:
            print(f"預覽背景執行失敗：{e}")

    threading.Thread(target=_bg, daemon=True).start()
    return ("⏳ 預覽生成中，1-2 分鐘內 push 給你（不會發到群組）。\n"
            "ℹ️ 即使週末也會強跑盤前段。")


def handle(text, ctx=None):
    """parse + dispatch；回字串（給 reply_message 直接送）或 None。
    ctx={'source_type': 'user'/'group'/'room', 'user_id': ..., 'group_id': ...}
    個人指令只在 source_type=='user' 才執行。"""
    parsed = parse(text)
    if not parsed:
        return None

    kind, arg = parsed

    # 權限檢查
    if kind in _ADMIN_KINDS and not _is_admin(ctx):
        return ADMIN_ONLY_MSG
    if kind in _PERSONAL_KINDS and not _is_personal_chat(ctx):
        return PERSONAL_ONLY_MSG

    try:
        if kind == "help":
            return HELP_TEXT

        if kind == "cost":
            from usage_tracker import get_stats
            return _format_cost_stats(get_stats())

        if kind == "portfolio":
            from gmail_reader import get_portfolio_from_gmail
            from portfolio import build_portfolio_summary
            portfolio = get_portfolio_from_gmail()
            summary = build_portfolio_summary(portfolio)
            return summary or "目前無持倉資料"

        if kind == "stock":
            from stock_news import get_stock_report
            return get_stock_report(arg)

        if kind == "compare":
            from compare import compare_returns
            return compare_returns(*arg)

        if kind == "free_query":
            from free_query import answer
            return answer(arg)

        if kind == "reminder_list":
            import personal
            return personal.format_reminders(ctx["user_id"])

        if kind == "reminder_cancel":
            import personal
            if personal.cancel_reminder(ctx["user_id"], arg):
                return f"🗑️ 提醒 [{arg}] 已取消"
            return f"找不到編號 {arg} 的提醒"

        if kind == "reminder_add":
            import personal
            from line_sender import push_to_user_sync
            parsed_time = personal.parse_reminder_input(arg)
            if not parsed_time:
                return ("無法解析時間，可用格式：\n"
                        "  /提醒 30 分鐘後 喝水\n"
                        "  /提醒 2 小時後 開會\n"
                        "  /提醒 明天 9:30 會議\n"
                        "  /提醒 今天 18:00 倒垃圾")
            fire_at, content = parsed_time
            rid = personal.add_reminder(
                ctx["user_id"], content, fire_at, push_to_user_sync,
            )
            if rid is None:
                return "⚠️ 排程系統未就緒，請稍後再試"
            return (f"✅ 已設定提醒 [{rid}]\n"
                    f"⏰ {fire_at.strftime('%Y-%m-%d %H:%M')}\n"
                    f"📝 {content}")

        if kind == "todo_list":
            import personal
            return personal.format_todos(ctx["user_id"])

        if kind == "todo":
            return _handle_todo_subcmd(ctx["user_id"], arg)

        if kind == "preview":
            return _handle_preview(ctx["user_id"])

        if kind == "finance_overview":
            import finance_query
            return finance_query.format_overview()
    except Exception as e:
        print(f"指令處理失敗 ({kind}/{arg})：{e}")
        import traceback; traceback.print_exc()
        try:
            from admin_notify import notify_admin
            notify_admin(e, {
                "module": "command_router",
                "section": str(kind),
                "extra": f"arg={str(arg)[:80]}",
            })
        except Exception:
            pass
        return f"查詢失敗：{e}"

    return None
