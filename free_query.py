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
    """自由 query → Sonnet + web_search；失敗回錯誤訊息。
    分兩種 query 結構：商品名稱型 = 完整檔案；議題型 = 多面向 bullet。"""
    prompt = f"""你是專業金融市場分析師。使用者問：「{query}」

請用 web_search 主動查 4-6 次（不要省搜尋次數），從多個來源蒐集 24-72 小時內最新資料再回答。

判斷 query 屬於哪一型，套對應結構：

═══════════════════════════════
A 型｜商品名稱（含 ETF / 個股 / 基金 / 期貨 / 加密貨幣 / 黃金存摺 / 外匯）
═══════════════════════════════
第一行：📌 全名 (代號 / 發行機構 / 對應標的)

接著用以下 6 塊，每塊 2-4 行，找不到的整塊省略不寫（不要寫「無資料」佔版面）。每筆數據都要附日期與來源：

1️⃣ 行情
   最新價、當日漲跌（%）、近 1 週 / 1 月 / YTD 表現、近 5 日均量

2️⃣ 成分／投資標的
   ETF/基金 → 前 5 大持股（含權重 %）
   原物料/期貨 → 對應底層商品 + 主要驅動因子
   個股 → 主要產品線/客戶/營收占比

3️⃣ 規模與配息（ETF/基金才寫，否則略過）
   規模、近 12 月配息、殖利率、費用率、追蹤誤差

4️⃣ 近期催化（3-5 點）
   法說 / 財報 / 併購 / 減資 / 產業大事 / 政策利多空，每點 1 句 + 日期

5️⃣ 風險與觀察點（2-3 點）
   技術面壓力支撐位、籌碼變化、產業競爭、政策風險

6️⃣ 同業／對照（1-2 個）
   關聯標的或競品近期表現對比（含具體數字）

═══════════════════════════════
B 型｜開放議題（含 Fed / 總經 / 類股輪動 / 政策 / 地緣 / 個股事件）
═══════════════════════════════
用 6-10 條 bullet（• 開頭），每點：
- 必含「日期 + 具體數字 + 來源/機構名」
- 短中長期分散（近 1 週已發生事件、本月關鍵數據、未來 1-2 個月將觀察點）
- 若市場有分歧，明確列正反方觀點與機構代表
- 涉及台股 → 含類股漲跌幅 / 權值股動態 / 三大法人籌碼
- 涉及美股 → 含 NVDA/TSM 等龍頭股動態（如果相關）

═══════════════════════════════
通用規則（嚴格遵守）
═══════════════════════════════
- 純文字繁體中文，禁止任何 Markdown（## ** * ``` ─ 等）
- 禁止任何開場白：「以下是」「我為您整理」「您好」「根據查詢」
- 禁止任何結語 / 收尾語：「簡言之」「綜合而言」「總結來說」「希望對你有幫助」「※」「以上資訊僅供參考」「投資人應留意」「值得關注」「市場將觀察」「資產配置工具」
- 禁止編造找不到的具體數字 / 日期
- 直接從 A 型的「📌」或 B 型的「• 」開始
- 數字 → 附日期；事件 → 附來源
- 找不到的整塊省略，不要寫「N/A」「資料尚待補充」
"""
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2500,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 6,
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
        return text
    except Exception as e:
        return f"AI 查詢失敗：{e}"
