"""每日個人報的卡片版型。

版型來自使用者提供的 digest_preview.html（2026-08-26）：米色底 + 白卡 +
圓角 + 棕色標題。使用者指定的區塊順序是 **待辦 → 財務 → 買菜**，
跟範本原本的順序不同 —— 順序由呼叫端決定，這裡只負責照單渲染。

內容一律當純文字處理再 escape。卡片裡會出現商家名、食材名這類來自
Notion 與信件解析的字串，含 & 或 < 會把版面弄壞（「全家 & Co.」就夠了，
不用到惡意輸入）。escape 之後才把換行轉成 <br>，排版保留、結構不受內容影響。

escape 之後才做的「掃讀強化」（v2 2026-09-04）：
- <b>…</b>（來自 format_todos / format_reminders）還原成粗體，
  不然 email 會看到字面上的「<b>」（HTML 版本原本沒還原，是個舊 bug）。
- 金額（NT$ / US$ / 三碼幣別）上色加粗 —— 一封信裡「花多少」最該一眼看到。
- 冰箱卡的到期字樣（已過期 / 今天到期 / 剩 N 天）標紅，急的東西自己跳出來。
金額與到期字樣都不含 < > &，在 escape 後的字串上用 regex 安全（不會咬到標籤）。

email client 不吃 <style> 區塊也不吃 class，所以樣式全部 inline ——
這是 HTML email 的常態限制，不是懶。跨欄對齊也只能靠 <table>，flex 在
Gmail/Outlook 都不穩，所以摘要列走 table。
"""

import re
from html import escape

_BG = "#f5f2ec"
_CARD_BORDER = "#ececec"
_HEADING = "#5b4636"
_TEXT = "#333"
_FOOTER = "#aaa"

# 金額：暖琥珀，跟米色底同調又夠跳（「這是一個數字，看這裡」）
_MONEY = "#b26b1e"
# 到期紅：只用在冰箱卡，急件自己跳出來
_EXPIRE = "#c0392b"

# 每張卡的左側色條 —— 用標題裡的 emoji 判斷屬於哪一類。
# 找不到對應就用中性米棕，不會突兀。
_ACCENT_DEFAULT = "#cdbfa9"
_ACCENTS = [
    (("📋",), "#a97b50"),          # 待辦
    (("⏰",), "#d1972f"),          # 提醒
    (("💳", "🧾", "💰"), "#3a6ea5"),  # 財務 / 消費
    (("🍳", "🥬", "🧊"), "#7d9a4f"),  # 冰箱 / 食材
    (("🌤️", "🌦️", "☀️", "🌧️", "⛅"), "#5a9bd4"),  # 天氣
]

_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "'Helvetica Neue',Arial,'PingFang TC','Microsoft JhengHei',sans-serif"
)

_CARD_TITLE_STYLE = (
    f"font-size:16px;font-weight:700;color:{_HEADING};margin:0 0 10px;"
)
_CARD_BODY_STYLE = f"font-size:14px;line-height:1.7;color:{_TEXT};"

# NT$3,450 / US$30 / USD 1,200 —— 貨幣符號在前或三碼幣別加空白在前。
# 後面接數字（可含千分位與小數）。CJK 不會被 [A-Z]{3} 咬到。
_MONEY_RE = re.compile(r"(?:NT\$|US\$|[A-Z]{3}\s)[\d,]+(?:\.\d+)?")
# 到期字樣（見 kitchen._days_text）
_EXPIRE_RE = re.compile(r"已過期\s*\d+\s*天|今天到期|剩\s*\d+\s*天")


def _card_accent(title):
    for keys, color in _ACCENTS:
        if any(k in title for k in keys):
            return color
    return _ACCENT_DEFAULT


def _is_kitchen(title):
    return any(k in title for k in ("🍳", "🥬", "🧊", "冰箱", "過期", "食材"))


def _emphasize(html_text, kitchen=False):
    """在「已 escape」的字串上做掃讀強化。順序有意義：
    先還原 <b>，再上金額色，最後（只在冰箱卡）標到期紅 —— 各步驟插進去的
    標籤/色碼不會被後一步的 regex 咬到（<b> 無數字、色碼是 # 開頭非 CJK）。"""
    out = html_text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    out = _MONEY_RE.sub(
        lambda m: f'<span style="color:{_MONEY};font-weight:600">{m.group(0)}</span>',
        out,
    )
    if kitchen:
        out = _EXPIRE_RE.sub(
            lambda m: f'<span style="color:{_EXPIRE};font-weight:600">{m.group(0)}</span>',
            out,
        )
    return out


def _as_html(text):
    """純文字 → 安全的 HTML 片段（escape 後換行轉 <br>）。標題等不需強化的用這個。"""
    return escape(str(text)).replace("\n", "<br>")


def _body_html(text, kitchen=False):
    """卡片內文：escape → 強化（金額/到期/粗體）→ 換行轉 <br>。"""
    return _emphasize(escape(str(text)), kitchen=kitchen).replace("\n", "<br>")


def _summary_card(tiles):
    """置頂摘要列：幾筆待辦、本月花多少、幾樣要過期 —— 一眼看懂。

    tiles: [(emoji, 大字值, 小字標籤, 顏色)]。空的話回空字串（不放空卡）。
    等寬置中，用 <table> 而不是 flex（Gmail/Outlook 對 flex 不穩）。
    """
    if not tiles:
        return ""
    width = f"{100 // len(tiles)}%"
    cells = []
    for emoji, value, label, color in tiles:
        cells.append(
            f'<td width="{width}" align="center" '
            f'style="padding:6px 4px;vertical-align:top">'
            f'<div style="font-size:19px;line-height:1.2">{_as_html(emoji)}</div>'
            f'<div style="font-size:19px;font-weight:800;color:{color};'
            f'padding:2px 0 1px">{_as_html(value)}</div>'
            f'<div style="font-size:11px;color:{_FOOTER}">{_as_html(label)}</div>'
            f'</td>'
        )
    return (
        f'<div style="background:#ffffff;border:1px solid {_CARD_BORDER};'
        f'border-radius:12px;padding:14px 10px;margin:0 0 16px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse"><tr>{"".join(cells)}</tr></table>'
        f'</div>'
    )


def build_digest_html(date_str, blocks, summary=None):
    """blocks: [(標題, 內容純文字)]，照給的順序渲染。
    summary: [(emoji, 值, 標籤, 顏色)]，置頂摘要列；None 或空就不放。

    內容是空的區塊直接不出現 —— 留一張空卡片比沒有還糟。
    全部都空回 None,呼叫端據此決定不寄信（不要寄一封只有標題的信）。
    """
    cards = []
    for title, body in blocks or []:
        if not body:
            continue
        accent = _card_accent(title)
        card_style = (
            f"background:#ffffff;border:1px solid {_CARD_BORDER};"
            f"border-left:4px solid {accent};border-radius:12px;"
            "padding:16px 18px;margin:0 0 16px;"
        )
        cards.append(
            f'<div style="{card_style}">'
            f'<div style="{_CARD_TITLE_STYLE}">{_as_html(title)}</div>'
            f'<div style="{_CARD_BODY_STYLE}">{_body_html(body, _is_kitchen(title))}</div>'
            f'</div>'
        )

    if not cards:
        return None

    header = (
        f'<div style="font-size:20px;font-weight:800;color:{_HEADING};'
        f'padding:4px 2px 16px;">🌅 早安 · {_as_html(date_str)}</div>'
    )
    footer = (
        f'<div style="font-size:12px;color:{_FOOTER};text-align:center;'
        f'padding:8px 2px 0;">ReportRobot · 每日自動寄送</div>'
    )
    return (
        f'<div style="margin:0;padding:20px;background:{_BG};font-family:{_FONT};">'
        f'<div style="max-width:600px;margin:0 auto;">'
        f'{header}{_summary_card(summary)}{"".join(cards)}{footer}'
        f'</div></div>'
    )
