"""
LINE Flex Message 卡片產生器。

提供：
- todo_list_flex(items)         待辦清單卡片（每筆一行 + 完成 postback 按鈕）
- reminder_list_flex(items)     提醒清單卡片（每筆 + 取消 / 延後 30 分按鈕）
- typhoon_alert_flex(...)       颱風警報卡片（informational only）

postback data 採 urlencoded query string，格式：
- action=todo_complete&id=5
- action=reminder_cancel&id=3
- action=reminder_snooze&id=3&min=30

回傳格式：LINE Messaging API 的 message dict
  {"type": "flex", "altText": "...", "contents": {bubble dict}}

altText 必填，是通知列 / 不支援 Flex 客戶端的 fallback。
"""

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
