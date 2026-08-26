"""每日個人報的卡片版型。

版型來自使用者提供的 digest_preview.html（2026-08-26）：米色底 + 白卡 +
圓角 + 棕色標題。使用者指定的區塊順序是 **待辦 → 財務 → 買菜**，
跟範本原本的順序不同 —— 順序由呼叫端決定，這裡只負責照單渲染。

內容一律當純文字處理再 escape。卡片裡會出現商家名、食材名這類來自
Notion 與信件解析的字串，含 & 或 < 會把版面弄壞（「全家 & Co.」就夠了，
不用到惡意輸入）。escape 之後才把換行轉成 <br>，排版保留、結構不受內容影響。

email client 不吃 <style> 區塊也不吃 class，所以樣式全部 inline ——
這是 HTML email 的常態限制，不是懶。
"""

from html import escape

_BG = "#f5f2ec"
_CARD_BORDER = "#ececec"
_HEADING = "#5b4636"
_TEXT = "#333"
_FOOTER = "#aaa"

_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "'Helvetica Neue',Arial,'PingFang TC','Microsoft JhengHei',sans-serif"
)

_CARD_STYLE = (
    f"background:#ffffff;border:1px solid {_CARD_BORDER};"
    "border-radius:12px;padding:16px 18px;margin:0 0 16px;"
)
_CARD_TITLE_STYLE = (
    f"font-size:16px;font-weight:700;color:{_HEADING};margin:0 0 10px;"
)
_CARD_BODY_STYLE = f"font-size:14px;line-height:1.7;color:{_TEXT};"


def _as_html(text):
    """純文字 → 安全的 HTML 片段（escape 後換行轉 <br>）。"""
    return escape(str(text)).replace("\n", "<br>")


def build_digest_html(date_str, blocks):
    """blocks: [(標題, 內容純文字)]，照給的順序渲染。

    內容是空的區塊直接不出現 —— 留一張空卡片比沒有還糟。
    全部都空回 None,呼叫端據此決定不寄信（不要寄一封只有標題的信）。
    """
    cards = []
    for title, body in blocks or []:
        if not body:
            continue
        cards.append(
            f'<div style="{_CARD_STYLE}">'
            f'<div style="{_CARD_TITLE_STYLE}">{_as_html(title)}</div>'
            f'<div style="{_CARD_BODY_STYLE}">{_as_html(body)}</div>'
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
        f'{header}{"".join(cards)}{footer}'
        f'</div></div>'
    )
