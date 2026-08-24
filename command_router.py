"""
解析使用者輸入文字，dispatch 到對應的查詢函式。
支援多種觸發：/2330、2330、查2330、AAPL、查AAPL、仁和持股、我的持股、持股 等。
"""

import re
from datetime import date as _date


_PORTFOLIO_KEYWORDS = {
    "仁和持股", "我的持股", "持股", "持倉", "我的股票",
    "portfolio", "Portfolio", "PORTFOLIO",
}

_HELP_KEYWORDS = {
    "help", "Help", "HELP", "說明", "指令", "功能", "幫助", "教學", "?", "？",
}

_COST_KEYWORDS = {
    "cost", "Cost", "COST", "用量", "費用", "成本", "stats",
}
_LINE_QUOTA_KEYWORDS = {
    "額度", "line額度", "LINE額度", "line配額", "LINE配額", "quota", "Quota", "QUOTA",
}

# 投資分頁那兩顆按鈕。功能本來就寫好了（markets / premarket），
# 只是一直沒接上指令，所以按下去會掉進付費的 free_query。
_MARKET_KEYWORDS = {
    "大盤", "指數", "大盤指數", "market", "Market", "MARKET",
}

# 發票品項查詢。「買了什麼」要排在 _PANTRY_ADD_RE 前面，
# 不然會被「買了」的前綴吃掉變成入庫指令。
_EINVOICE_KEYWORDS = {
    "買了什麼", "品項", "發票", "明細", "消費明細", "發票明細",
}
_PREMARKET_KEYWORDS = {
    "盤前", "盤前報告", "premarket", "Premarket", "PREMARKET",
}

# 個人指令（只在 1 對 1 chat 觸發）
_REMINDER_RE = re.compile(r"^(?:提醒|remind)\s*(.*)$", re.IGNORECASE)
_REMINDER_LIST_KEYWORDS = {"提醒", "remind", "提醒清單", "我的提醒"}
_REMINDER_CANCEL_RE = re.compile(r"^(?:取消提醒|cancel)\s+(\d+)$")
_TODO_RE = re.compile(r"^(?:待辦|todo)\s*(.*)$", re.IGNORECASE)
_TODO_LIST_KEYWORDS = {"待辦", "todo", "待辦清單"}

# 煮飯模板。Rich Menu 的煮飯分頁會送出這些字串，一定要解析得到。
# 「買了 / 用掉」用行首錨定的 regex，不需要前綴 —— 家人講「我今天買了菜」
# 不是以「買了」開頭，所以不會誤觸發。
_PANTRY_LIST_KEYWORDS = {"庫存", "食材", "冰箱", "pantry"}
_PANTRY_EXPIRING_KEYWORDS = {"快過期", "過期", "即期", "expiring"}
_COOK_KEYWORDS = {"煮什麼", "吃什麼", "今天煮什麼", "cook"}
_SHOPPING_KEYWORDS = {"採購", "採購清單", "購物清單", "shopping"}
_PANTRY_ADD_RE = re.compile(r"^(?:買了|買|採買)\s*(.*)$")
_PANTRY_CONSUME_RE = re.compile(r"^(?:用掉|吃掉|用完)\s*(.*)$")

# 財務分頁。同樣用行首錨定，不要求前綴。
_SPENDING_KEYWORDS = {"本月支出", "這個月花多少", "月支出", "spending"}
_RECENT_KEYWORDS = {"最近交易", "最近消費", "近期交易", "recent"}
# 原本是每日推播的第五張卡，2026-08-16 拿掉改成要看時自己問。
# 跟 _RECENT_KEYWORDS 的差別：這個只看「資料裡最新的那一天」並附本月累計
# 與資料過舊警告，最近交易則是平鋪最近 10 筆。
_LATEST_DAY_KEYWORDS = {"最新消費", "最近一天消費", "昨天花多少", "今天花多少"}
_CARD_KEYWORDS = {"卡費", "信用卡帳單", "帳單", "card"}
_NETWORTH_KEYWORDS = {"淨值", "資產", "networth"}
_MANUAL_RE = re.compile(r"^(?:記一筆|記帳)\s*(.*)$")
_SHOPPING_ADD_RE = re.compile(r"^(?:要買|待買)\s*(.*)$")
_SHOPPING_BOUGHT_RE = re.compile(r"^(?:買好了|買到了)\s*(.*)$")
_PREVIEW_KEYWORDS = {"預覽", "preview", "Preview", "PREVIEW", "test", "預覽報告"}
_WHOAMI_KEYWORDS = {"我的id", "我的ID", "我的Id", "myid", "MyID", "MYID", "whoami", "我是誰"}

# Admin-only（user 本人 LINE userId == ADMIN_LINE_USER_ID 才能用）
# Gmail 含財務 PII 一律歸這類
_FINANCE_OVERVIEW_KEYWORDS = {
    "財務", "我的財務", "finance", "Finance", "FINANCE", "財務總覽",
    "帳單", "信用卡", "信用卡帳單", "對帳單",
    "訂閱", "訂閱費", "月費", "subscription", "subscriptions",
    "扣款", "自動扣繳", "繳費", "autopay",
}
_FINANCE_DETAIL_KEYWORDS = {
    "財務詳細", "詳細財務", "財務 詳細", "詳細 財務",
    "finance detail", "finance details", "finance full",
}

PERSONAL_ONLY_MSG = (
    "🤫 這是私人指令，請在 1 對 1 chat 跟我說。\n"
    "在 LINE 找「鄭家大總管」單獨對話，不會打擾家庭群組。"
)
ADMIN_ONLY_MSG = (
    "🔒 這個指令會讀取本人 Gmail 財務資訊，只有專案擁有者本人能用。"
)

HELP_TEXT = (
    "🏠 全能大管家指令清單\n"
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
    "  區間：1m / 3m / 6m / 1y / ytd / 5y / max（可省略）\n"
    "\n"
    "📊 大盤與盤前\n"
    "  • /大盤 或 /指數   ← 台股加權、美股、匯率等即時報價（免費）\n"
    "  • /盤前            ← 跟早上推播同一份盤前報告\n"
    "    ℹ️ 盤前含 AI 整理那段會計費，但同一天只算一次（早上跑過就重用）\n"
    "\n"
    "💼 查仁和持倉\n"
    "  • 仁和持股 / 我的持股 / 持股 / 持倉\n"
    "\n"
    "💳 財務（1 對 1 才能用）\n"
    "  • 本月支出 / 最近交易 / 卡費 / 淨值\n"
    "  • 最新消費   ← 最新一天的明細 + 本月累計（原本在每日推播，已改成用問的）\n"
    "  • 記一筆        ← 只打三個字會跳常記品項，再點金額，兩下記完\n"
    "  • 記一筆 午餐 120     ← 直接打也可以\n"
    "  • 記一筆 薪水 50000   ← 含薪水/獎金/退款會記成收入\n"
    "  信用卡消費每天 15:30 自動同步進 Notion\n"
    "\n"
    "🧾 買了什麼（具體品項）\n"
    "  • /買了什麼 或 /品項 或 /發票\n"
    "  ℹ️ 信用卡通知只有「商店名 + 金額」，買了哪些菜銀行收不到。\n"
    "     品項來自財政部手機條碼載具，只有結帳時掃了條碼的才查得到\n"
    "\n"
    "🍳 煮飯（1 對 1 才能用）\n"
    "  • 買了          ← 只打這兩個字會跳一排常買清單，點一下就入庫\n"
    "  • 買了 高麗菜1顆 番茄5顆   ← 一次加好幾樣還是打字快，沒寫數量當 1\n"
    "  • 庫存 / 快過期 / 煮什麼\n"
    "  • 用掉 高麗菜   ← 標成用完，並自動排進採購清單\n"
    "  分類、到期日、營養都會自動帶入（營養是粗估）\n"
    "\n"
    "🛒 採買清單（1 對 1 才能用）\n"
    "  • 要買 醬油     ← 加進採購清單\n"
    "  • 採購          ← 看目前要買什麼\n"
    "  • 買好了 醬油   ← 從清單移走（買回來的請用「買了」入庫存）\n"
    "\n"
    "🤖 自由問答（要加 /）\n"
    "  • /Fed 最新利率動向       ← 預設精簡版（200-350 字）\n"
    "  • /詳細 黃金未來價格      ← 詳細版（IC Memo、bull/base/bear 6 塊）\n"
    "  • 詳細版觸發詞：詳細 / 完整 / 深入 / 深度 / 詳盡 / 分析報告 / detail / full\n"
    "  • 不認得的中文指令會丟給 AI 上網查\n"
    "\n"
    "💰 看 AI 用量與費用\n"
    "  • /cost 或 /用量 或 /費用 或 /成本\n"
    "\n"
    "📊 LINE Push 月配額\n"
    "  • /額度 或 /quota\n"
    "  ℹ️ Free Plan 200 則/月；超 80%/90%/100% 自動 warn admin\n"
    "\n"
    "📋 待辦清單（只在 1 對 1 chat 有效）\n"
    "  • /待辦 加 [內容]    新增\n"
    "  • /待辦              看清單卡片（可直接按「完成」按鈕）\n"
    "  • /待辦 完成 [編號]  完成（自動移除）\n"
    "  • /待辦 刪 [編號]    刪除\n"
    "  • /待辦 清空         清掉全部\n"
    "  ℹ️ 不會自動 nag，請定期打 /待辦 看清單\n"
    "\n"
    "⏰ 提醒（只在 1 對 1 chat 有效，響一次就結束）\n"
    "  • /提醒 30 分鐘後 喝水\n"
    "  • /提醒 2 小時後 開會\n"
    "  • /提醒 明天 9:30 會議\n"
    "  • /提醒 今天 18:00 倒垃圾\n"
    "  • /提醒              看清單卡片（可直接按「延後 30 分」「取消」）\n"
    "  • /取消提醒 [編號]\n"
    "\n"
    "🧪 預覽每日情報（只在 1 對 1 chat 有效）\n"
    "  • /預覽 或 /preview\n"
    "  ℹ️ 1-2 分鐘內 push 一份天氣 + 盤前給你（force 強跑、不發群組）\n"
    "\n"
    "💳 個人 Gmail 財務查詢（限本人 LINE 帳號）\n"
    "  • /財務     ← 精簡版：持股概況 + 信用卡分類總和 + ⚡ 大筆消費(≥3000)\n"
    "  • /財務詳細 ← 詳細版：信用卡逐筆 + 訂閱 + 證券手續費 + 月加總\n"
    "  • 同義詞：/帳單 /訂閱 /扣款 都觸發精簡版\n"
    "  ℹ️ 要先設 ADMIN_LINE_USER_ID（用 /我的id 取得自己的 LINE userId）\n"
    "\n"
    "🆔 取得自己的 LINE userId（首次設定 admin 用）\n"
    "  • /我的id 或 /whoami\n"
    "  ℹ️ 把回傳的 U 開頭字串貼到 Infisical 的 ADMIN_LINE_USER_ID\n"
    "\n"
    "⚠️ 即時警示（自動推送，不用打指令；每 5 分鐘背景檢查）\n"
    "  • CWA 颱風警報新發布 → 群組 + admin（卡片式）\n"
    "  • 重要 Gmail（GMAIL_FORWARD_FROM 設的寄件人）→ admin\n"
    "\n"
    "🆘 顯示這個說明\n"
    "  • help / 說明 / 指令 / 功能 / 幫助 / 教學 / ?\n"
    "\n"
    "📅 每天早上自動推送（橫滑卡片，1 則，只有三張）\n"
    "  • 💫 今日一則（小知識或笑話 + 節日）\n"
    "  • 🌤️ 淡水區天氣 + 近期活動\n"
    "  • 📊 盤前報告（週末略過）\n"
    "  ℹ️ 食材提醒與消費卡片已拿掉，改成想看時自己問：\n"
    "     打「快過期」看食材（每樣附「已用掉」按鈕），打「最新消費」看花費\n"
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


# 選單的「比較」是預填鍵盤，使用者可能只補一檔就送出。那樣會漏到付費的
# free_query 買一段廢話，所以攔下來回用法就好。
_COMPARE_INCOMPLETE_RE = re.compile(r"^比較\s*[:：]?\s*(\S*)$")

COMPARE_USAGE = (
    "要比哪兩檔?這樣打:\n"
    "/比較 0050 0056 1y\n\n"
    "區間可省略,支援 1m / 3m / 6m / 1y / ytd / 5y / max。\n"
    "中文名也可以:/比較 台積電 鴻海"
)


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

    if cleaned in _LINE_QUOTA_KEYWORDS:
        return ("line_quota", None)

    # 要排在 _PANTRY_ADD_RE 前面：「買了什麼」會被「買了」的前綴吃掉
    if cleaned in _EINVOICE_KEYWORDS:
        return ("einvoice_items", None)

    # 要排在股票代號查詢前面：「大盤」「盤前」都是中文，會被中文反查吃掉
    if cleaned in _MARKET_KEYWORDS:
        return ("market", None)

    if cleaned in _PREMARKET_KEYWORDS:
        return ("premarket", None)

    if cleaned in _PORTFOLIO_KEYWORDS:
        return ("portfolio", None)

    if cleaned in _PREVIEW_KEYWORDS:
        return ("preview", None)

    if cleaned in _WHOAMI_KEYWORDS:
        return ("whoami", None)

    # 個人 Gmail 財務查詢（admin-only）— /財務詳細 優先比對（含「財務」字也算詳細）
    if cleaned in _FINANCE_DETAIL_KEYWORDS:
        return ("finance_overview_detail", None)
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

    # 個人指令：煮飯模板
    if cleaned in _PANTRY_LIST_KEYWORDS:
        return ("pantry_list", None)
    if cleaned in _PANTRY_EXPIRING_KEYWORDS:
        return ("pantry_expiring", None)
    if cleaned in _COOK_KEYWORDS:
        return ("cook_what", None)
    if cleaned in _SHOPPING_KEYWORDS:
        return ("shopping_list", None)
    m = _SHOPPING_ADD_RE.match(cleaned)
    if m:
        return ("shopping_add", m.group(1).strip() or None)
    m = _SHOPPING_BOUGHT_RE.match(cleaned)
    if m:
        return ("shopping_bought", m.group(1).strip() or None)
    m = _PANTRY_ADD_RE.match(cleaned)
    if m:
        # Rich Menu 的「買了」格子送出的是不帶品項的 /買了 → arg=None，回提示
        return ("pantry_add", m.group(1).strip() or None)
    m = _PANTRY_CONSUME_RE.match(cleaned)
    if m:
        return ("pantry_consume", m.group(1).strip() or None)

    # 個人指令：財務
    if cleaned in _SPENDING_KEYWORDS:
        return ("fin_spending", None)
    if cleaned in _RECENT_KEYWORDS:
        return ("fin_recent", None)
    if cleaned in _LATEST_DAY_KEYWORDS:
        return ("fin_latest_day", None)
    if cleaned in _CARD_KEYWORDS:
        return ("fin_card", None)
    if cleaned in _NETWORTH_KEYWORDS:
        return ("fin_networth", None)
    m = _MANUAL_RE.match(cleaned)
    if m:
        return ("fin_manual", m.group(1).strip() or None)

    # 比較指令（必須要前綴，避免「台積跟鴻海比較」之類聊天誤觸發）
    if has_prefix:
        compare = _try_parse_compare(cleaned)
        if compare:
            return ("compare", compare)
        # 只補了一檔（或什麼都沒補）就送出 —— 回用法，別漏到付費的 AI
        if _COMPARE_INCOMPLETE_RE.match(cleaned):
            return ("compare", None)

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
                   "todo", "todo_list", "preview", "whoami",
                   # 庫存與財務都是個人資料，不該在家人群組裡被查
                   "pantry_list", "pantry_expiring", "cook_what",
                   "pantry_add", "pantry_consume", "shopping_list",
                   "shopping_add", "shopping_bought",
                   "fin_spending", "fin_recent", "fin_card",
                   "fin_networth", "fin_manual", "fin_latest_day",
                   # 買了什麼菜是個人消費資料
                   "einvoice_items"}

# Admin-only kinds（要 1 對 1 + LINE userId == ADMIN_LINE_USER_ID）
_ADMIN_KINDS = {"finance_overview", "finance_overview_detail"}


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


_EINVOICE_SETUP_MSG = (
    "還沒接上財政部電子發票。\n\n"
    "信用卡的通知信只有「商店名 + 金額」,買了什麼銀行收不到,\n"
    "所以品項只能從財政部的手機條碼載具撈。\n\n"
    "要開通的話:\n"
    "1. 到 https://einvoice.nat.gov.tw/APCONSUMER/BTC605W/ 申請 AppID\n"
    "2. 把 AppID、手機條碼、手機條碼驗證碼設成環境變數\n"
    "   EINVOICE_APP_ID / EINVOICE_CARD_NO / EINVOICE_CARD_ENCRYPT\n\n"
    "⚠️ 只有結帳時出示手機條碼的發票才查得到。"
)


def _today():
    """抽出來讓測試可以固定日期。"""
    return _date.today()


def _handle_einvoice_items():
    """本月發票的品項明細。沒設定就講怎麼設定 —— 這功能的門檻是
    使用者要自己去申請 AppID,講不清楚等於沒做。"""
    import einvoice

    if not einvoice.is_configured():
        return _EINVOICE_SETUP_MSG

    today = _today()
    try:
        invoices = einvoice.fetch_month(today.year, today.month)
    except einvoice.EInvoiceError as e:
        # 這個訊息已經是人話（explain_code 翻過），直接給使用者
        return f"查發票失敗:{e}"
    except Exception as e:
        print(f"發票查詢失敗:{e}")
        return "查發票失敗,稍後再試。"

    return einvoice.format_purchases(invoices)


def _is_postback_owner(user_id):
    """postback 沒有 ctx，只拿得到按的人是誰，所以直接比對 ADMIN_LINE_USER_ID。

    沒設就一律放行 —— 這跟 _is_admin 的 deny-by-default 相反是刻意的：
    那邊擋的是 Gmail 財務 PII（外洩代價高），這邊只是把自家冰箱裡的
    菜標成用完，misconfig 時整個功能死掉的代價比誤按高。
    """
    import os
    admin_id = os.environ.get("ADMIN_LINE_USER_ID", "")
    return (not admin_id) or user_id == admin_id


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

    # 不認得 → 列清單卡片
    from flex_builder import todo_list_flex
    return todo_list_flex(personal.list_todos(user_id))


def _handle_preview(user_id):
    """/預覽：背景跑天氣 + 盤前 (force) 並 push 給該 user，立即 reply 確認訊息。
    避免在 reply 路徑內阻塞 30 秒以上把 replyToken 用爆。"""
    import threading
    from tz_utils import today_tpe

    def _bg():
        from line_sender import push_to_user_sync
        try:
            today = today_tpe().strftime("%Y-%m-%d")
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


_BUY_USAGE = (
    "要加什麼食材?這樣打:\n"
    "買了 高麗菜1顆 番茄5顆 雞胸肉2片\n\n"
    "沒寫數量就當 1。分類、到期日、營養會自動帶入。"
)

_CONSUME_USAGE = "要用掉哪一樣?這樣打:\n用掉 高麗菜"

# LINE 的 quick reply 一定要掛在一則訊息上,沒辦法只送按鈕不送字。
# 2026-08-19 依使用者要求砍到剩一行,畫面上幾乎只剩那排按鈕。
# 完整用法說明留在 _BUY_USAGE(Notion 掛掉、沒常買清單時才會跳)。
_BUY_QUICK_HINT = "要加什麼?"


def _buy_quick_reply():
    """「買了」不帶參數 → 常買清單按鈕。Notion 掛掉就退回原本的用法說明。

    在庫 + 用完都撈：只看在庫的話,常買但剛好吃完的會從清單上消失,
    而那正是最該出現在「要買什麼」按鈕列上的東西。
    """
    import kitchen
    import notion_db
    from flex_builder import quick_reply_text

    if not notion_db.is_configured():
        return _BUY_USAGE

    try:
        rows = list(notion_db.pantry_load()) + list(notion_db.pantry_load("用完"))
    except Exception as e:
        print(f"常買清單載入失敗:{e}")
        return _BUY_USAGE

    names = kitchen.frequent_items(rows)
    if not names:
        return _BUY_USAGE
    return quick_reply_text(_BUY_QUICK_HINT, [(n, f"買了 {n}") for n in names])


def _handle_kitchen(kind, arg):
    """煮飯模板的六個指令。Notion 掛掉時回可讀的訊息，不丟例外。"""
    import kitchen
    import notion_db

    if kind == "pantry_add":
        if not arg:
            return _buy_quick_reply()
        items, unknown = kitchen.parse_purchase(arg)
        today = _date.today()
        added = []
        for it in items:
            desc = kitchen.describe_item(it["name"], it["qty"], it["unit"])
            desc["bought"] = today
            desc["expiry"] = kitchen.estimate_expiry(
                today, desc["category"], desc["storage"])
            if notion_db.pantry_add(desc):
                added.append(desc)
        if items and not added:
            return "寫入 Notion 失敗,請稍後再試。"
        return kitchen.format_added(added, unknown)

    if kind == "pantry_consume":
        if not arg:
            return _CONSUME_USAGE
        rows = notion_db.pantry_load()
        hits = [r for r in rows if arg in r["name"]]
        if not hits:
            return f"庫存裡找不到「{arg}」。"
        if len(hits) > 1:
            names = "、".join(r["name"] for r in hits)
            return f"有多樣符合:{names}\n請打完整一點。"
        item = hits[0]
        notion_db.pantry_set_status(item["page_id"], "用完")
        # 用完就自動排進採購清單 —— 不然「用掉」跟「要再買」之間會斷掉
        notion_db.shopping_add(item["name"], category=item.get("category"),
                               source="低庫存自動")
        return f"✅ 已把「{item['name']}」標成用完,並加進採購清單。"

    if kind == "pantry_list":
        return kitchen.format_pantry(notion_db.pantry_load())

    if kind == "pantry_expiring":
        # 附「已用掉」按鈕的卡片版。這組按鈕原本掛在每日推播的食材提醒上,
        # 2026-08-16 那張卡拿掉了,搬來這裡 —— 要看時自己問,看到就能直接處理。
        pantry = notion_db.pantry_load()
        items, more = kitchen.expiring_actions(pantry)
        if items:
            from flex_builder import kitchen_reminder_bubble
            from tz_utils import today_tpe
            return kitchen_reminder_bubble(
                items, subtitle=today_tpe().strftime("%Y-%m-%d"), more_count=more)
        # 撈不到 page_id(或根本沒有快過期的)就退回純文字,提醒不會消失
        return kitchen.format_expiring(pantry)

    if kind == "cook_what":
        pantry = notion_db.pantry_load()
        recipes = notion_db.recipes_load(pantry)
        if not recipes:
            return ("食譜庫是空的,還沒辦法推薦。\n"
                    "先到 Notion 的「煮飯模板 → 食譜」加幾道常煮的菜。")
        return kitchen.format_recommendations(kitchen.recommend(pantry, recipes))

    if kind == "shopping_add":
        if not arg:
            return "要買什麼?這樣打:\n要買 醬油"
        added = []
        for it, _unknown in [kitchen.parse_purchase(arg)]:
            for x in it:
                if notion_db.shopping_add(x["name"],
                                          category=kitchen.guess_category(x["name"])):
                    added.append(x["name"])
        if not added:
            return "沒有解析到品項。試試「要買 醬油」。"
        return "🛒 已加進採購清單:" + "、".join(added)

    if kind == "shopping_bought":
        rows = notion_db.shopping_load()
        hits = [r for r in rows if arg and arg in r["name"]]
        if not hits:
            return f"採購清單裡找不到「{arg}」。"
        notion_db.shopping_mark_bought(hits[0]["page_id"])
        return f"✅ 已把「{hits[0]['name']}」標成買好了。"

    if kind == "shopping_list":
        return kitchen.format_shopping(notion_db.shopping_load())

    return None


_MANUAL_USAGE = (
    "要記什麼?這樣打:\n"
    "記一筆 午餐 120\n"
    "記一筆 薪水 50000\n\n"
    "金額一定要有。含「薪水、獎金、退款」等字會自動記成收入。"
)

_MANUAL_QUICK_HINT = (
    "要記什麼?點下面常記的,或直接打:\n"
    "記一筆 午餐 120\n\n"
    "含「薪水、獎金、退款」等字會記成收入。"
)


def _manual_item_quick_reply():
    """「記一筆」不帶參數 → 常記品項按鈕。Notion 掛掉就退回用法說明。"""
    import finance_report
    import notion_db
    from flex_builder import quick_reply_text

    if not notion_db.is_configured():
        return _MANUAL_USAGE

    try:
        txns = notion_db.transactions_load()
    except Exception as e:
        print(f"常記品項載入失敗:{e}")
        return _MANUAL_USAGE

    names = finance_report.frequent_expense_items(txns)
    if not names:
        return _MANUAL_USAGE
    return quick_reply_text(_MANUAL_QUICK_HINT,
                            [(n, f"記一筆 {n}") for n in names])


def _manual_amount_quick_reply(item):
    """「記一筆 午餐」→ 常用金額按鈕。

    沒有可用金額(使用者自己打的品項且無歷史)就只回文字提示 ——
    空的 quickReply 物件會被 LINE 當格式錯誤,整則訊息退回。
    """
    import finance_report
    import notion_db
    from flex_builder import quick_reply_text

    hint = f"{item} 多少錢?點下面的,或直接打:\n記一筆 {item} 95"

    if not notion_db.is_configured():
        return hint

    try:
        txns = notion_db.transactions_load()
    except Exception as e:
        print(f"常用金額載入失敗:{e}")
        return hint

    amounts = finance_report.frequent_amounts(txns, item)
    if not amounts:
        return hint
    return quick_reply_text(hint,
                            [(str(a), f"記一筆 {item} {a}") for a in amounts])


def _handle_finance(kind, arg):
    """財務分頁的五個功能。Notion 掛掉時回可讀訊息，不丟例外。"""
    import finance_report
    import notion_db

    if kind == "fin_manual":
        if not arg:
            return _manual_item_quick_reply()
        txn = finance_report.parse_manual(arg)
        if not txn:
            # 有品項沒金額 —— 這是兩段式的第二段，不是錯誤
            return _manual_amount_quick_reply(arg.strip())
        if not notion_db.transaction_add(txn):
            return "寫入 Notion 失敗,請稍後再試。"
        sign = "+" if txn["direction"] == "收入" else "-"
        return (f"✅ 已記錄:{txn['shop']}　{sign}NT${txn['amount']:,}"
                f"（{txn['category']}）")

    if kind == "fin_spending":
        from tz_utils import today_tpe
        month = today_tpe().strftime("%Y-%m")
        return finance_report.format_monthly_spending(
            notion_db.transactions_load(), month)

    if kind == "fin_recent":
        return finance_report.format_recent(notion_db.transactions_load())

    if kind == "fin_latest_day":
        from tz_utils import today_tpe
        text = finance_report.format_latest_day_spending(
            notion_db.transactions_load(), today_tpe())
        return text or "還沒有任何消費紀錄。"

    if kind == "fin_card":
        return finance_report.format_card_bills(notion_db.card_statements_load())

    if kind == "fin_networth":
        return finance_report.format_net_worth(notion_db.networth_load())

    return None


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

        if kind == "line_quota":
            import line_quota
            return line_quota.format_stats()

        if kind == "einvoice_items":
            return _handle_einvoice_items()

        if kind == "market":
            import markets
            return markets.build_market_summary()

        if kind == "premarket":
            import premarket
            # force=True：排程週末會 skip，但使用者主動按就是想看，
            # 回一句「週末沒有」對按鈕來說是最沒用的回答。
            # AI 那段有當日快取，早上推播跑過就不會重複付費。
            return (premarket.build_premarket_report(force=True)
                    or "盤前資料暫時取不到，等一下再試。")

        if kind in ("pantry_list", "pantry_expiring", "cook_what",
                    "pantry_add", "pantry_consume", "shopping_list",
                    "shopping_add", "shopping_bought"):
            return _handle_kitchen(kind, arg)

        if kind in ("fin_spending", "fin_recent", "fin_card",
                    "fin_networth", "fin_manual", "fin_latest_day"):
            return _handle_finance(kind, arg)

        if kind == "portfolio":
            from gmail_reader import get_portfolio_from_gmail
            from portfolio import build_portfolio_flex, build_portfolio_summary
            portfolio = get_portfolio_from_gmail()
            flex = build_portfolio_flex(portfolio)
            if flex:
                return flex
            # fallback 文字版（理論上空持倉才會走到這）
            return build_portfolio_summary(portfolio) or "目前無持倉資料"

        if kind == "stock":
            from stock_news import get_stock_report
            from flex_builder import stock_report_carousel
            text = get_stock_report(arg)
            carousel = stock_report_carousel(text)
            return carousel or text  # 解析失敗 fallback 純文字

        if kind == "compare":
            if not arg:
                return COMPARE_USAGE
            from compare import compare_returns
            return compare_returns(*arg)

        if kind == "free_query":
            from free_query import answer
            return answer(arg)

        if kind == "reminder_list":
            import personal
            from flex_builder import reminder_list_flex
            return reminder_list_flex(personal.list_reminders(ctx["user_id"]))

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
            from flex_builder import todo_list_flex
            return todo_list_flex(personal.list_todos(ctx["user_id"]))

        if kind == "todo":
            return _handle_todo_subcmd(ctx["user_id"], arg)

        if kind == "preview":
            return _handle_preview(ctx["user_id"])

        if kind == "finance_overview":
            import finance_query
            return finance_query.format_overview(detailed=False)

        if kind == "finance_overview_detail":
            import finance_query
            return finance_query.format_overview(detailed=True)

        if kind == "whoami":
            uid = ctx.get("user_id") or "(unknown)"
            return (
                "🆔 你的 LINE userId：\n\n"
                f"{uid}\n\n"
                "把這串複製貼到 Infisical / Railway 的 ADMIN_LINE_USER_ID 環境變數，"
                "之後 /財務 等管理員指令就會認得你。\n"
                "（其他人即使知道這串也用不出去，是 LINE 平台內部識別碼。）"
            )

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


# ════════════════════════════════════════
# Postback handler（按 Flex 按鈕觸發的 webhook 事件）
# ════════════════════════════════════════

def handle_postback(data, user_id):
    """處理 Flex 卡片按鈕的 postback。
    data: 'action=todo_complete&id=5' urlencoded
    user_id: 來自 event.source.userId（信任 LINE 平台，不從 data 取）
    回 str | dict | list，給 reply_message 直接送。"""
    from urllib.parse import parse_qs

    if not data or not user_id:
        return None

    parsed = parse_qs(data)
    action = (parsed.get("action") or [""])[0]
    if not action:
        return None

    def _int_param(key, default=0):
        try:
            return int((parsed.get(key) or [str(default)])[0])
        except (ValueError, TypeError):
            return default

    try:
        if action == "todo_complete":
            tid = _int_param("id")
            import personal
            from flex_builder import todo_list_flex
            ok = personal.delete_todo(user_id, tid)
            new_list = todo_list_flex(personal.list_todos(user_id))
            if ok:
                return [f"✅ 已完成 [{tid}]", new_list]
            return f"找不到編號 {tid} 的待辦（可能已完成）"

        if action == "reminder_cancel":
            rid = _int_param("id")
            import personal
            from flex_builder import reminder_list_flex
            ok = personal.cancel_reminder(user_id, rid)
            new_list = reminder_list_flex(personal.list_reminders(user_id))
            if ok:
                return [f"🗑️ 已取消提醒 [{rid}]", new_list]
            return f"找不到編號 {rid} 的提醒"

        if action == "pantry_used":
            pid = (parsed.get("pid") or [""])[0]
            name = (parsed.get("n") or [""])[0] or "那樣食材"
            # 每日情報是推到家人群組的，這顆按鈕誰都按得到。庫存在
            # _PERSONAL_KINDS 裡是個人資料，寫入更該只認本人。
            if not _is_postback_owner(user_id):
                return "庫存只有本人能改，這顆按鈕對你沒有作用。"
            if not pid:
                return (f"這張卡片定位不到「{name}」在 Notion 的位置。\n"
                        f"改打「用掉 {name}」就可以。")
            import notion_db
            row = next((r for r in notion_db.pantry_load()
                        if r.get("page_id") == pid), None)
            if row is None:
                # 推播卡片會一直留在聊天室，隔天再按到是常態不是錯誤，
                # 講清楚就好，別再寫一次 Notion 也別重複加採購清單
                return f"「{name}」已經標成用完了，不用再按一次。"
            if not notion_db.pantry_set_status(pid, "用完"):
                return f"「{name}」寫入 Notion 失敗，請稍後再試。"
            # 用完就自動排進採購清單 —— 跟打「用掉」走同一條路
            notion_db.shopping_add(row["name"], category=row.get("category"),
                                   source="低庫存自動")
            return f"✅ 已把「{row['name']}」標成用完，並加進採購清單。"

        if action == "reminder_snooze":
            from datetime import datetime, timedelta
            rid = _int_param("id")
            mins = _int_param("min", 30)
            import personal
            from line_sender import push_to_user_sync
            from flex_builder import reminder_list_flex
            # 找原提醒、取消、用同樣文字 reschedule
            items = personal.list_reminders(user_id)
            target = next((t for t in items if t["id"] == rid), None)
            if not target:
                return f"找不到編號 {rid} 的提醒"
            personal.cancel_reminder(user_id, rid)
            new_fire = datetime.now() + timedelta(minutes=mins)
            new_id = personal.add_reminder(
                user_id, target["text"], new_fire, push_to_user_sync,
            )
            if new_id is None:
                return "⚠️ 排程系統未就緒，請稍後再試"
            confirm = (
                f"⏳ 已延後 {mins} 分至 {new_fire.strftime('%H:%M')}\n"
                f"📝 {target['text']}"
            )
            new_list = reminder_list_flex(personal.list_reminders(user_id))
            return [confirm, new_list]

    except Exception as e:
        print(f"Postback 處理失敗 (action={action})：{e}")
        import traceback; traceback.print_exc()
        try:
            from admin_notify import notify_admin
            notify_admin(e, {
                "module": "command_router",
                "section": f"postback/{action}",
                "extra": f"data={data[:80]}",
            })
        except Exception:
            pass
        return f"操作失敗：{e}"

    print(f"未知的 postback action：{action}")
    return None
