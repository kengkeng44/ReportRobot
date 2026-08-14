"""
LINE Flex Message 卡片產生器。

提供：
- todo_list_flex(items)         待辦清單卡片（每筆一行 + 完成 postback 按鈕）
- reminder_list_flex(items)     提醒清單卡片（每筆 + 取消 / 延後 30 分按鈕）
- typhoon_alert_flex(...)       颱風警報卡片（informational only）
- text_bubble(title, body, ...) 通用「標題 + 多段文字」bubble（給每日報用）
- daily_report_carousel(...)    每日報 carousel（多 bubble 橫滑、1 則 push 搞定）

postback data 採 urlencoded query string，格式：
- action=todo_complete&id=5
- action=reminder_cancel&id=3
- action=reminder_snooze&id=3&min=30

回傳格式：LINE Messaging API 的 message dict
  {"type": "flex", "altText": "...", "contents": {bubble or carousel dict}}

altText 必填，是通知列 / 不支援 Flex 客戶端的 fallback。
"""

import re
from html import unescape
from urllib.parse import urlencode


_BROWN = "#A0826D"
_GREEN = "#88B07A"
_RED = "#D9534F"
_ORANGE = "#F0AD4E"
_LIGHT_BG = "#FAF7F2"
_SEP = "#EEE2D7"
_TEXT_DARK = "#3A2E27"
_TEXT_LIGHT = "#8A7A6E"


def _postback(action, **params):
    return urlencode({"action": action, **params})


def _separator():
    return {"type": "separator", "margin": "md", "color": _SEP}


def _header(title, subtitle, bg=_BROWN):
    contents = [{
        "type": "text", "text": title,
        "color": "#FFFFFF", "weight": "bold", "size": "lg",
    }]
    if subtitle:
        contents.append({
            "type": "text", "text": subtitle,
            "color": "#FFFFFF", "size": "sm", "margin": "xs",
        })
    return {
        "type": "box", "layout": "vertical",
        "backgroundColor": bg, "paddingAll": "lg",
        "contents": contents,
    }


def _footer_tip(text):
    return {
        "type": "box", "layout": "vertical",
        "paddingAll": "md", "backgroundColor": _LIGHT_BG,
        "contents": [{
            "type": "text", "text": text,
            "size": "xs", "color": _TEXT_LIGHT,
            "align": "center", "wrap": True,
        }],
    }


def _wrap(bubble, alt):
    return {"type": "flex", "altText": alt[:400], "contents": bubble}


# ════════════════════════════════════════
# 待辦清單
# ════════════════════════════════════════

def todo_list_flex(items):
    """items: [{'id': int, 'text': str}, ...]"""
    if not items:
        bubble = {
            "type": "bubble", "size": "mega",
            "header": _header("📋 待辦清單", "全部清空"),
            "body": {
                "type": "box", "layout": "vertical", "spacing": "md",
                "backgroundColor": _LIGHT_BG, "paddingAll": "xl",
                "contents": [
                    {"type": "text", "text": "🎉", "size": "3xl", "align": "center"},
                    {"type": "text", "text": "目前沒有待辦事項",
                     "size": "md", "align": "center", "color": _TEXT_DARK,
                     "weight": "bold", "margin": "md"},
                    {"type": "text", "text": "辛苦了！",
                     "size": "sm", "align": "center", "color": _TEXT_LIGHT,
                     "margin": "sm"},
                ],
            },
            "footer": _footer_tip("新增：/待辦 加 [內容]"),
        }
        return _wrap(bubble, alt="📋 沒有待辦事項")

    rows = []
    for i, t in enumerate(items):
        if i > 0:
            rows.append(_separator())
        text_preview = t["text"][:40]
        rows.append({
            "type": "box", "layout": "horizontal",
            "spacing": "md", "alignItems": "center",
            "contents": [
                {
                    "type": "text", "text": f"⬜ {t['text']}",
                    "size": "sm", "color": _TEXT_DARK,
                    "wrap": True, "flex": 5,
                },
                {
                    "type": "button",
                    "style": "primary", "color": _GREEN,
                    "height": "sm", "flex": 2,
                    "action": {
                        "type": "postback",
                        "label": "完成",
                        "data": _postback("todo_complete", id=t["id"]),
                        "displayText": f"✅ 完成 [{t['id']}] {text_preview}",
                    },
                },
            ],
        })

    bubble = {
        "type": "bubble", "size": "mega",
        "header": _header("📋 待辦清單", f"共 {len(items)} 筆待完成"),
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "backgroundColor": _LIGHT_BG, "paddingAll": "lg",
            "contents": rows,
        },
        "footer": _footer_tip("新增：/待辦 加 [內容]"),
    }
    return _wrap(bubble, alt=f"📋 待辦清單（{len(items)} 筆）")


# ════════════════════════════════════════
# 提醒清單
# ════════════════════════════════════════

def reminder_list_flex(items):
    """items: [{'id': int, 'text': str, 'fire_at': datetime}, ...]
    items 由 personal.list_reminders 提供（已依 fire_at 排序）。"""
    if not items:
        bubble = {
            "type": "bubble", "size": "mega",
            "header": _header("⏰ 進行中的提醒", "目前沒有"),
            "body": {
                "type": "box", "layout": "vertical",
                "backgroundColor": _LIGHT_BG, "paddingAll": "xl", "spacing": "md",
                "contents": [
                    {"type": "text", "text": "💤", "size": "3xl", "align": "center"},
                    {"type": "text", "text": "目前沒有提醒",
                     "size": "md", "align": "center", "color": _TEXT_DARK,
                     "weight": "bold", "margin": "md"},
                ],
            },
            "footer": _footer_tip("範例：/提醒 18:00 倒垃圾、/提醒 30 分鐘後 喝水"),
        }
        return _wrap(bubble, alt="⏰ 沒有提醒")

    rows = []
    for i, t in enumerate(items):
        if i > 0:
            rows.append(_separator())
        when = t["fire_at"].strftime("%m/%d %H:%M")
        text_preview = t["text"][:20]
        rows.append({
            "type": "box", "layout": "vertical", "spacing": "xs",
            "contents": [
                {
                    "type": "box", "layout": "baseline", "spacing": "sm",
                    "contents": [
                        {"type": "text", "text": "⏰", "size": "sm", "flex": 0},
                        {"type": "text", "text": when,
                         "size": "sm", "weight": "bold",
                         "color": _TEXT_DARK, "flex": 4},
                    ],
                },
                {
                    "type": "text", "text": t["text"],
                    "size": "sm", "color": _TEXT_DARK,
                    "wrap": True, "margin": "xs",
                },
                {
                    "type": "box", "layout": "horizontal",
                    "spacing": "sm", "margin": "sm",
                    "contents": [
                        {
                            "type": "button",
                            "style": "secondary",
                            "height": "sm", "flex": 1,
                            "action": {
                                "type": "postback",
                                "label": "延後 30 分",
                                "data": _postback("reminder_snooze", id=t["id"], min=30),
                                "displayText": f"⏳ 延後 [{t['id']}] 30 分",
                            },
                        },
                        {
                            "type": "button",
                            "style": "primary", "color": _RED,
                            "height": "sm", "flex": 1,
                            "action": {
                                "type": "postback",
                                "label": "取消",
                                "data": _postback("reminder_cancel", id=t["id"]),
                                "displayText": f"🗑️ 取消提醒 [{t['id']}] {text_preview}",
                            },
                        },
                    ],
                },
            ],
        })

    bubble = {
        "type": "bubble", "size": "mega",
        "header": _header("⏰ 進行中的提醒", f"共 {len(items)} 個"),
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md",
            "backgroundColor": _LIGHT_BG, "paddingAll": "lg",
            "contents": rows,
        },
        "footer": _footer_tip("新增：/提醒 明天 9:30 開會"),
    }
    return _wrap(bubble, alt=f"⏰ 進行中提醒（{len(items)} 個）")


# ════════════════════════════════════════
# 颱風警報
# ════════════════════════════════════════

# ════════════════════════════════════════
# 通用：標題 + 多段文字 bubble（給每日報、自由問答長文用）
# ════════════════════════════════════════

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BOLD_TAG_RE = re.compile(r"<b>([^<]+)</b>")
_LEADING_BOLD_HEADER_RE = re.compile(r"^\s*<b>[^<]+</b>\s*\n+")


def _strip_html(text):
    """LINE Flex text 元件不支援 HTML，全部 strip 掉只留純文字。"""
    if not text:
        return ""
    return unescape(_HTML_TAG_RE.sub("", text)).strip()


def text_bubble(title, body, subtitle="", header_color=_BROWN, bold_subheaders=True):
    """通用 bubble：header（標題 + 副標）+ body（按 \\n\\n 拆成多段 text 元件）。

    - 自動拿掉 body 開頭重複的 <b>title</b> 段（避免跟 header 重複）
    - bold_subheaders=True 時：多行段落第一行（≤30 字）視為次標 bold
      single-bubble 多區塊內容（每日報的 premarket / weather）用 True
      single-section 內容（個股 carousel 的單一 section）用 False
    """
    if not body:
        body = ""
    # 拿掉 body 開頭重複的 <b>...</b>\n\n（避免跟 bubble header 撞）
    body = _LEADING_BOLD_HEADER_RE.sub("", body, count=1)
    body = _strip_html(body)

    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    contents = []
    for i, para in enumerate(paragraphs):
        if i > 0:
            contents.append(_separator())
        lines = para.split("\n")
        if bold_subheaders and len(lines) > 1 and len(lines[0]) <= 30:
            contents.append({
                "type": "text", "text": lines[0],
                "size": "sm", "weight": "bold",
                "color": _TEXT_DARK, "wrap": True,
            })
            contents.append({
                "type": "text", "text": "\n".join(lines[1:]),
                "size": "xs", "color": _TEXT_DARK,
                "wrap": True, "margin": "xs",
            })
        else:
            contents.append({
                "type": "text", "text": para,
                "size": "sm", "color": _TEXT_DARK, "wrap": True,
            })

    if not contents:
        contents = [{
            "type": "text", "text": "（無內容）",
            "size": "sm", "color": _TEXT_LIGHT, "align": "center",
        }]

    return {
        "type": "bubble", "size": "mega",
        "header": _header(title, subtitle, bg=header_color),
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "backgroundColor": _LIGHT_BG, "paddingAll": "lg",
            "contents": contents,
        },
    }


def kitchen_reminder_bubble(items, subtitle="", extra_text="", more_count=0):
    """食材提醒 bubble：每樣快過期食材一列 + 一顆「已用掉」按鈕。

    items 由 kitchen.expiring_actions() 產生：[{page_id, name, days_text}]。
    數量已在那裡截斷（一張 bubble 塞不下無限顆按鈕），這裡只負責畫。
    more_count > 0 時會寫出還有幾樣沒放上來 —— 默默吞掉會讓人以為只剩這些。
    extra_text 放建議菜色那段（快過期清單本身已由按鈕列呈現，不要重複）。
    """
    rows = []
    for i, it in enumerate(items):
        if i > 0:
            rows.append(_separator())
        name = it["name"]
        rows.append({
            "type": "box", "layout": "horizontal",
            "spacing": "md", "alignItems": "center",
            "contents": [
                {
                    "type": "box", "layout": "vertical",
                    "flex": 5, "spacing": "xs",
                    "contents": [
                        {"type": "text", "text": name,
                         "size": "sm", "weight": "bold",
                         "color": _TEXT_DARK, "wrap": True},
                        {"type": "text", "text": it.get("days_text") or "",
                         "size": "xs", "color": _TEXT_LIGHT},
                    ],
                },
                {
                    "type": "button",
                    "style": "primary", "color": _GREEN,
                    "height": "sm", "flex": 3,
                    "action": {
                        "type": "postback",
                        "label": "已用掉",
                        # 用 page_id 定位：名稱可能重複，而且點下去時庫存可能已變動。
                        # n 只給「重複點擊」時的可讀訊息用，截短以守住 300 字元上限。
                        "data": _postback("pantry_used",
                                          pid=it["page_id"], n=name[:8]),
                        "displayText": f"🍳 用掉 {name[:20]}",
                    },
                },
            ],
        })

    if more_count > 0:
        rows.append(_separator())
        rows.append({
            "type": "text",
            "text": f"還有 {more_count} 樣快過期，打「快過期」看全部",
            "size": "xs", "color": _TEXT_LIGHT, "wrap": True,
        })

    if extra_text:
        rows.append(_separator())
        for para in [p.strip() for p in _strip_html(extra_text).split("\n\n") if p.strip()]:
            rows.append({
                "type": "text", "text": para,
                "size": "xs", "color": _TEXT_DARK, "wrap": True,
            })

    if not rows:
        rows = [{
            "type": "text", "text": "（無內容）",
            "size": "sm", "color": _TEXT_LIGHT, "align": "center",
        }]

    return {
        "type": "bubble", "size": "mega",
        "header": _header("🥬 食材提醒", subtitle, bg="#6F9A62"),
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "backgroundColor": _LIGHT_BG, "paddingAll": "lg",
            "contents": rows,
        },
        "footer": _footer_tip("點「已用掉」會標成用完並自動排進採購清單"),
    }


QUICK_REPLY_MAX = 13      # LINE 一則最多 13 顆，超過整包被打回
QUICK_REPLY_LABEL_MAX = 20


def quick_reply_text(body, options):
    """一則文字訊息 + 一排 quick reply 按鈕。

    options: [(label, 送出的文字)]。按下去等同使用者自己打那串字，
    所以送出的文字必須是 parse() 認得的指令，不然點了沒反應。

    超過上限的直接截掉、label 過長截短 —— LINE 是整則退回，
    寧可少一顆按鈕也不要整句話送不出去。
    options 空的時候不放 quickReply key：空物件會被當格式錯誤。
    """
    msg = {"type": "text", "text": body}
    items = [
        {
            "type": "action",
            "action": {
                "type": "message",
                "label": str(label)[:QUICK_REPLY_LABEL_MAX],
                "text": str(text),
            },
        }
        for label, text in (options or [])[:QUICK_REPLY_MAX]
    ]
    if items:
        msg["quickReply"] = {"items": items}
    return msg


def daily_report_carousel(extra_text, weather_text, premarket_text, today_str,
                          kitchen_text=None, spending_text=None,
                          kitchen_items=None, kitchen_more=0):
    """把每日報組成 carousel（橫滑），1 則 push 搞定。
    順序：今日一則 → 食材提醒 → 天氣 → 盤前 → 消費。全部缺回 None。

    kitchen_text 只在真的有食材快過期時才給 —— 沒事就不要佔一個 bubble，
    每天都跳「沒有要過期的」會讓人開始略過整則推播。

    kitchen_items 有值時走「附按鈕」版本，kitchen_text 這時只放建議菜色那段
    （快過期清單改由按鈕列呈現）；沒有 items（例如撈不到 page_id）就退回純文字
    bubble，提醒還是要出現，只是少了按鈕。
    """
    bubbles = []

    if extra_text:
        bubbles.append(text_bubble(
            title="💫 今日一則",
            subtitle=today_str,
            body=extra_text,
            header_color=_GREEN,
        ))

    # 排在天氣前面：這是有時效、要今天動手的事，看得越早越好
    if kitchen_items:
        bubbles.append(kitchen_reminder_bubble(
            kitchen_items,
            subtitle=today_str,
            extra_text=kitchen_text or "",
            more_count=kitchen_more,
        ))
    elif kitchen_text:
        bubbles.append(text_bubble(
            title="🥬 食材提醒",
            subtitle=today_str,
            body=kitchen_text,
            header_color="#6F9A62",
        ))

    if weather_text:
        bubbles.append(text_bubble(
            title="🌤️ 天氣報告",
            subtitle=today_str,
            body=weather_text,
            header_color=_BROWN,
        ))
    else:
        bubbles.append(text_bubble(
            title="🌤️ 天氣報告",
            subtitle=today_str,
            body="⚠️ 天氣資料暫時無法取得",
            header_color=_BROWN,
        ))

    if premarket_text:
        bubbles.append(text_bubble(
            title="📊 盤前報告",
            subtitle=today_str,
            body=premarket_text,
            header_color="#5B8DA6",
        ))
    # premarket_text 為 None 時通常是週末，這時就只有今日一則 + 天氣 bubble

    # 排最後：食材是今天要動手的事，消費是回顧，優先度最低
    if spending_text:
        bubbles.append(text_bubble(
            title="💳 最近一天消費",
            subtitle=today_str,
            body=spending_text,
            header_color="#A66F6F",
        ))

    if not bubbles:
        return None

    if len(bubbles) == 1:
        return _wrap(bubbles[0], alt="📅 每日情報")
    return _wrap(
        {"type": "carousel", "contents": bubbles},
        alt=f"📅 每日情報（{today_str}）",
    )


# ════════════════════════════════════════
# 個股報告 carousel（把 stock_news.get_stock_report 字串轉成多 bubble 橫滑）
# ════════════════════════════════════════

# 不同區塊用不同 header 色，視覺上比較有層次
_STOCK_SECTION_COLORS = {
    "📖": _BROWN,    # 簡介
    "💰": _GREEN,    # 股價
    "📦": "#5B8DA6", # ETF 持股
    "📊": "#7A6D9C", # 基本面
    "📰": _ORANGE,   # 新聞
    "🗣️": "#5C8A8A", # PTT
    "🌐": "#5C8A8A", # 英文論壇
    "🎴": "#A0826D", # Dcard
    "🤖": _BROWN,    # AI 解讀
}


def _section_color(header):
    """根據 section 開頭 emoji 挑顏色；不認得回 _BROWN。"""
    for emoji, color in _STOCK_SECTION_COLORS.items():
        if header.startswith(emoji):
            return color
    return _BROWN


def _parse_html_sections(text):
    """把 stock_news 輸出的多段 HTML 字串切成 [(header, body), ...]。
    格式：'<b>📌 ...</b>\\n\\n<b>📖 簡介</b>\\nbody1\\n\\n<b>💰 股價</b>\\nbody2...'
    第一段如果只有 header 無 body，body 會是空字串。"""
    parts = _BOLD_TAG_RE.split(text)
    sections = []
    # parts[0]=前置垃圾, parts[1]=第1個header, parts[2]=第1個body, parts[3]=第2個header...
    for i in range(1, len(parts), 2):
        head = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        sections.append((head, body))
    return sections


def stock_report_carousel(full_text):
    """把 stock_news.get_stock_report 輸出的 HTML 字串轉成 Flex Carousel。
    每個 <b>...</b> section 一張 bubble；首段「📌 ticker name」當 carousel altText、不變 bubble。
    解析失敗 / 沒 section 回 None（呼叫端 fallback 文字）。"""
    if not full_text:
        return None
    sections = _parse_html_sections(full_text)
    if not sections:
        return None

    title = None
    bubbles_data = []
    for head, body in sections:
        if head.startswith("📌") and not body:
            title = head  # 例：「📌 2330 台積電」
            continue
        if not body:
            continue
        bubbles_data.append((head, body))

    bubbles = []
    for head, body in bubbles_data[:10]:  # carousel 上限 12，留點 buffer
        bubbles.append(text_bubble(
            head, body,
            header_color=_section_color(head),
            bold_subheaders=False,  # 單一 section bubble 不需要再分次標
            subtitle=title.replace("📌 ", "") if title else "",
        ))

    if not bubbles:
        return None
    if len(bubbles) == 1:
        return _wrap(bubbles[0], alt=title or "個股報告")
    return _wrap(
        {"type": "carousel", "contents": bubbles},
        alt=title or "個股報告",
    )


# ════════════════════════════════════════
# 持倉概覽 Flex
# ════════════════════════════════════════

def _portfolio_row(r):
    """單檔持股 row：左邊 display + 股數 / 均價；右邊現價 + 漲跌% 色塊。"""
    pct = r.get("pnl_pct")
    if pct is None:
        pct_text = "—"
        pct_color = _TEXT_LIGHT
    else:
        sign = "+" if pct >= 0 else ""
        pct_text = f"{sign}{pct:.1f}%"
        pct_color = _GREEN if pct >= 0 else _RED

    current = r.get("current_str") or "N/A"
    avg = r.get("avg_str") or "—"
    shares = r.get("shares")
    display = r.get("display") or "—"

    return {
        "type": "box", "layout": "horizontal", "spacing": "sm",
        "contents": [
            {
                "type": "box", "layout": "vertical",
                "flex": 5, "spacing": "xs",
                "contents": [
                    {"type": "text", "text": display,
                     "size": "sm", "weight": "bold",
                     "color": _TEXT_DARK, "wrap": True},
                    {"type": "text", "text": f"{shares} 股 @{avg}",
                     "size": "xs", "color": _TEXT_LIGHT},
                ],
            },
            {
                "type": "box", "layout": "vertical",
                "flex": 4, "spacing": "xs",
                "contents": [
                    {"type": "text", "text": current,
                     "size": "sm", "color": _TEXT_DARK,
                     "align": "end", "wrap": True},
                    {"type": "text", "text": pct_text,
                     "size": "xs", "color": pct_color,
                     "weight": "bold", "align": "end"},
                ],
            },
        ],
    }


def _summary_line(label, s):
    """總報酬 footer 單行：label | 成本 | 市值 | ±X.X%"""
    pct = s["pct"]
    sign = "+" if pct >= 0 else ""
    color = _GREEN if pct >= 0 else _RED
    is_us = s.get("is_us", False)
    prefix = "$" if is_us else ""
    return {
        "type": "box", "layout": "horizontal", "spacing": "sm",
        "contents": [
            {"type": "text", "text": label,
             "size": "xs", "color": _TEXT_LIGHT,
             "flex": 3, "weight": "bold"},
            {"type": "text",
             "text": f"市值 {prefix}{s['value']:,.0f}",
             "size": "xs", "color": _TEXT_DARK,
             "flex": 4, "align": "end"},
            {"type": "text", "text": f"{sign}{pct:.1f}%",
             "size": "xs", "color": color,
             "weight": "bold", "flex": 2, "align": "end"},
        ],
    }


def portfolio_flex(data):
    """data 由 portfolio.build_portfolio_flex 計算後傳入；schema 見該函式 docstring。
    無持倉回 None。"""
    rows = data.get("rows") or []
    if not rows:
        return None

    body = []
    for i, r in enumerate(rows):
        if i > 0:
            body.append(_separator())
        body.append(_portfolio_row(r))

    # footer：台美分區總報酬 + 淨值合計
    footer_contents = []
    tw = data.get("tw_summary")
    us = data.get("us_summary")
    if tw or us:
        footer_contents.append({
            "type": "text", "text": "💰 總報酬",
            "size": "sm", "weight": "bold", "color": _TEXT_DARK,
        })
    if tw:
        footer_contents.append(_summary_line("🇹🇼 台股", tw))
    if us:
        footer_contents.append(_summary_line("🇺🇸 美股", us))

    net = data.get("net_value_ntd")
    rate = data.get("usd_twd_rate")
    if net is not None:
        suffix = f"  (USD/TWD={rate:.2f})" if rate and data.get("us_summary") else ""
        footer_contents.append(_separator())
        footer_contents.append({
            "type": "text",
            "text": f"💎 淨值合計：NTD {net:,.0f}{suffix}",
            "size": "sm", "weight": "bold",
            "color": _TEXT_DARK, "wrap": True,
        })
    elif data.get("us_value_only_usd") is not None and rate is None:
        # 匯率抓不到 → 顯示兩個幣別分計
        tw_v = data.get("tw_value_only_ntd") or 0
        us_v = data.get("us_value_only_usd")
        footer_contents.append(_separator())
        footer_contents.append({
            "type": "text",
            "text": f"💎 淨值：NTD {tw_v:,.0f} + USD {us_v:,.0f}（匯率抓取失敗）",
            "size": "xs", "weight": "bold",
            "color": _TEXT_DARK, "wrap": True,
        })

    bubble = {
        "type": "bubble", "size": "mega",
        "header": _header("📊 持倉概覽", f"共 {len(rows)} 檔"),
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "backgroundColor": _LIGHT_BG, "paddingAll": "lg",
            "contents": body,
        },
    }
    if footer_contents:
        bubble["footer"] = {
            "type": "box", "layout": "vertical",
            "paddingAll": "md", "spacing": "xs",
            "backgroundColor": _LIGHT_BG,
            "contents": footer_contents,
        }
    return _wrap(bubble, alt=f"📊 持倉概覽（{len(rows)} 檔）")


def typhoon_alert_flex(name, time, location, pressure, wind, gust, moving_dir, moving):
    rows = [
        ("📅 觀測時間", time),
        ("📍 位置", location),
        ("🔻 中心氣壓", f"{pressure} hPa"),
        ("💨 近中心風速", f"{wind} m/s"),
        ("🌪️ 陣風", f"{gust} m/s"),
        ("➡️ 移動", f"{moving_dir} {moving} km/h"),
    ]
    body = []
    for label, value in rows:
        body.append({
            "type": "box", "layout": "horizontal", "spacing": "md",
            "contents": [
                {"type": "text", "text": label,
                 "size": "sm", "color": _TEXT_LIGHT, "flex": 4},
                {"type": "text", "text": str(value),
                 "size": "sm", "color": _TEXT_DARK,
                 "weight": "bold", "flex": 5, "wrap": True},
            ],
        })

    bubble = {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": _ORANGE, "paddingAll": "lg",
            "contents": [
                {"type": "text", "text": "🌀 颱風警報",
                 "color": "#FFFFFF", "weight": "bold", "size": "lg"},
                {"type": "text", "text": str(name),
                 "color": "#FFFFFF", "size": "xl",
                 "weight": "bold", "margin": "xs"},
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md",
            "backgroundColor": _LIGHT_BG, "paddingAll": "lg",
            "contents": body,
        },
        "footer": {
            "type": "box", "layout": "vertical", "paddingAll": "sm",
            "contents": [{
                "type": "button",
                "style": "link", "height": "sm",
                "action": {
                    "type": "uri",
                    "label": "CWA 颱風專區",
                    "uri": "https://www.cwa.gov.tw/V8/C/P/Typhoon/Typhoon.html",
                },
            }],
        },
    }
    return _wrap(bubble, alt=f"🌀 颱風警報 {name}")
