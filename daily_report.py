"""
每日報告：天氣 + 盤前 包成 Flex Carousel 1 則 push。

設計：先 gather 兩段純文字（不直接推），再組成 carousel push 1 次。
任一段失敗就在對應 bubble 顯示降級文案；兩段都失敗才 fallback 純文字。
比過去切 2-3 chunk 多次 push 節省 30-60 則配額/月。

推兩個目標，內容刻意不同，但只有群組吃 push 配額（1 則/天，約 30 則/月）：
- 群組（LINE_GROUP_ID）：今日一則 + 天氣（淡水、金山）+ 盤前 —— LINE push
- 本人：食材 + 天氣（板橋）+ 最新消費 —— 2026-08-20 改寄 Gmail（見 mailer.py）
  財務只走個人版，不進群組。
"""

import os
import traceback

import humor
from admin_notify import notify_admin
from flex_builder import daily_report_carousel
from line_sender import push_message
from premarket import build_premarket_report
from stock_news import get_cnyes_news
from tz_utils import today_tpe
from weather import PERSONAL_WEATHER_LOCATIONS, get_weather_report


def _safe(label, body_fn):
    """執行 body_fn() 拿字串；失敗時通知 admin 並回 None。"""
    try:
        return body_fn()
    except Exception as e:
        print(f"[{label}] 失敗：{e}")
        traceback.print_exc()
        notify_admin(e, {"module": "daily_report", "section": label})
        return None


def _fetch_market_news(limit=3):
    """抓鉅亨台股市場新聞數則標題，組成一段文字；無則回 None。"""
    items = get_cnyes_news("台股", limit=limit)
    lines = [f"• {it['title']}" for it in items[:limit] if it.get("title")]
    return "\n".join(lines) if lines else None


# 2026-08-16 使用者要求把「食材提醒」與「最近一天消費」都從**群組**推播拿掉，
# 群組只留三張：今日一則 / 天氣 / 盤前。要看時自己問：
#   食材提醒 → LINE 打「快過期」（command_router 的 pantry_expiring，含「已用掉」按鈕）
#   最近消費 → LINE 打「最新消費」（command_router 的 fin_latest_day）
#
# 2026-08-19 兩者都回到「個人版」推播（下面 _push_personal_report）——
# 群組不該看到別人的冰箱和帳單，但自己每天要看是另一回事。


def _kitchen_for_personal(threshold_days=3):
    """快過期食材 + 建議菜色。沒有快過期的就回 None。

    沒事時回 None 是刻意的：每天都跳一則「今天沒有要過期的」，
    人很快就會開始略過整則推播。

    跟 command_router 的「快過期」指令不同 —— 那是使用者主動問的，
    沒有快過期的也必須回答，不能靜默。
    """
    import kitchen
    import notion_db

    if not notion_db.is_configured():
        return None

    pantry = notion_db.pantry_load()
    if not kitchen.expiring_soon(pantry, threshold_days):
        return None

    items, more = kitchen.expiring_actions(pantry, threshold_days)

    recipe_text = ""
    recipes = notion_db.recipes_load(pantry)
    if recipes:
        recs = kitchen.recommend(pantry, recipes, threshold_days)
        if recs:
            recipe_text = kitchen.format_recommendations(recs)

    # 有按鈕時文字只留菜色建議，快過期清單交給按鈕列呈現；
    # 撈不到 page_id（沒按鈕）就退回完整文字，提醒不能消失
    parts = [kitchen.format_expiring(pantry, threshold_days)]
    if recipe_text:
        parts.append(recipe_text)

    return {
        "items": items,
        "more": more,
        "recipe_text": recipe_text,
        "text": "\n\n".join(parts),
        # 摘要列要的是「幾樣快過期」的完整數字，不是給按鈕的 items（那被 limit
        # 到 5 筆、又只留有 page_id 的）。所以直接數整份快過期清單。
        "expiring_count": len(kitchen.expiring_soon(pantry, threshold_days)),
    }


def _spending_recent():
    """最近一天的消費明細 + 本月累計。沒有任何支出資料就回 None。

    刻意不是「昨天」：國泰消費彙整信每天彙整前一日、當天下午才寄到，
    早上推播時昨天的資料還沒進 Notion。寫死「昨日」會每天都是空的。
    """
    import finance_report
    import notion_db

    if not notion_db.is_configured():
        return None

    txns = notion_db.transactions_load()
    return finance_report.format_latest_day_spending(txns, today_tpe())


NL = chr(10)
SEP = NL * 2


def _build_personal_sections(todos, reminders, monthly_detail,
                             spending, kitchen, weather):
    """個人版每日信的區塊與順序：**待辦 → 財務 → 買菜**（使用者指定，
    2026-08-26），跟 digest_preview.html 範本原本的順序不同。

    本月明細排在最新消費前面 —— 使用者要的是「整個月的花銷」，
    那是主角，最新消費只是補充。

    天氣範本裡沒有但現有信件有，保留並排最後（移除功能不在這次要求裡）。
    空的區塊直接不放：留一張空卡片比沒有還糟。
    """
    candidates = [
        ("📋 今日待辦", todos),
        ("⏰ 進行中提醒", reminders),
        ("💳 本月消費明細", monthly_detail),
        ("🧾 最新消費", spending),
        ("🍳 冰箱快過期・煮什麼", kitchen),
        ("🌤️ 天氣", weather),
    ]
    return [(title, text) for title, text in candidates if text]


def _email_personal_report(today):
    """個人版：食材 + 板橋天氣 + 最新消費，寄 Gmail 給自己。

    2026-08-20 從 LINE 1 對 1 推播改成 email。push 每月只有 200 則，
    群組版每天已經佔掉一則，個人版再佔一則等於一半配額花在自己身上；
    email 免費。群組版維持 LINE 不動 —— 家人不會去收信。

    天氣得重抓（地點跟群組版不同，見 weather.PERSONAL_WEATHER_LOCATIONS），
    食材也得自己抓 —— 群組版 2026-08-16 之後就不碰食材了。

    食材用完整文字版（kitchen["text"]）而不是推播那套按鈕版：email 沒有
    quick reply，「已用掉」還是得回 LINE 打，所以清單不能只靠按鈕呈現。
    要改回 LINE 推播的話，flex_builder.personal_report_carousel 還留著。

    整段包在呼叫端的 try 裡：個人版炸掉不能影響已經推出去的群組版。
    """
    import mailer

    if not mailer.is_configured():
        print("[個人版] 沒設 GMAIL_USER / SEND_TOKEN_PICKLE_B64，跳過")
        return

    def _personal_weather():
        msg, _ = get_weather_report(PERSONAL_WEATHER_LOCATIONS)
        return msg

    weather_text = _safe("個人版天氣", _personal_weather)
    kitchen = _safe("個人版食材", _kitchen_for_personal) or {}
    spending_text = _safe("個人版消費", _spending_recent)

    def _personal_todos():
        user_id = os.environ.get("PERSONAL_USER_ID", "").strip()
        if not user_id:
            return None          # 沒設就跳過這區塊，跟 mailer 的 gate 同一套
        import personal
        return personal.format_todos(user_id)

    def _personal_reminders():
        user_id = os.environ.get("PERSONAL_USER_ID", "").strip()
        if not user_id:
            return None
        import personal
        return personal.format_reminders(user_id)

    # 摘要列要的數字（本月總額）順手在明細那次載入算出來，不另外打一次
    # Notion —— transactions_load 已經是這個 job 裡最貴的呼叫。
    stats = {}

    def _monthly_detail():
        # 使用者要「一整個月的花銷都列出來」（2026-08-26）。
        # limit 抓 400 是因為當月筆數可能超過 transactions_load 的預設 200，
        # 抓不夠會安靜地少列幾筆 —— 那正是這個專案一直在防的錯。
        import notion_db
        from finance_report import (
            format_monthly_detail, month_spending_total, _EMPTY_MONTH,
        )
        txns = notion_db.transactions_load(limit=400)
        month = today_tpe().strftime("%Y-%m")
        stats["month_total"] = month_spending_total(txns, month)
        text = format_monthly_detail(txns, month)
        # 月初還沒有任何消費時 format_monthly_detail 回的是一段說明文字，
        # 不是空值。那段對「/財務」這種指令查詢有用（你問了就該回答），
        # 但對每日信只是雜訊 —— 其他區塊也空的話整封信就不該寄。
        # 判斷放在這裡而不是改 format_monthly_detail，改它會弄壞指令查詢。
        return None if text == _EMPTY_MONTH else text

    def _todo_count():
        user_id = os.environ.get("PERSONAL_USER_ID", "").strip()
        if not user_id:
            return 0
        import personal
        # cache 這時已被 format_todos 暖過，不會再打 Notion
        return len(personal.list_todos(user_id))

    todos_text = _safe("個人版待辦", _personal_todos)
    reminders_text = _safe("個人版提醒", _personal_reminders)
    monthly_text = _safe("個人版本月明細", _monthly_detail)
    todo_count = _safe("個人版待辦數", _todo_count) or 0

    sections = _build_personal_sections(
        todos=todos_text,
        reminders=reminders_text,
        monthly_detail=monthly_text,
        spending=spending_text,
        kitchen=kitchen.get("text"),
        weather=weather_text,
    )
    if not sections:
        # 全部區塊都沒東西 → 不寄空信
        print("[個人版] 沒有任何內容，這次不寄")
        return

    # 置頂摘要列：幾筆待辦、本月花多少、幾樣要過期 —— 一眼看懂。
    # 每格只在有數字時出現（0 不放，避免「本月 NT$0」的雜訊），
    # 顏色對齊 digest 卡片左色條。
    from finance_report import _money
    month_total = stats.get("month_total") or 0
    expiring_count = kitchen.get("expiring_count") or 0
    tiles = []
    if todo_count:
        tiles.append(("📋", str(todo_count), "待辦", "#a97b50"))
    if month_total:
        tiles.append(("💳", f"NT${_money(month_total)}", "本月", "#3a6ea5"))
    if expiring_count:
        tiles.append(("🍳", str(expiring_count), "快過期", "#7d9a4f"))

    import digest
    html = digest.build_digest_html(today, sections, summary=tiles)
    # 純文字版是給不吃 HTML 的收信端看的，內容一樣、沒有版型。
    # 摘要列在純文字也放一行（不吃 HTML 也該一眼看到重點）。
    plain_parts = []
    if tiles:
        plain_parts.append(" ・ ".join(f"{e} {label} {v}" for e, v, label, _ in tiles))
    plain_parts.extend(f"{title}{NL}{text}" for title, text in sections)
    plain = SEP.join(plain_parts)

    mailer.send_email(f"📮 每日個人報 {today}", plain, html=html)


async def run_daily_report(force_premarket=False):
    print(f"開始執行每日情報... (force_premarket={force_premarket})")
    today = today_tpe().strftime("%Y-%m-%d")

    # 1. 天氣
    def _weather():
        weather_msg, _ = get_weather_report()  # chart_path 不用，LINE 不傳圖
        return weather_msg
    weather_text = _safe("天氣", _weather)

    # 2. 盤前報告（週末回 None，week_text 也是 None）
    premarket_text = _safe(
        "盤前",
        lambda: build_premarket_report(force=force_premarket),
    )

    # 2b. 盤前有內容才附市場新聞
    if premarket_text:
        market_news = _safe("市場新聞", _fetch_market_news)
        if market_news:
            premarket_text = f"{premarket_text}\n\n📰 今日市場新聞\n{market_news}"

    # 3. 今日一則（小知識/笑話 + 節日 + 天氣新聞）
    extra_text = _safe("今日一則", humor.get_daily_extra)

    # 組 carousel 一次推（1 則 push）
    carousel = daily_report_carousel(extra_text, weather_text, premarket_text, today)
    if carousel is None:
        # 兩段都炸了 → 推一則純文字告知
        await push_message(f"<b>⚠️ 每日情報 {today}</b>\n資料暫時無法取得，已通知維運。")
    else:
        await push_message(carousel)
        print("每日情報傳送完成！")

    # 個人版另外寄一封信。放在群組版之後、且整段包 try ——
    # 這是附加功能，壞掉不該影響主推播（群組版這時已經送出去了）
    try:
        _email_personal_report(today)
    except Exception as e:
        print(f"[個人版] 失敗：{e}")
        traceback.print_exc()
        notify_admin(e, {"module": "daily_report", "section": "個人版"})
