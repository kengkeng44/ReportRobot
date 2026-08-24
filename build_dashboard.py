"""把 Notion 財務資料轉成一頁靜態 HTML 儀表板。

用法：
    infisical run -- python build_dashboard.py            # 抓 Notion 產出 dashboard.html
    infisical run -- python build_dashboard.py --dump-json data.json
    python build_dashboard.py --from-json data.json       # 離線重畫，不碰 Notion

為什麼是靜態檔而不是 Railway 路由：省掉一整套存取控制，而且不替財務資料
新增對外的網路入口。要更新就重跑一次。

刻意拆成 collect / compute / render 三段：
- collect  只碰 Notion，唯一需要金鑰的部分
- compute  純函式，輸入 dict 輸出 dict
- render   純函式，輸入 dict 輸出 HTML 字串
後兩段不用連線就能測，也就真的有測（見 tests/test_build_dashboard.py）。
"""

import argparse
import html
import json
import sys
from datetime import date, datetime


OUTPUT_DEFAULT = "dashboard.html"

# dataviz 參考配色，兩個模式都跑過 validate_palette.js（全 PASS）。
# 每張圖都只有一個資料序列，所以不需要類別色盤，也就沒有色盲配對的問題 ——
# 類別的身分由座標軸標籤承載，不是由顏色承載。
_C = {
    "series_l": "#2a78d6", "series_d": "#3987e5",
    "accent_l": "#eb6834", "accent_d": "#d95926",
    "good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b",
}


# ─────────────────────────────────────────────────────────
# collect：唯一需要 Notion 金鑰的部分
# ─────────────────────────────────────────────────────────

def collect(notion=None, txn_limit=500, networth_limit=90):
    """從 Notion 抓原始資料。回可直接 json.dump 的 dict。"""
    if notion is None:
        import notion_db as notion

    if not notion.is_configured():
        raise SystemExit(
            "缺少 NOTION_TOKEN / NOTION_PARENT_PAGE_ID。\n"
            "金鑰在 Infisical，用這行跑：\n"
            "    infisical run -- python build_dashboard.py"
        )

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "transactions": notion.transactions_load(limit=txn_limit),
        "networth": notion.networth_load(limit=networth_limit),
        "holdings": notion.holdings_load(),
    }


# ─────────────────────────────────────────────────────────
# compute：純函式
# ─────────────────────────────────────────────────────────

def _month_of(iso_day):
    return (iso_day or "")[:7]


def compute(raw, today=None):
    """把原始資料算成畫面要的統計。today 可注入，方便測試。"""
    today = today or date.today()
    txns = raw.get("transactions") or []
    networth = raw.get("networth") or []
    holdings = raw.get("holdings") or []

    # 只有「支出」進金額統計。轉帳與還款是錢在自己口袋間移動，
    # 混進來會讓本月支出憑空膨脹一倍。
    spend = [t for t in txns
             if (t.get("direction") or "支出") == "支出" and t.get("amount")]

    this_month = today.strftime("%Y-%m")
    month_spend = [t for t in spend if _month_of(t.get("date")) == this_month]

    by_category = {}
    for t in month_spend:
        key = t.get("category") or "未分類"
        by_category[key] = by_category.get(key, 0) + t["amount"]
    categories = sorted(by_category.items(), key=lambda kv: -kv[1])

    by_day = {}
    for t in month_spend:
        if t.get("date"):
            by_day[t["date"]] = by_day.get(t["date"], 0) + t["amount"]
    daily = sorted(by_day.items())

    latest_txn = max((t["date"] for t in txns if t.get("date")), default=None)
    stale_days = None
    if latest_txn:
        try:
            stale_days = (today - date.fromisoformat(latest_txn)).days
        except ValueError:
            stale_days = None

    holdings_value = sum(h["value"] for h in holdings if h.get("value"))

    # 來源分佈：看得出同步管線還活著，以及哪些來源其實從沒用過
    by_source = {}
    for t in txns:
        key = t.get("source") or "未標記"
        by_source[key] = by_source.get(key, 0) + 1

    return {
        "generated_at": raw.get("generated_at"),
        "today": today.isoformat(),
        "month": this_month,
        "month_total": sum(t["amount"] for t in month_spend),
        "month_count": len(month_spend),
        "all_count": len(txns),
        "latest_txn": latest_txn,
        "stale_days": stale_days,
        "categories": categories,
        "daily": daily,
        "networth": [n for n in networth if n.get("net") is not None],
        "latest_net": (networth[-1]["net"] if networth and
                       networth[-1].get("net") is not None else None),
        "holdings": holdings,
        "holdings_value": holdings_value,
        "sources": sorted(by_source.items(), key=lambda kv: -kv[1]),
        "recent": txns[:15],
    }


# ─────────────────────────────────────────────────────────
# render：純函式，產出單檔 HTML
# ─────────────────────────────────────────────────────────

def _money(v, sign=False):
    if v is None:
        return "—"
    s = f"{abs(round(v)):,}"
    if sign:
        return ("+" if v >= 0 else "−") + s
    return ("−" if v < 0 else "") + s


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _bar_rows(categories, total):
    """類別佔比：橫向長條。

    14 個類別用圓餅／甜甜圈會擠成一團且無法排序比較，橫條可以直接讀出
    名次與差距。單一序列所以全部同色 —— 身分由左側標籤承載。
    每根都直接標數字，不必再做 tooltip 才看得到值。
    """
    if not categories:
        return '<p class="empty">本月沒有支出資料</p>'
    top = max(v for _, v in categories) or 1
    out = ['<div class="bars">']
    for name, value in categories:
        pct = (value / total * 100) if total else 0
        width = value / top * 100
        out.append(
            '<div class="bar-row" tabindex="0" '
            f'aria-label="{_e(name)} {_money(value)} 元，佔 {pct:.0f}%">'
            f'<div class="bar-label">{_e(name)}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{width:.2f}%"></div></div>'
            f'<div class="bar-value">{_money(value)}'
            f'<span class="bar-pct">{pct:.0f}%</span></div>'
            '</div>')
    out.append("</div>")
    return "".join(out)


def _daily_chart(daily):
    """每日支出：直條圖。4px 圓角資料端，錨在基線上。"""
    if not daily:
        return '<p class="empty">本月沒有支出資料</p>'
    w, h = 720, 200
    pad_b, pad_t = 26, 14
    top = max(v for _, v in daily) or 1
    n = len(daily)
    slot = w / n
    bw = min(28, max(6, slot - 4))          # 條間至少留 4px surface 間隙
    bars, labels = [], []
    for i, (day, value) in enumerate(daily):
        bh = (value / top) * (h - pad_b - pad_t)
        x = i * slot + (slot - bw) / 2
        y = h - pad_b - bh
        r = min(4, bw / 2, bh)              # bh 很小時不要讓圓角超過條身
        bars.append(
            f'<rect class="dbar" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
            f'height="{bh:.1f}" rx="{r:.1f}" '
            f'data-label="{_e(day[5:])} · NT$ {_money(value)}">'
            f'<title>{_e(day)} NT$ {_money(value)}</title></rect>')
        # 標籤只放頭尾與每第 5 根，否則日期會互相疊到看不清
        if n <= 8 or i == 0 or i == n - 1 or i % 5 == 0:
            labels.append(
                f'<text class="axis" x="{x + bw / 2:.1f}" y="{h - 8}" '
                f'text-anchor="middle">{_e(day[8:])}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
            'aria-label="本月每日支出直條圖">'
            f'<line class="baseline" x1="0" y1="{h - pad_b}" x2="{w}" y2="{h - pad_b}"/>'
            f'{"".join(bars)}{"".join(labels)}</svg>')


def _networth_chart(rows):
    """淨值走勢：折線 + 圓點。點數少，折線比面積圖誠實。"""
    if len(rows) < 2:
        return '<p class="empty">淨值快照不足 2 筆，畫不出趨勢</p>'
    w, h = 720, 210
    pad_l, pad_r, pad_b, pad_t = 12, 12, 26, 16
    values = [r["net"] for r in rows]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    iw = w - pad_l - pad_r
    ih = h - pad_b - pad_t

    def xy(i, v):
        x = pad_l + iw * i / (len(rows) - 1)
        y = pad_t + ih - (v - lo) / span * ih
        return x, y

    pts = [xy(i, v) for i, v in enumerate(values)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                    for i, (x, y) in enumerate(pts))
    dots = "".join(
        f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="4.5" '
        f'data-label="{_e(rows[i]["date"])} · NT$ {_money(values[i])}">'
        f'<title>{_e(rows[i]["date"])} NT$ {_money(values[i])}</title></circle>'
        for i, (x, y) in enumerate(pts))
    ends = (f'<text class="axis" x="{pts[0][0]:.1f}" y="{h - 8}" '
            f'text-anchor="start">{_e(rows[0]["date"][5:])}</text>'
            f'<text class="axis" x="{pts[-1][0]:.1f}" y="{h - 8}" '
            f'text-anchor="end">{_e(rows[-1]["date"][5:])}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
            'aria-label="淨值走勢折線圖">'
            f'<path class="nwline" d="{path}"/>{dots}{ends}</svg>')


def _holdings_table(rows):
    if not rows:
        return '<p class="empty">沒有持倉資料</p>'
    body = []
    for r in rows:
        pct = r.get("pnl_pct")
        cls = "flat" if pct is None else ("up" if pct >= 0 else "down")
        pct_text = ("—" if pct is None
                    else f"{'+' if pct >= 0 else chr(8722)}{abs(pct):.1f}%")
        body.append(
            f"<tr><td class='mono'>{_e(r.get('ticker'))}</td>"
            f"<td>{_e(r.get('display'))}</td>"
            f"<td class='num'>{_money(r.get('shares'))}</td>"
            f"<td class='num'>{_money(r.get('value'))}</td>"
            f"<td class='num {cls}'>{_money(r.get('pnl'), sign=True)}</td>"
            f"<td class='num {cls}'>{pct_text}</td></tr>")
    return ("<table><thead><tr><th>代號</th><th>名稱</th><th class='num'>股數</th>"
            "<th class='num'>市值</th><th class='num'>未實現損益</th>"
            "<th class='num'>報酬率</th></tr></thead><tbody>"
            + "".join(body) + "</tbody></table>")


def _recent_table(rows):
    if not rows:
        return '<p class="empty">沒有交易資料</p>'
    body = []
    for r in rows:
        body.append(
            f"<tr><td class='mono'>{_e(r.get('date'))}</td>"
            f"<td>{_e(r.get('shop'))}</td>"
            f"<td>{_e(r.get('category'))}</td>"
            f"<td class='num'>{_money(r.get('amount'))}</td>"
            f"<td><span class='chip'>{_e(r.get('status') or '—')}</span></td>"
            f"<td class='dim'>{_e(r.get('source'))}</td></tr>")
    return ("<table><thead><tr><th>日期</th><th>商店</th><th>類別</th>"
            "<th class='num'>金額</th><th>狀態</th><th>來源</th></tr></thead><tbody>"
            + "".join(body) + "</tbody></table>")


def _freshness(stale_days):
    """資料新鮮度徽章。狀態色一律搭配文字，不讓顏色單獨承載意義。"""
    if stale_days is None:
        return ("warning", "沒有交易資料")
    if stale_days <= 1:
        return ("good", f"資料是最新的（{stale_days} 天前）")
    if stale_days <= 3:
        return ("warning", f"最後一筆是 {stale_days} 天前")
    return ("critical", f"已 {stale_days} 天沒有新交易，同步可能斷了")


def render(s):
    """把 compute() 的結果畫成單檔 HTML。無外部資源請求。"""
    level, fresh_text = _freshness(s.get("stale_days"))
    total = s.get("month_total") or 0
    src = "、".join(f"{k} {v}" for k, v in (s.get("sources") or [])) or "—"
    css = _CSS.replace("__SERIES_L__", _C["series_l"]) \
              .replace("__SERIES_D__", _C["series_d"]) \
              .replace("__ACCENT_L__", _C["accent_l"]) \
              .replace("__ACCENT_D__", _C["accent_d"]) \
              .replace("__GOOD__", _C["good"]) \
              .replace("__WARNING__", _C["warning"]) \
              .replace("__CRITICAL__", _C["critical"])

    return f"""<!doctype html>
<html lang="zh-Hant" data-palette="{_C['series_l']},{_C['accent_l']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>財務儀表板 · ReportRobot</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>財務儀表板</h1>
  <p class="sub">資料來源 Notion 財務中心 · 產生於 {_e(s.get('generated_at') or s.get('today'))}</p>
  <span class="badge {level}">{_e(fresh_text)}</span>
</header>

<div class="kpis">
  <div class="kpi"><div class="k">本月支出（{_e(s.get('month'))}）</div>
    <div class="v">NT$ {_money(total)}</div>
    <div class="n">{s.get('month_count', 0)} 筆</div></div>
  <div class="kpi"><div class="k">交易總筆數</div>
    <div class="v">{s.get('all_count', 0)}</div>
    <div class="n">最後一筆 {_e(s.get('latest_txn') or '—')}</div></div>
  <div class="kpi"><div class="k">最新淨值快照</div>
    <div class="v">NT$ {_money(s.get('latest_net'))}</div>
    <div class="n">= 股票市值（見下方說明）</div></div>
  <div class="kpi"><div class="k">持倉市值</div>
    <div class="v">NT$ {_money(s.get('holdings_value'))}</div>
    <div class="n">{len(s.get('holdings') or [])} 檔</div></div>
</div>

<div class="note">
  <strong>「淨值」目前等於股票市值。</strong>
  現金餘額要靠 PDF 對帳單、信用卡未繳要靠月帳單解析，這兩個資料源都還沒接上。
  程式刻意不把它們當 0 算進去 —— 那會產生一個看起來精確、其實是錯的數字。
  另外所有交易狀態都停在「授權中」，因為國泰電子帳單的對帳 parser 還沒寫，
  海外消費結匯後的金額還會變動。
</div>

<div class="card">
  <h2>本月支出結構</h2>
  <p class="hint">依類別由多到少。單一序列所以全部同色 —— 類別身分由左側標籤承載，不靠顏色分辨。</p>
  {_bar_rows(s.get('categories') or [], total)}
</div>

<div class="card">
  <h2>本月每日支出</h2>
  <p class="hint">只計「支出」方向；轉帳與還款是錢在自己口袋間移動，不計入。</p>
  {_daily_chart(s.get('daily') or [])}
</div>

<div class="card">
  <h2>淨值走勢</h2>
  <p class="hint">共 {len(s.get('networth') or [])} 個快照，由舊到新。</p>
  {_networth_chart(s.get('networth') or [])}
</div>

<div class="card">
  <h2>持倉</h2>
  <p class="hint">依市值排序。</p>
  <div class="scroll">{_holdings_table(s.get('holdings') or [])}</div>
</div>

<div class="card">
  <h2>最近交易</h2>
  <p class="hint">最新 {len(s.get('recent') or [])} 筆。來源分佈：{_e(src)}</p>
  <div class="scroll">{_recent_table(s.get('recent') or [])}</div>
</div>

<footer>ReportRobot · 重跑 <code>infisical run -- python build_dashboard.py</code> 可更新</footer>
</div>
<div id="tip"></div>
<script>{_JS}</script>
</body>
</html>
"""


_CSS = """
:root {
  color-scheme: light;
  --surface-0:#f4f3f0; --surface-1:#fcfcfb; --border:#e2e1dc;
  --text-1:#0b0b0b; --text-2:#52514e; --text-3:#84837c;
  --series:__SERIES_L__; --accent:__ACCENT_L__;
  --good:__GOOD__; --warning:__WARNING__; --critical:__CRITICAL__;
  --warn-ink:#8a6200;
  --track:#eceae5;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-0:#111110; --surface-1:#1a1a19; --border:#2f2f2c;
    --text-1:#ffffff; --text-2:#c3c2b7; --text-3:#8b8a80;
    --series:__SERIES_D__; --accent:__ACCENT_D__;
    --warn-ink:__WARNING__;
    --track:#2a2a27;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0:#111110; --surface-1:#1a1a19; --border:#2f2f2c;
  --text-1:#ffffff; --text-2:#c3c2b7; --text-3:#8b8a80;
  --series:__SERIES_D__; --accent:__ACCENT_D__;
  --warn-ink:__WARNING__;
  --track:#2a2a27;
}
* { box-sizing:border-box; }
body {
  margin:0; padding:24px 16px 64px;
  background:var(--surface-0); color:var(--text-1);
  font-family:"Microsoft JhengHei","Noto Sans TC",-apple-system,
              BlinkMacSystemFont,"Segoe UI",sans-serif;
  line-height:1.6; -webkit-font-smoothing:antialiased;
}
.wrap { max-width:1080px; margin:0 auto; }
header { margin-bottom:20px; }
h1 { font-size:22px; margin:0 0 6px; letter-spacing:-.01em; }
.sub { color:var(--text-2); font-size:13px; margin:0; }
.badge {
  display:inline-flex; align-items:center; gap:6px;
  font-size:12px; font-weight:600; padding:4px 10px;
  border-radius:999px; border:1px solid currentColor; margin-top:10px;
}
.badge.good { color:var(--good); }
.badge.warning { color:var(--warn-ink); }
.badge.critical { color:var(--critical); }
.kpis {
  display:grid; gap:12px; margin:20px 0;
  grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
}
.kpi {
  background:var(--surface-1); border:1px solid var(--border);
  border-radius:12px; padding:16px 18px;
}
.kpi .k { font-size:12px; color:var(--text-2); margin-bottom:4px; }
.kpi .v { font-size:26px; font-weight:650; letter-spacing:-.02em;
          font-variant-numeric:tabular-nums; }
.kpi .n { font-size:12px; color:var(--text-3); margin-top:2px; }
.card {
  background:var(--surface-1); border:1px solid var(--border);
  border-radius:12px; padding:18px 20px; margin-bottom:16px;
}
.card h2 { font-size:15px; margin:0 0 2px; }
.card .hint { font-size:12px; color:var(--text-3); margin:0 0 16px; }
.bars { display:flex; flex-direction:column; gap:2px; }
.bar-row {
  display:grid; grid-template-columns:112px 1fr 116px;
  align-items:center; gap:10px; padding:3px 4px; border-radius:6px;
}
.bar-row:hover, .bar-row:focus-visible { background:var(--surface-0); outline:none; }
.bar-label { font-size:13px; color:var(--text-2);
             overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bar-track { background:var(--track); border-radius:4px; height:16px; }
.bar-fill { background:var(--series); border-radius:4px; height:16px; min-width:3px; }
.bar-value { font-size:13px; text-align:right; font-variant-numeric:tabular-nums; }
.bar-pct { color:var(--text-3); font-size:11px; margin-left:6px; }
.chart { width:100%; height:auto; display:block; overflow:visible; }
.dbar { fill:var(--series); }
.dbar:hover { fill:var(--accent); }
.nwline { fill:none; stroke:var(--series); stroke-width:2;
          stroke-linejoin:round; stroke-linecap:round; }
.dot { fill:var(--series); stroke:var(--surface-1); stroke-width:2; }
.dot:hover { fill:var(--accent); }
.axis { fill:var(--text-3); font-size:10px; }
.baseline { stroke:var(--border); stroke-width:1; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; color:var(--text-2); font-weight:600;
     padding:6px 8px; border-bottom:1px solid var(--border); font-size:12px; }
td { padding:7px 8px; border-bottom:1px solid var(--border); }
tr:last-child td { border-bottom:none; }
.num { text-align:right; font-variant-numeric:tabular-nums; }
.mono { font-variant-numeric:tabular-nums; color:var(--text-2); }
.dim { color:var(--text-3); font-size:12px; }
.up { color:var(--good); }
.down { color:var(--critical); }
.flat { color:var(--text-3); }
.chip { font-size:11px; padding:2px 8px; border-radius:999px;
        background:var(--track); color:var(--text-2); }
.empty { color:var(--text-3); font-size:13px; padding:12px 0; margin:0; }
.note {
  background:var(--surface-1); border:1px solid var(--border);
  border-left:3px solid var(--warning); border-radius:8px;
  padding:12px 16px; font-size:12.5px; color:var(--text-2); margin-bottom:16px;
}
.scroll { overflow-x:auto; }
footer { color:var(--text-3); font-size:12px; margin-top:24px; text-align:center; }
#tip {
  position:fixed; pointer-events:none; opacity:0; transition:opacity .1s;
  background:var(--text-1); color:var(--surface-1);
  font-size:12px; padding:5px 9px; border-radius:6px; white-space:nowrap; z-index:9;
}
@media (max-width:560px) {
  .bar-row { grid-template-columns:88px 1fr 96px; gap:7px; }
  .kpi .v { font-size:22px; }
}
"""


_JS = """
// hover 提示：直條與折線點都吃 data-label。SVG <title> 已經是無滑鼠時的
// 後備，這層只是讓它跟得上游標而不用等瀏覽器的預設延遲。
(function () {
  var tip = document.getElementById('tip');
  document.addEventListener('mouseover', function (e) {
    var label = e.target.getAttribute && e.target.getAttribute('data-label');
    if (!label) return;
    tip.textContent = label;
    tip.style.opacity = '1';
  });
  document.addEventListener('mousemove', function (e) {
    if (tip.style.opacity !== '1') return;
    var x = e.clientX + 12, y = e.clientY - 30;
    if (x + tip.offsetWidth > window.innerWidth - 8)
      x = e.clientX - tip.offsetWidth - 12;
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  });
  document.addEventListener('mouseout', function (e) {
    if (e.target.getAttribute && e.target.getAttribute('data-label'))
      tip.style.opacity = '0';
  });
})();
"""


# ─────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="產生 Notion 財務儀表板")
    ap.add_argument("--from-json", metavar="FILE",
                    help="用先前 dump 的 JSON 重畫，不連 Notion")
    ap.add_argument("--dump-json", metavar="FILE",
                    help="順便把抓到的原始資料存成 JSON")
    ap.add_argument("-o", "--output", default=OUTPUT_DEFAULT)
    args = ap.parse_args(argv)

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = collect()
        if args.dump_json:
            with open(args.dump_json, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            print(f"原始資料已存：{args.dump_json}")

    stats = compute(raw)
    with open(args.output, "w", encoding="utf-8", newline="") as f:
        f.write(render(stats))

    print(f"已產出：{args.output}")
    print(f"  本月支出 NT$ {_money(stats['month_total'])}（{stats['month_count']} 筆）")
    print(f"  交易總數 {stats['all_count']}、持倉 {len(stats['holdings'])} 檔、"
          f"淨值快照 {len(stats['networth'])} 筆")
    if stats["stale_days"] is not None and stats["stale_days"] > 3:
        print(f"  ⚠ 最後一筆交易是 {stats['stale_days']} 天前，同步可能斷了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
