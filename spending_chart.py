"""本月消費的類別彙總 + 圓餅圖。

為什麼獨立成檔而不是塞進 finance_report:那個模組被 command_router
每次處理 LINE 指令時都會 import,加進 matplotlib 等於每一則訊息都拖著
繪圖函式庫。相依方向必須是 spending_chart → finance_report。

matplotlib 用 Agg backend(無視窗環境),中文字型沿用 weather.py 那套
載入邏輯 —— 那個坑已經踩平了,不要再寫第二份。
"""

from collections import defaultdict

# 跟 finance_report 共用同一套「什麼算支出」的判斷 —— 各寫一份遲早會漂移
# （mailer.py 借用 line_sender._strip_html 是同樣的取捨）
from finance_report import _currency, _is_spending

OTHER = "其他"

# 圓餅圖畫幾片。類別總共有 14 種（notion_db._SPEND_CATEGORIES），
# 全畫是色票不是圖表。
TOP_N = 6


def summarize(txns, month, top_n=TOP_N):
    """當月 TWD 支出按類別彙總,回 [(類別, 金額)] 由大到小。

    第 top_n 名之後併成「其他」,而且「其他」永遠排最後 —— 它是分類
    殘渣,不該在排序上跟真類別競爭。

    回 [] 代表當月沒有任何 TWD 支出。
    """
    totals = defaultdict(float)
    for t in txns or []:
        if not (t.get("date") or "").startswith(month):
            continue
        if not _is_spending(t) or _currency(t) != "TWD":
            continue
        totals[t.get("category") or OTHER] += t.get("amount") or 0

    if not totals:
        return []

    tail = totals.pop(OTHER, 0)
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)

    head = ranked[:top_n]
    tail += sum(amount for _, amount in ranked[top_n:])

    out = [(name, _round(amount)) for name, amount in head]
    if tail:
        out.append((OTHER, _round(tail)))
    return out


def _round(n):
    """金額累加後可能出現浮點尾巴。整數就回 int,顯示與比較都乾淨。"""
    return int(n) if float(n) == int(n) else round(n, 2)
