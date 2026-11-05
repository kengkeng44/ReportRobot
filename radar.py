"""全球股市雷達：機器人賽道的即時動能榜。

GET /radar 每次打開就重抓（10 分鐘快取），流程拆成三段：
- collect  打 Yahoo（日線一年）與證交所／櫃買 OpenAPI（月營收），唯一碰網路的部分
- compute  純函式：收盤價序列 → 均線、52 週位置、相對強度、量比
- render   把結果塞進 static/radar.html 的 window.RADAR

燈號門檻只寫在 static/radar.html 的 IND 表裡，伺服器只給原始數字，
這樣推薦榜、供應鏈、個股細節用的是同一套標準。
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import http_utils

TPE = timezone(timedelta(hours=8))
PRICE_TTL = 600          # 價格快取 10 分鐘
REVENUE_TTL = 6 * 3600   # 月營收一個月才變一次，6 小時夠了
UA = {"User-Agent": "Mozilla/5.0"}

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "static", "radar.html")

# ── 候選池 ────────────────────────────────────────────────
# m: tw / gl；g: 供應鏈位置（對應 static/radar.html 的 SEG）
# 台股不寫後綴，collect 會先試 .TW 再試 .TWO。
UNIVERSE = [
    ("2049", "上銀", "tw", "screw", "螺桿、滑軌、諧波減速機"),
    ("1597", "直得", "tw", "screw", "滑軌、螺桿"),
    ("1536", "和大", "tw", "screw", "經盟英供應 Optimus"),
    ("4583", "台灣精銳", "tw", "reducer", "精密減速機"),
    ("4576", "大銀微系統", "tw", "motor", "線性／力矩馬達"),
    ("1590", "亞德客-KY", "tw", "motor", "氣動元件"),
    ("2308", "台達電", "tw", "motor", "馬達、驅動、自動化"),
    ("1504", "東元", "tw", "motor", "馬達"),
    ("8374", "羅昇", "tw", "motor", "自動化傳動整合"),
    ("2359", "所羅門", "tw", "vision", "視覺 AI"),
    ("2395", "研華", "tw", "brain", "工業電腦、邊緣 AI"),
    ("6166", "凌華", "tw", "brain", "機器人控制器、邊緣 AI"),
    ("4585", "達明", "tw", "integ", "協作手臂、人形"),
    ("2317", "鴻海", "tw", "integ", "整機代工、NVIDIA 夥伴"),
    ("6215", "和椿", "tw", "integ", "自動化系統整合"),
    ("2464", "盟立", "tw", "integ", "自動化設備"),
    ("6324.T", "Harmonic Drive", "gl", "reducer", "諧波減速機，市占約 35%"),
    ("6268.T", "Nabtesco", "gl", "reducer", "RV 減速機"),
    ("688017.SS", "綠的諧波", "gl", "reducer", "中國諧波減速機"),
    ("300124.SZ", "匯川", "gl", "motor", "中國伺服、驅動"),
    ("6273.T", "SMC", "gl", "motor", "氣動元件"),
    ("6861.T", "Keyence", "gl", "vision", "感測、視覺"),
    ("HEXA-B.ST", "Hexagon", "gl", "vision", "量測、感測"),
    ("NVDA", "NVIDIA", "gl", "brain", "Jetson Thor、Isaac 平台"),
    ("TSLA", "Tesla", "gl", "integ", "Optimus 人形"),
    ("9880.HK", "優必選", "gl", "integ", "人形整機"),
    ("277810.KQ", "Rainbow Robotics", "gl", "integ", "三星人形平台"),
    ("454910.KS", "斗山機器人", "gl", "integ", "協作手臂、人形"),
    ("TER", "Teradyne", "gl", "integ", "Universal Robots 協作臂"),
    ("6954.T", "發那科", "gl", "integ", "工業機器人"),
    ("6506.T", "安川電機", "gl", "integ", "工業機器人、伺服"),
    ("ABBN.SW", "ABB", "gl", "integ", "工業機器人"),
]

BENCH = {"tw": "^TWII", "us": "^GSPC", "vix": "^VIX"}

# 人工整理的基本面（抓不到 API 的部分）。每條重點都附來源連結。
NOTES = {
    "2049": {"est": "up", "why": [
        ("FactSet 調查法人上修 EPS 預估至 9.7 元", "https://news.cnyes.com/news/id/6620281"),
        ("人形機器人零件已分批出貨美系大廠", "https://udn.com/news/story/7241/9838392"),
    ]},
    "4576": {"why": [("關節馬達送樣中；9 月營收年增 74.7%",
                      "https://tw.stock.yahoo.com/news/%E5%85%AC%E5%91%8A-%E5%A4%A7%E9%8A%80%E5%BE%AE%E7%B3%BB%E7%B5%B1-2026%E5%B9%B49%E6%9C%88%E5%90%88%E4%BD%B5%E7%87%9F%E6%94%B64-27%E5%84%84%E5%85%83-%E5%B9%B4%E5%A2%9E74-054205333.html")]},
    "1590": {"why": [("10 月營收同期新高，公司預期需求延續到 2027 年", "https://udn.com/news/story/7252/9855075")]},
    "1536": {"loss": True, "why": [("2026 Q1 稅後虧損 1.46 億", "https://www.pocket.tw/school/articles/6763/")]},
    "4585": {"why": [("規劃 2026 下半年推雙足人形機器人", "https://ec.ltn.com.tw/article/breakingnews/5379841")]},
    "2317": {"why": [("Computex 展出結合 NVIDIA Isaac 的輪式人形機器人", "https://ec.ltn.com.tw/article/breakingnews/5493169")]},
    "TSLA": {"why": [("股東信稱 Optimus V3 可量產，尚無交機數據",
                      "https://www.allblogthings.com/2026/10/tesla-tells-shareholders-optimus-v3-robot-is-ready-for-mass-production.html")]},
    "9880.HK": {"loss": True, "why": [("Airbus 下單；目標 2026 年產 1 萬台",
                                       "https://www.marketscreener.com/news/shares-of-china-s-ubtech-jump-after-airbus-buys-its-humanoid-robots-ce7e58dfd18bf622")]},
    "454910.KS": {"loss": True, "why": [("2026 Q1 營業虧損 120 億韓元", "https://v.daum.net/v/20260303101555582")]},
    "6324.T": {"why": [("市占約 35%，中國廠低價 30–40% 追趕",
                        "https://jpstock-reports.pages.dev/reports/robotics_humanoid_bom_dd_2026-05-16")]},
}


# ── compute（純函式，測試直接打）─────────────────────────

def _sma(xs, n, end=None):
    end = len(xs) if end is None else end
    if end < n:
        return None
    w = xs[end - n:end]
    return sum(w) / n


def _ret(xs, days):
    if len(xs) <= days or not xs[-days - 1]:
        return None
    return (xs[-1] / xs[-days - 1] - 1) * 100


def price_metrics(closes, volumes):
    """日收盤（舊→新，已去掉 None）→ 指標原始數字。資料不足的欄位給 None。"""
    if not closes:
        return {}
    px = closes[-1]
    year = closes[-252:]
    m = {
        "px": round(px, 2),
        "chg": round(_ret(closes, 1), 2) if _ret(closes, 1) is not None else None,
        "ma50": _sma(closes, 50), "ma150": _sma(closes, 150), "ma200": _sma(closes, 200),
        "hi52": max(year), "lo52": min(year),
    }
    m200_prev = _sma(closes, 200, len(closes) - 20)
    m["ma200up"] = (m["ma200"] is not None and m200_prev is not None and m["ma200"] > m200_prev)
    # IBD 式加權報酬：近 3 月 40%，其餘三季各 20%；不足一年就用能算的部分
    r3, r6, r9, r12 = (_ret(closes, d) for d in (63, 126, 189, 252))
    m["r3m"] = round(r3, 1) if r3 is not None else None
    if r3 is not None and r6 is not None:
        parts = [(0.4, r3), (0.2, r6), (0.2, r9), (0.2, r12)]
        parts = [(w, r) for w, r in parts if r is not None]
        tw = sum(w for w, _ in parts)
        m["rsraw"] = sum(w * r for w, r in parts) / tw
    else:
        m["rsraw"] = None
    vols = [v for v in volumes if v]
    v20, v60 = _sma(vols, 20), _sma(vols, 60)
    m["vol"] = round(v20 / v60, 2) if v20 and v60 else None
    for k in ("ma50", "ma150", "ma200", "hi52", "lo52"):
        if m[k] is not None:
            m[k] = round(m[k], 2)
    return m


def rank_rs(rows):
    """把 rsraw 轉成 1–99 百分位（候選池內排名）。原地寫入 row['rs']。"""
    have = sorted((r["rsraw"], i) for i, r in enumerate(rows) if r.get("rsraw") is not None)
    n = len(have)
    for rank, (_, i) in enumerate(have):
        rows[i]["rs"] = max(1, min(99, round((rank + 1) / n * 99))) if n > 1 else 50
    for r in rows:
        r.setdefault("rs", None)


def regime(index_closes):
    """指數收盤 → 大環境：價、200 日線、是否在線上、200 日線是否向上。"""
    if not index_closes:
        return None
    m = price_metrics(index_closes, [])
    return {"px": m["px"], "chg": m["chg"], "ma200": m["ma200"], "ma200up": m["ma200up"]}


def parse_revenue(rows):
    """證交所／櫃買月營收 OpenAPI → {代號: (年增%, '2026/10')}。欄位名用包含比對，兩邊格式略有差異。"""
    out = {}
    for r in rows or []:
        code = yoy = ym = None
        for k, v in r.items():
            if "公司代號" in k:
                code = str(v).strip()
            elif "去年同月增減" in k:
                yoy = v
            elif "資料年月" in k:
                ym = str(v).strip()
        if not code or yoy in (None, ""):
            continue
        try:
            yoy = round(float(str(yoy).replace(",", "")), 1)
        except ValueError:
            continue
        label = ""
        if ym and ym.isdigit() and len(ym) >= 4:
            label = f"{int(ym[:-2]) + 1911}/{int(ym[-2:])}"
        out[code] = (yoy, label)
    return out


def assemble(prices, revenue, bench):
    """各來源 → 給頁面的資料包。prices: {代號: (symbol, closes, volumes)}。"""
    rows = []
    for code, name, mkt, seg, role in UNIVERSE:
        sym, closes, vols = prices.get(code, (None, [], []))
        r = {"c": code, "n": name, "m": mkt, "g": seg, "role": role, "sym": sym}
        r.update(price_metrics(closes, vols))
        if mkt == "tw" and code in revenue:
            r["rev"], r["revAt"] = revenue[code]
        note = NOTES.get(code, {})
        if note.get("loss"):
            r["loss"] = True
        if note.get("est"):
            r["est"] = note["est"]
        r["why"] = [{"t": t, "u": u} for t, u in note.get("why", [])]
        r["links"] = _links(code, mkt, sym)
        rows.append(r)
    rank_rs(rows)
    for r in rows:
        r.pop("rsraw", None)
    return {
        "at": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"),
        "regime": {k: regime(v) for k, v in bench.items()},
        "stocks": rows,
        "missing": [r["c"] for r in rows if r.get("px") is None],
    }


def _links(code, mkt, sym):
    if mkt == "tw":
        return [
            {"t": "Yahoo 股市", "u": f"https://tw.stock.yahoo.com/quote/{code}"},
            {"t": "月營收", "u": f"https://tw.stock.yahoo.com/quote/{code}/revenue"},
            {"t": "新聞", "u": f"https://news.cnyes.com/search?keyword={code}"},
        ]
    s = sym or code
    return [
        {"t": "Yahoo Finance", "u": f"https://finance.yahoo.com/quote/{s}"},
        {"t": "新聞", "u": f"https://finance.yahoo.com/quote/{s}/news"},
    ]


# ── collect（碰網路）──────────────────────────────────────

def _chart(sym):
    r = http_utils.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                       params={"interval": "1d", "range": "1y"}, headers=UA, timeout=12)
    if r.status_code != 200:
        return None
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        return None
    q = (res[0].get("indicators") or {}).get("quote") or [{}]
    closes_raw, vols_raw = q[0].get("close") or [], q[0].get("volume") or []
    pairs = [(c, (vols_raw[i] if i < len(vols_raw) else None))
             for i, c in enumerate(closes_raw) if c is not None]
    if not pairs:
        return None
    return [c for c, _ in pairs], [v for _, v in pairs]


def _fetch_one(code, mkt):
    syms = [f"{code}.TW", f"{code}.TWO"] if mkt == "tw" else [code]
    for s in syms:
        try:
            got = _chart(s)
        except Exception as e:
            print(f"[radar] {s} 抓取失敗：{e}")
            got = None
        if got:
            return code, (s, got[0], got[1])
    return code, None


_REV_URLS = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
]
_lock = threading.Lock()
_cache = {"data": None, "ts": 0, "rev": None, "rev_ts": 0}


def _revenue():
    if _cache["rev"] is not None and time.time() - _cache["rev_ts"] < REVENUE_TTL:
        return _cache["rev"]
    out = {}
    for u in _REV_URLS:
        try:
            out.update(parse_revenue(http_utils.get(u, headers=UA, timeout=20).json()))
        except Exception as e:
            print(f"[radar] 月營收抓取失敗 {u[:50]}：{e}")
    if out:
        _cache["rev"], _cache["rev_ts"] = out, time.time()
    return out


def collect():
    with ThreadPoolExecutor(max_workers=8) as ex:
        got = list(ex.map(lambda u: _fetch_one(u[0], u[2]), UNIVERSE))
        bench = list(ex.map(lambda kv: (kv[0], _chart(kv[1])), BENCH.items()))
    prices = {c: v for c, v in got if v}
    return prices, _revenue(), {k: (v[0] if v else []) for k, v in bench}


def data(force=False):
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["ts"] < PRICE_TTL:
            return _cache["data"]
        d = assemble(*collect())
        _cache["data"], _cache["ts"] = d, time.time()
        return d


def render(payload):
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        tpl = f.read()
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return tpl.replace("/*__RADAR_DATA__*/null", blob)


if __name__ == "__main__":
    # 本機預覽：python radar.py > radar_preview.html
    print(render(data(force=True)))
