"""
自由格式查詢：使用者打 / 開頭、解析不到結構化指令的中文問題，
全部交給 Sonnet + web_search 自由發揮。
"""

import os
import anthropic


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")


def answer(query):
    """自由 query → Sonnet + web_search；失敗回錯誤訊息。"""
    prompt = (
        f"使用者問：「{query}」\n\n"
        f"這是台股或美股相關問題。請用網路搜尋給簡潔具體的繁體中文回答。\n\n"
        f"嚴格規則：\n"
        f"- 必須包含具體數字、日期、來源網站\n"
        f"- 純文字、不要 Markdown\n"
        f"- 找不到資料就明說「找不到相關資料」，不要編造\n"
        f"- 禁止泛泛廢話（避免「值得關注」「市場將觀察」「投資人應留意」等空話）\n"
        f"- 回答長度約 100-300 字"
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 4,
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                text = block.text
        text = text.strip()
        if not text:
            return "找不到相關資料"
        return f"<b>🤖 AI 回答</b>（{query[:30]}...）\n\n{text}\n\n<i>※ AI 自由回答，僅供參考</i>"
    except Exception as e:
        return f"AI 查詢失敗：{e}"
