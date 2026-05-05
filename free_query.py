"""
自由格式查詢：使用者打 / 開頭、解析不到結構化指令的中文問題，
全部交給 Sonnet + web_search 自由發揮。
"""

import os
import anthropic
import usage_tracker


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
        f"這是金融市場相關問題（台股 / 美股 / ETF / 基金 / 原物料 / 加密貨幣 / 匯率皆可能）。\n"
        f"請主動用 web_search 多查 2-4 次，蒐集具體數據後再回答（繁體中文、純文字、不要 Markdown）。\n\n"
        f"如果 query 看起來是某檔商品名稱（例：「台銀金」「00679B」「比特幣」「金價」「黃金存摺」），\n"
        f"請主動補齊以下資訊（找得到的全部寫上，找不到的省略，禁止編造）：\n"
        f"  • 全名與代號（若是 ETF / 基金 / 股票 / 期貨 / 黃金存摺，註明發行 / 交易所）\n"
        f"  • 最新價格（含日期）、當日漲跌幅、近 1 週 / 1 月變化\n"
        f"  • 主要成分 / 投資標的 / 對應原物料\n"
        f"  • 規模 / 殖利率 / 配息（若為 ETF / 基金）\n"
        f"  • 近期重要新聞或催化（1-3 點，要有日期）\n\n"
        f"如果 query 是開放式問題（例：「Fed 最新動向」「半導體輪動」），\n"
        f"則用 5-8 條 bullet 回答，每點要有日期 / 數字 / 來源。\n\n"
        f"嚴格規則：\n"
        f"- 必須包含具體數字、日期、來源網站\n"
        f"- 找不到資料就明說「找不到相關資料」，不要編造\n"
        f"- 禁止泛泛廢話（「值得關注」「市場將觀察」「投資人應留意」「資產配置工具」等空話一律砍）\n"
        f"- 回答長度約 150-400 字"
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1800,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 5,
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        usage_tracker.track("claude-sonnet-4-5", msg)
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
