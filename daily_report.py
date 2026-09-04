"""
每日報告：天氣 + 盤前 包成 Flex Carousel 1 則 push。

設計：先 gather 兩段純文字（不直接推），再組成 carousel push 1 次。
任一段失敗就在對應 bubble 顯示降級文案；兩段都失敗才 fallback 純文字。
比過去切 2-3 chunk 多次 push 節省 30-60 則配額/月。

推兩個目標，內容刻意不同，但只有群組吃 push 配額（1 則/天，約 30 則/月）：
- 群組（LINE_GROUP_ID）：今日一則 + 天氣（淡水、金山）+ 盤前 —— LINE push
- 本人：待辦 + 今日三句 + 財務 + 天氣（板橋）—— 2026-08-20 改寄 Gmail（見 mailer.py）
  財務只走個人版，不進群組。

2026-09-04 個人版拿掉「冰箱快過期・煮什麼」區塊：使用者覺得每天跳這張
太吵。食材記錄（LINE「買了」、電子發票 → pantry 的自動記錄）完全沒動，
動的只是「每天被動通知」這件事 —— 要看自己在 LINE 打「快過期」問。
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


def _spending_recent():
    """有資料的最近三天消費明細。沒有任何支出資料就回 None。

    刻意不是「昨天」：國泰消費彙整信每天彙整前一日、當天下午才寄到，
    早上寄信時昨天的資料還沒進 Notion。寫死「昨日」會每天都是空的。

    2026-09-04 從 format_latest_day_spending（最近一天）改成
    format_recent_days（最近三個有資料的日期）—— 使用者要「近三天花費」。
    """
    import finance_report
    import notion_db

    if not notion_db.is_configured():
        return None

    txns = notion_db.transactions_load(limit=400)
    return finance_report.format_recent_days(txns, today_tpe(), days=3)


NL = chr(10)
SEP = NL * 2

# 圓餅圖在信裡的 Content-ID。mailer 掛圖與 digest 產 <img> 都引用它 ——
# 兩邊各寫死一個字串遲早會漂移
CHART_CID = "spending"


def _build_personal_sections(todos, reminders, recent_days, weather,
                             phrases=None, monthly_chart=None):
    """個人版每日信的區塊與順序。

    待辦 → 今日三句 → 財務 → 天氣（使用者指定順序 2026-08-26）。

    三句排在待辦之後而不是信尾：學習內容放最後容易被滑過去。待辦仍然
    排最前 —— 那是當天要做的事。天氣永遠壓最後。

    2026-09-04 拿掉「冰箱快過期・煮什麼」：使用者覺得每天跳這張太吵。
    買了什麼還是記在 pantry，只是不再主動通知 —— 沒有 kitchen 參數了，
    加回來之前先看 tests/test_daily_kitchen.py 同一套精神的測試。

    2026-09-04 原本的「本月消費明細」（整月逐筆）與「最新消費」合併成
    「📊 本月消費分布」（圓餅圖）+「🧾 近三天消費」。整月流水帳長到
    沒人看，使用者要的是分布；流水帳只需要最近幾天。

    圓餅圖排在近三天前面：分布是主角，流水帳是補充。

    monthly_chart 是 (摘要文字, cid) 或 None。有 cid 的區塊回三元組，
    digest.build_digest_html 據此插 <img>。

    空的區塊直接不放：留一張空卡片比沒有還糟。
    """
    candidates = [
        ("📋 今日待辦", todos),
        ("⏰ 進行中提醒", reminders),
        ("🗣️ 今日三句", phrases),
        ("🧾 近三天消費", recent_days),
        ("🌤️ 天氣", weather),
    ]
    out = [(title, text) for title, text in candidates if text]

    if monthly_chart and monthly_chart[0]:
        summary, cid = monthly_chart
        # 插在「近三天消費」之前。用索引搜尋而不是寫死位置 ——
        # 空區塊會被濾掉，位置每天都不一樣。
        titles = [s[0] for s in out]
        if "🧾 近三天消費" in titles:
            at = titles.index("🧾 近三天消費")
        else:
            # 天氣永遠壓最後，分布不能掉到它後面
            at = len(out) - (1 if "🌤️ 天氣" in titles else 0)
        out.insert(at, ("📊 本月消費分布", summary, cid))

    return out


def _email_personal_report(today):
    """個人版：待辦 + 今日三句 + 財務 + 板橋天氣，寄 Gmail 給自己。

    2026-08-20 從 LINE 1 對 1 推播改成 email。push 每月只有 200 則，
    群組版每天已經佔掉一則，個人版再佔一則等於一半配額花在自己身上；
    email 免費。群組版維持 LINE 不動 —— 家人不會去收信。

    天氣得重抓（地點跟群組版不同，見 weather.PERSONAL_WEATHER_LOCATIONS）。

    2026-09-04 拿掉「冰箱快過期・煮什麼」區塊：使用者覺得每天跳這張太吵。
    買了什麼仍然記在 pantry（LINE「買了」、電子發票 → pantry 的橋接都沒動），
    只是不再每天主動通知快過期 —— 要看自己在 LINE 打「快過期」問
    command_router 的 pantry_expiring。

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

    def _monthly_chart():
        """圓餅圖 + 摘要文字。當月沒有 TWD 支出時回 None。"""
        import notion_db
        import spending_chart
        txns = notion_db.transactions_load(limit=400)
        path, summary = spending_chart.build_pie(
            txns, today_tpe().strftime("%Y-%m")
        )
        return (path, summary) if path else None

    todos_text = _safe("個人版待辦", _personal_todos)
    reminders_text = _safe("個人版提醒", _personal_reminders)
    chart = _safe("個人版消費圓餅圖", _monthly_chart)
    chart_path, chart_summary = chart if chart else (None, None)

    def _daily_phrases():
        import phrasebook
        return phrasebook.daily_three(today_tpe())

    phrases_text = _safe("個人版今日三句", _daily_phrases)

    sections = _build_personal_sections(
        todos=todos_text,
        reminders=reminders_text,
        recent_days=spending_text,
        weather=weather_text,
        phrases=phrases_text,
        monthly_chart=(chart_summary, CHART_CID) if chart_path else None,
    )
    if not sections:
        # 全部區塊都沒東西 → 不寄空信
        print("[個人版] 沒有任何內容，這次不寄")
        return

    import digest
    html = digest.build_digest_html(today, sections)
    # 純文字版是給不吃 HTML 的收信端看的，內容一樣、沒有版型
    plain = SEP.join(f"{s[0]}{NL}{s[1]}" for s in sections)

    mailer.send_email(
        f"📮 每日個人報 {today}", plain, html=html,
        images={CHART_CID: chart_path} if chart_path else None,
    )


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
