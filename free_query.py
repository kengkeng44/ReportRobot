"""
自由格式查詢：使用者打 / 開頭、解析不到結構化指令的中文問題，
全部交給 Sonnet + web_search 自由發揮。

Prompt 架構參考機構級 IC Memo + macro transmission framework：
- A 型：商品 / 個股「現況快照」
- B 型：開放議題「多面向 bullet」
- C 型：「未來展望／價格預測」用 bull/base/bear 三情境機率加權 + 機構共識
        + 觀察點 + 假設列表，避免只回歷史最高價式的偷懶答案。
"""

import os
import re
import anthropic
import usage_tracker


# 觸發詳細版的關鍵字（含中英文）。Query 含其中之一 → 走詳細版 6 塊；
# 否則走精簡版（預設）。
_DETAIL_KEYWORDS = (
    "詳細", "完整", "深入", "深度", "詳盡", "full", "detail", "report", "分析報告",
)


def _is_detail_request(query):
    q = query.lower()
    for kw in _DETAIL_KEYWORDS:
        if kw in q:
            return True
    return False


def _strip_detail_keywords(query):
    """把觸發詞從 query 移除（讓 prompt 看到的是乾淨主題）。"""
    out = query
    for kw in _DETAIL_KEYWORDS:
        out = re.sub(re.escape(kw), "", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()


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


_CONCISE_PROMPT = """你是金融市場分析師。使用者問：「{q}」

請用 web_search 查 2-3 次拿關鍵數據後，給「精簡版」回答。

格式（純文字繁體中文，不要 Markdown）：
1. 第一行：📌 主題 + 一句重點（含最關鍵的數字 + 日期）
2. 接著 3-5 條 bullet（• 開頭），每點 1 句、含具體數字 / 日期 / 來源
3. 若是預測類 query，bullet 第 1 條寫共識方向 + 區間（不寫機率），
   不必跑完整 bull/base/bear 6 塊；機構代表 1-2 家即可
4. 最後一行：「💡 想看完整版分析請輸入：/詳細 {q_clean}」

長度上限：12 行內、200-350 字。

絕對禁止：
- Markdown（## ** * ``` ─ 等）
- 開場白：「以下是」「您好」「我為您整理」「根據查詢」
- 結語：「簡言之」「綜合而言」「總結來說」「希望對你有幫助」「※」
        「投資人應留意」「值得關注」「市場將觀察」「資產配置工具」
        「不構成投資建議」
- 編造找不到的具體數字 / 日期 / 機構名
"""


def answer(query):
    """自由 query → Sonnet + web_search；失敗回錯誤訊息。
    預設走精簡版；query 含「詳細/完整/深入/分析報告/full/detail」走詳細版。"""
    detailed = _is_detail_request(query)
    clean_query = _strip_detail_keywords(query) if detailed else query

    if not detailed:
        prompt = _CONCISE_PROMPT.format(q=query, q_clean=query)
        return _call_claude(prompt, max_tokens=1500, max_uses=3, label="concise")

    prompt = f"""你是首席投資策略師（Chief Investment Strategist），CFA 等級分析。
使用者問：「{clean_query}」

⚠️ 絕對要求：
- 必須完整輸出對應結構的全部段落（A 型 6 塊 / B 型 6-10 條 / C 型 6 塊），少一塊視為錯誤
- 不可寫到一半就停（例如只寫「技術面：金價 4/24 失守...」就結束 → 視為失敗）
- 預測類 query → 強制走 C 型 6 塊，技術面只是其中第 1 塊的一部分，不可代表整體回答
- 先想好 6 塊各要寫什麼，確認 token 預算夠 → 再開始輸出；每塊至少 50 字
- 寧可 web_search 少查 1-2 次，也要把全部 6 塊寫完整

先用 web_search 查 4-5 次（聚焦於最重要的資料源，不要花在無關搜尋），蒐集 24-72 小時內資料 + 主要券商/機構最新報告，再開始回答。
回答前先判斷 query 屬於哪一型，套對應結構：

═══════════════════════════════
A 型｜商品名稱現況（ETF / 個股 / 基金 / 期貨 / 加密貨幣 / 黃金存摺 / 外匯）
═══════════════════════════════
第一行：📌 全名 (代號 / 發行機構 / 對應標的)
然後 6 塊（找不到整塊省略，不要寫「無資料」佔版面）：

1️⃣ 行情：最新價、當日漲跌%、近 1 週 / 1 月 / YTD、近 5 日均量
2️⃣ 成分／標的：ETF→前 5 大持股+權重；原物料→驅動因子；個股→產品線/客戶/營收占比
3️⃣ 規模與配息（ETF/基金）：規模、近 12 月配息、殖利率、費用率、追蹤誤差
4️⃣ 近期催化（3-5 點）：每點 1 句 + 日期
5️⃣ 風險／觀察點（2-3 點）：技術面壓力支撐、籌碼、產業競爭、政策
6️⃣ 同業／對照（1-2 個）：含具體數字比較

═══════════════════════════════
B 型｜開放議題（Fed / 總經 / 類股輪動 / 政策 / 地緣 / 個股事件）
═══════════════════════════════
6-10 條 bullet（• 開頭），每點：
- 必含「日期 + 具體數字 + 來源/機構」
- 短中長期分散（近 1 週已發生 / 本月關鍵數據 / 未來 1-2 個月觀察）
- 市場分歧 → 明確列正反方 + 代表機構名
- 涉台股 → 含類股漲跌、權值股、三大法人；涉美股 → 龍頭股動態

═══════════════════════════════
C 型｜未來展望／價格預測／趨勢預測（query 含「未來」「預測」「展望」「會不會」
「明年」「2026」「2027」「目標價」「上看」「下看」「漲到」「跌到」等關鍵字）
═══════════════════════════════
第一行：📌 對象 + 預測時間範圍（如「黃金 / 未來 6-12 個月」）

1️⃣ 當前狀態（2-3 行）
   最新價、近 30 日 / YTD 表現、技術面位置（壓力位 / 支撐位 / 均線排列）

2️⃣ 三大驅動因子（每個 2-3 行，要說清楚因果鏈與量化關係）
   • 因子 A：當前數值、歷史相關性、對價格的傳導機制
   • 因子 B：同上
   • 因子 C：同上
   （黃金常用因子：實質利率、美元指數、央行購金、地緣風險、ETF 持倉、通膨預期）

3️⃣ 三情境分析（最重要的一塊，務必完整）
   🐂 Bull case｜機率 X%
       目標價區間：US$NNNN-NNNN
       觸發條件：1-2 個具體事件（如 Fed 降息 50bp、美元跌破 95）
       時間：何時可能發生
   ⚖️ Base case｜機率 X%
       目標價區間：US$NNNN-NNNN
       核心假設：1-2 個（延續現況的前提）
   🐻 Bear case｜機率 X%
       目標價區間：US$NNNN-NNNN
       觸發條件：1-2 個具體事件
       時間：何時可能發生
   ⚠️ 三機率合計 = 100%。每個情境的目標價必須是區間，不是單點。

4️⃣ 主要機構預測（蒐集 4-6 家券商的最新目標價）
   機構 (報告日期)：目標價 + 一句立論
   範例：Goldman Sachs (2026/04/15)：年底目標 US$5,400，看升因央行購金與降息週期延長
   黃金的話至少包含 Goldman Sachs / J.P. Morgan / Wells Fargo / Citi / UBS / World Gold Council 中的 4 家

5️⃣ 關鍵觀察點（未來 1-3 個月會驗證 / 推翻論點的事件）
   3-5 個事件，每個含日期 + 為何重要

6️⃣ 假設列表（這份預測建立在哪些 assumptions 上）
   3-4 條，例如「假設 Fed 在未來 6 個月降息 75bp」「假設地緣衝突無重大升級」
   此區允許讀者自己判斷預測是否仍 valid

═══════════════════════════════
通用規則（嚴格遵守）
═══════════════════════════════
- 純文字繁體中文，禁止任何 Markdown（## ** * ``` ─ 等）
- 禁止任何開場白：「以下是」「我為您整理」「您好」「根據查詢」
- 禁止任何結語/收尾語：「簡言之」「綜合而言」「總結來說」「希望對你有幫助」「※」「以上資訊僅供參考」「投資人應留意」「值得關注」「市場將觀察」「資產配置工具」「不構成投資建議」
- 禁止編造找不到的具體數字 / 日期 / 機構名
- 預測 query 不可只回歷史最高價就結束 — 必須完成 C 型 6 塊
- 直接從第一行內容開始（A/C 型的「📌」或 B 型的「• 」）
- 數字 → 附日期；事件 → 附來源；預測 → 附情境機率
- 找不到的整塊省略，不要寫「N/A」「資料尚待補充」
"""
    return _call_claude(prompt, max_tokens=4096, max_uses=5, label="detailed")


def _call_claude(prompt, max_tokens, max_uses, label):
    """共用：打 Claude API、收集所有 text block、log stop_reason。"""
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=max_tokens,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": max_uses,
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        usage_tracker.track("claude-sonnet-4-5", msg)
        # 蒐集所有 text block（web_search 之間會穿插多個 text，必須全部收）
        texts = []
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                texts.append(block.text)
        text = "\n\n".join(t for t in texts if t and t.strip()).strip()
        print(f"[free_query/{label}] stop_reason={msg.stop_reason} "
              f"text_blocks={len(texts)} chars={len(text)} "
              f"input={msg.usage.input_tokens} output={msg.usage.output_tokens}")
        if msg.stop_reason == "max_tokens":
            text += "\n\n（⚠️ 回答被 token 上限截斷，可再問一次更窄的問題）"
        if not text:
            return "找不到相關資料"
        return text
    except Exception as e:
        return f"AI 查詢失敗：{e}"
