"""本月消費的類別彙總 + 圓餅圖。

為什麼獨立成檔而不是塞進 finance_report:那個模組被 command_router
每次處理 LINE 指令時都會 import,加進 matplotlib 等於每一則訊息都拖著
繪圖函式庫。相依方向必須是 spending_chart → finance_report。

matplotlib 用 Agg backend(無視窗環境),中文字型沿用 weather.py 那套
載入邏輯 —— 那個坑已經踩平了,不要再寫第二份。
"""

import os
import tempfile
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')  # 無視窗環境。必須在 import pyplot 之前
import matplotlib.pyplot as plt

# 跟 finance_report 共用同一套「什麼算支出」的判斷 —— 各寫一份遲早會漂移
# （mailer.py 借用 line_sender._strip_html 是同樣的取捨）
from finance_report import _currency, _is_spending, _money

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


# 版面沿用 digest.py 的米色系,信裡才不會突兀
_BG = '#f5f2ec'
_TEXT = '#5b4636'

# 手挑的暖色盤,對齊卡片版型。不用 matplotlib 預設(那組偏冷、
# 相鄰兩片在手機上分不出來)
_COLORS = ['#c96f4a', '#d9a05b', '#8fa87d', '#6d8fa8',
           '#9b7aa8', '#c98fa0', '#b0a89b']


def build_pie(txns, month, top_n=TOP_N):
    """畫當月消費圓餅圖。回 (png 路徑, 摘要文字);沒資料回 (None, None)。

    摘要文字是給純文字版信件用的 —— 那邊沒有圖,合計數字必須有地方活。
    """
    slices = summarize(txns, month, top_n=top_n)
    if not slices:
        return None, None

    rows = [t for t in txns or []
            if (t.get("date") or "").startswith(month)
            and _is_spending(t) and _currency(t) == "TWD"]
    total = sum(amount for _, amount in slices)

    from weather import get_chinese_font
    font = get_chinese_font()

    values = [amount for _, amount in slices]

    fig, ax = plt.subplots(figsize=(7, 5), dpi=120)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    wedges, _ = ax.pie(
        values,
        colors=_COLORS[:len(values)],
        startangle=90,
        counterclock=False,
        wedgeprops={'edgecolor': _BG, 'linewidth': 2},
    )

    # 百分比與金額放圖例而不是圖上:類別名是中文,標在小片上會疊在一起
    legend_labels = [
        f"{name}　NT${_money(amount)}（{amount / total * 100:.0f}%）"
        for name, amount in slices
    ]
    ax.legend(
        wedges, legend_labels,
        loc='center left', bbox_to_anchor=(1.0, 0.5),
        frameon=False, prop=font, labelcolor=_TEXT,
    )
    ax.set_aspect('equal')

    chart_path = os.path.join(tempfile.gettempdir(), 'spending_pie.png')
    plt.tight_layout()
    plt.savefig(chart_path, facecolor=_BG, bbox_inches='tight')
    plt.close(fig)

    summary = f"本月合計 NT${_money(total)}（共 {len(rows)} 筆）"
    return chart_path, summary
