"""說明文字（HELP_TEXT）與實際功能的一致性。

說明是使用者唯一看得到的「這台機器人會什麼」清單，寫錯比沒寫還糟：
寫了程式不支援的指令 → 使用者打了沒反應；程式支援卻沒寫 → 等於沒做。
這裡把兩邊釘在一起，任何一邊改了另一邊沒跟上就會紅。
"""

import re

import pytest

import command_router as cr


# ── /功能 別名 ────────────────────────────────────────────

@pytest.mark.parametrize("text", ["/功能", "功能", "查功能"])
def test_gongneng_triggers_help(text):
    """使用者要求打 /功能 也能叫出說明。
    parse() 會先用 _STRIP_PREFIX_RE 剝掉開頭的 / 或「查」，三種寫法都得通。"""
    assert cr.parse(text) == ("help", None)


def test_gongneng_handle_returns_help_text():
    assert cr.handle("/功能") == cr.HELP_TEXT


@pytest.mark.parametrize("kw", sorted(cr._HELP_KEYWORDS))
def test_every_help_keyword_parses(kw):
    """_HELP_KEYWORDS 裡的每個字，加不加 / 都要叫得出說明。"""
    assert cr.parse(kw) == ("help", None)
    assert cr.parse("/" + kw) == ("help", None)


def test_help_lists_its_own_trigger_words():
    """說明裡列的觸發詞，必須真的在 _HELP_KEYWORDS 裡。"""
    for kw in ("help", "說明", "指令", "功能", "幫助", "教學", "?"):
        assert kw in cr._HELP_KEYWORDS
        assert kw in cr.HELP_TEXT, f"說明沒列出觸發詞 {kw}"


# ── 說明裡寫的指令，程式都要接得住 ──────────────────────────

# 說明文字裡出現過的指令範例。每一條都必須 parse 得到指定的 kind，
# 不然就是說明寫了程式不支援的東西。
DOCUMENTED = [
    ("2330", "stock"),
    ("/2330", "stock"),
    ("AAPL", "stock"),
    ("/比較 0050 0056 1y", "compare"),
    ("仁和持股", "portfolio"),
    ("持股", "portfolio"),
    ("/本月支出", "fin_spending"),
    ("/最近交易", "fin_recent"),
    ("/卡費", "fin_card"),
    ("/淨值", "fin_networth"),
    ("記一筆 午餐 120", "fin_manual"),
    ("買了 高麗菜1顆", "pantry_add"),
    ("/庫存", "pantry_list"),
    ("/快過期", "pantry_expiring"),
    ("/煮什麼", "cook_what"),
    ("用掉 高麗菜", "pantry_consume"),
    ("/採購", "shopping_list"),
    ("要買 醬油", "shopping_add"),
    ("買好了 醬油", "shopping_bought"),
    ("/cost", "cost"),
    ("/用量", "cost"),
    ("/費用", "cost"),
    ("/額度", "line_quota"),
    ("/quota", "line_quota"),
    ("/待辦", "todo_list"),
    ("/待辦 加 買醬油", "todo"),
    ("/提醒", "reminder_list"),
    ("/提醒 30 分鐘後 喝水", "reminder_add"),
    ("/取消提醒 3", "reminder_cancel"),
    ("/預覽", "preview"),
    ("/preview", "preview"),
    ("/財務", "finance_overview"),
    ("/財務詳細", "finance_overview_detail"),
    ("/帳單", "finance_overview"),
    ("/訂閱", "finance_overview"),
    ("/扣款", "finance_overview"),
    ("/我的id", "whoami"),
    ("/whoami", "whoami"),
    ("/help", "help"),
]


@pytest.mark.parametrize("text,kind", DOCUMENTED)
def test_documented_commands_parse(text, kind):
    parsed = cr.parse(text)
    assert parsed is not None, f"說明寫了 {text!r} 但 parse 不認得"
    assert parsed[0] == kind, f"{text!r} 解析成 {parsed[0]}，說明卻寫成 {kind}"


@pytest.mark.parametrize("text,_kind", DOCUMENTED)
def test_documented_commands_appear_in_help(text, _kind):
    """反向檢查：上面這張表是從說明抄下來的，指令字樣要真的在說明裡。"""
    token = text.lstrip("/").split()[0]
    assert token in cr.HELP_TEXT, f"{token} 不在說明裡，表跟說明脫節了"


# ── 採買清單：既有說明漏掉的一整組指令 ─────────────────────

def test_help_documents_shopping_list_commands():
    """「要買 / 買好了」是採買清單的入口，說明漏了等於沒做。"""
    assert "要買" in cr.HELP_TEXT
    assert "買好了" in cr.HELP_TEXT
    assert "採購" in cr.HELP_TEXT


def test_help_mentions_consume_auto_adds_to_shopping():
    """「用掉」會順手把東西排進採購清單，這是使用者看不到的副作用，要寫出來。"""
    kitchen_section = cr.HELP_TEXT.split("🍳")[1].split("🤖")[0]
    assert "用掉" in kitchen_section and "採購清單" in kitchen_section


# ── 每日推播 ──────────────────────────────────────────────

def test_help_lists_every_daily_bubble():
    """daily_report_carousel 會出的五種 bubble，說明都要提到。"""
    for label in ("今日一則", "食材提醒", "天氣", "盤前", "消費"):
        assert label in cr.HELP_TEXT, f"每日推播漏寫 {label}"


def test_help_says_conditional_bubbles_are_conditional():
    """食材與消費 bubble 只在有資料時出現，沒寫的話使用者會以為壞了。"""
    daily = cr.HELP_TEXT.split("📅")[1]
    assert "快過期" in daily
    assert "有消費紀錄" in daily or "有紀錄" in daily


def test_help_does_not_hardcode_push_clock_time():
    """推播時間可用 DAILY_CRON 覆蓋，寫死幾點幾分過幾個月就又錯了。
    （程式預設 0 22 * * * UTC = 台北 06:00，但說明只寫「早上」。）"""
    daily = cr.HELP_TEXT.split("📅")[1]
    assert not re.search(r"\d{1,2}:\d{2}", daily), "每日推播段落不要寫死時刻"
    assert "早上" in daily


def test_help_explains_why_spending_date_is_not_yesterday():
    """消費 bubble 的日期通常是前天 —— 國泰彙整信每天寄前一日的明細。
    不解釋的話使用者會以為同步壞了（見 daily-spending-bubble spec 第 2 節）。"""
    daily = cr.HELP_TEXT.split("📅")[1]
    assert "前天" in daily
    assert "彙整" in daily


# ── 名稱與長度 ────────────────────────────────────────────

def test_help_uses_current_bot_name():
    """專案已改名「全能大管家」（見 setup_richmenu.MENUS），舊名不該留在說明裡。"""
    assert "全能大管家" in cr.HELP_TEXT
    assert "喵管家" not in cr.HELP_TEXT


def test_help_fits_in_one_line_message():
    """LINE 單則文字訊息上限 5000 字元，超過整則會被拒收（說明就變成打了沒反應）。"""
    assert len(cr.HELP_TEXT) <= 5000, f"HELP_TEXT {len(cr.HELP_TEXT)} 字元，超過 LINE 上限"


def test_help_does_not_document_unsupported_commands():
    """說明裡每個 /指令 樣式的 token 都要 parse 得到（自由問答的例句除外）。"""
    ignore = {"詳細", "Fed"}  # 自由問答段落的示範句，本來就是丟給 AI 的
    # 只認行首或空白後面緊接的 /xxx，才不會把「80%/90%」的百分比切成指令
    for token in re.findall(r"(?:^|[\s（(])/([一-鿿\w]+)", cr.HELP_TEXT, re.M):
        if token in ignore:
            continue
        assert cr.parse("/" + token) is not None, f"說明寫了 /{token} 但程式不認得"
