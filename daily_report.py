"""
每日報告：天氣 + 盤前 包成 Flex Carousel 1 則 push。

設計：先 gather 兩段純文字（不直接推），再組成 carousel push 1 次。
任一段失敗就在對應 bubble 顯示降級文案；兩段都失敗才 fallback 純文字。
比過去切 2-3 chunk 多次 push 節省 30-60 則配額/月。

推兩個目標，內容刻意不同（共 2 則 push/天，約 60 則/月）：
- 群組（LINE_GROUP_ID）：今日一則 + 食材 + 天氣（淡水、金山）+ 盤前
- 本人 1 對 1（ADMIN_LINE_USER_ID）：食材 + 天氣（板橋）+ 最新消費
  財務只走個人版，不進群組。
"""

import os
import traceback

import humor
from admin_notify import notify_admin
from flex_builder import daily_report_carousel, personal_report_carousel
from line_sender import push_message, push_to_user_sync
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


def _kitchen_payload(threshold_days=3):
    """食材提醒的素材。沒有快過期的就回 None。

    刻意在沒事時回 None 而不是「沒有要過期的食材」：每天都跳一則
    無事發生的提醒，人很快就會開始略過整則推播。

    回 dict：
    - items / more：給按鈕版 bubble 用（沒 page_id 的食材按了也無法定位，
      expiring_actions 已把它們濾掉）
    - recipe_text：只有建議菜色那段。有按鈕時快過期清單由按鈕列呈現，
      文字再列一次會變成同一份資料講兩遍
    - text：純文字 fallback，清單 + 建議都要在裡面
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

    parts = [kitchen.format_expiring(pantry, threshold_days)]
    if recipe_text:
        parts.append(recipe_text)

    return {
        "items": items,
        "more": more,
        "recipe_text": recipe_text,
        "text": "\n\n".join(parts),
    }


def _kitchen_reminder(threshold_days=3):
    """快過期食材 + 建議今天煮什麼的純文字版。沒有快過期的就回 None。"""
    payload = _kitchen_payload(threshold_days)
    return payload["text"] if payload else None


# 2026-08-16 使用者要求把「最近一天消費」從**群組**推播拿掉 —— 每天固定跳
# 一段回顧性資訊會稀釋掉推播真正要提醒的事。要看時自己問：
# LINE 打「最新消費」走 command_router 的 fin_latest_day。
# 2026-08-19 加回來，但只走個人版：財務不進群組。


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


def _push_personal_report(today, kitchen_text, kitchen_items, kitchen_more):
    """個人版推播：食材 + 板橋天氣 + 最新消費，推到本人 1 對 1。

    食材沿用群組版已經抓好的資料 —— 同一份東西不必再打一次 Notion。
    天氣得重抓，因為地點跟群組版不同（見 weather.PERSONAL_WEATHER_LOCATIONS）。

    整段包在呼叫端的 try 裡：個人版炸掉不能影響已經推出去的群組版。
    """
    admin_id = os.environ.get("ADMIN_LINE_USER_ID", "")
    if not admin_id:
        print("[個人版] 沒設 ADMIN_LINE_USER_ID，跳過")
        return

    def _personal_weather():
        msg, _ = get_weather_report(PERSONAL_WEATHER_LOCATIONS)
        return msg

    weather_text = _safe("個人版天氣", _personal_weather)
    spending_text = _safe("個人版消費", _spending_recent)

    carousel = personal_report_carousel(
        today,
        weather_text=weather_text,
        kitchen_text=kitchen_text,
        kitchen_items=kitchen_items,
        kitchen_more=kitchen_more,
        spending_text=spending_text,
    )
    if carousel is None:
        # 三段都沒東西（沒食材要過期 + 天氣掛了 + 沒消費資料）→ 不推空卡
        print("[個人版] 沒有任何內容，這次不推")
        return

    push_to_user_sync(admin_id, carousel)
    print("個人版情報傳送完成！")


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

    # 4. 食材提醒（沒有快過期的就回 None，不佔 bubble）
    kitchen = _safe("食材提醒", _kitchen_payload) or {}
    kitchen_items = kitchen.get("items") or []
    # 有按鈕時文字只留菜色建議，清單交給按鈕列；撈不到 page_id 就退回完整文字
    kitchen_text = (kitchen.get("recipe_text") if kitchen_items
                    else kitchen.get("text")) or None

    # 組 carousel 一次推（1 則 push）
    carousel = daily_report_carousel(extra_text, weather_text, premarket_text, today,
                                     kitchen_text=kitchen_text,
                                     kitchen_items=kitchen_items,
                                     kitchen_more=kitchen.get("more", 0))
    if carousel is None:
        # 兩段都炸了 → 推一則純文字告知
        await push_message(f"<b>⚠️ 每日情報 {today}</b>\n資料暫時無法取得，已通知維運。")
    else:
        await push_message(carousel)
        print("每日情報傳送完成！")

    # 個人版另外推一則。放在群組版之後、且整段包 try ——
    # 這是附加功能，壞掉不該影響主推播（群組版這時已經送出去了）
    try:
        _push_personal_report(today, kitchen_text, kitchen_items,
                              kitchen.get("more", 0))
    except Exception as e:
        print(f"[個人版] 失敗：{e}")
        traceback.print_exc()
        notify_admin(e, {"module": "daily_report", "section": "個人版"})
