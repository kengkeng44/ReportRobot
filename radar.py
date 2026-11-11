"""全球股市雷達：機器人賽道的即時動能榜。

GET /radar 只讀快照（秒開）；快照每天 14:45 排程更新，或按頁面上的更新鈕。流程拆成三段：
- collect  打 Yahoo（日線一年、財報摘要）、證交所／櫃買 OpenAPI（月營收）、FinMind（台股 EPS、法人），唯一碰網路的部分
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

# ── 我的追蹤 ──────────────────────────────────────────────
# MINE 是「追蹤」分頁的順序。候選池裡已有的（例如 NVDA）直接寫代號；
# 不在池裡的放進 WATCH，跟候選池同一次抓價，但不參加推薦榜和 RS 排名。
# kind: stock / etf（ETF 沒有營收、獲利、上修三燈）
MINE = ["SPCX", "GOOGL", "AAPL", "NVDA", "2330", "0050"]
WATCH = [
    ("SPCX", "SpaceX", "gl", "stock", "火箭發射、Starlink 衛星網路"),
    ("GOOGL", "Google", "gl", "stock", "Alphabet：搜尋、YouTube、雲端、AI"),
    ("AAPL", "Apple", "gl", "stock", "iPhone、Mac、服務"),
    ("2330", "台積電", "tw", "stock", "晶圓代工"),
    ("0050", "元大台灣50", "tw", "etf", "台灣市值前 50 大"),
]

BENCH = {"tw": "^TWII", "us": "^GSPC", "vix": "^VIX"}

# 相對強度跟「所在市場」的大盤比：日股比日經、陸股比滬深 300…，不再只在候選池裡互比
# 陸股：Yahoo 的 000300.SS 只有一天資料，改用追蹤滬深 300 的 ETF 510300.SS
LOCAL_INDEX = {".T": "^N225", ".SS": "510300.SS", ".SZ": "510300.SS", ".HK": "^HSI",
               ".KS": "^KS11", ".KQ": "^KQ11", ".ST": "^OMX", ".SW": "^SSMI"}


def index_of(code, mkt):
    if mkt == "tw":
        return "^TWII"
    for suf, idx in LOCAL_INDEX.items():
        if code.endswith(suf):
            return idx
    return "^GSPC"

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
    "SPCX": {"why": [("2026/6/12 在 Nasdaq 上市；不滿一年，150、200 日線和 52 週高低點要到 2027 年 3、4 月才算得出來",
                      "https://builtin.com/articles/spacex-ipo-valuation-20260612")]},
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


def rs_vs_pool(pool_raw, raw):
    """不在候選池的股票，用它的 rsraw 對照候選池算 1–99 百分位，不改動池內排名。"""
    if raw is None or not pool_raw:
        return None
    below = sum(1 for x in pool_raw if x <= raw)
    return max(1, min(99, round(below / len(pool_raw) * 99)))


def excess_rs(raw, index_raw):
    """個股 IBD 式加權報酬 − 所在市場大盤的同一個數，單位是百分點。"""
    if raw is None or index_raw is None:
        return None
    return round(raw - index_raw, 1)


def ttm_eps_yoy(quarters):
    """單季 EPS（舊→新）→ (近四季 EPS 年增 %, 近四季是否虧損)。不滿 8 季或去年同期 ≤ 0 時年增給 None。"""
    q = [x for x in quarters if x is not None]
    if len(q) < 4:
        return None, False
    now = sum(q[-4:])
    if len(q) < 8:
        return None, now < 0
    before = sum(q[-8:-4])
    if before <= 0:
        return None, now < 0
    return round((now / before - 1) * 100, 1), now < 0


def est_change(current, ago):
    """今年度 EPS 共識：現在 vs 90 天前，變化 %。分母取絕對值，虧損股也看得出方向。"""
    if current is None or ago is None or ago == 0:
        return None
    return round((current - ago) / abs(ago) * 100, 1)


def inst_ratio(net_shares, volumes, days=20):
    """近 N 日三大法人買賣超股數 ÷ 同期成交股數，%。"""
    vols = [v for v in volumes if v][-days:]
    if net_shares is None or len(vols) < days:
        return None
    total = sum(vols)
    return round(net_shares / total * 100, 1) if total else None


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


def _row(code, name, mkt, seg, role, prices, revenue):
    sym, closes, vols = prices.get(code, (None, [], []))
    r = {"c": code, "n": name, "m": mkt, "g": seg, "role": role, "sym": sym}
    r.update(price_metrics(closes, vols))
    r["spark"] = [round(c, 2) for c in closes[-60:][::2]]  # 近 3 個月走勢小圖，30 點
    return r


def _fundamentals(r, code, mkt, revenue, extra, etf=False):
    """營收、獲利、上修、法人、相對強度塞進 row。資料來源的優先序寫在這裡，頁面只看數字。"""
    yf = (extra.get("yahoo") or {}).get(code, {})
    note = NOTES.get(code, {})
    if not etf:
        # 營收：台股用月營收（較新），外股用 Yahoo 最近一季年增
        if mkt == "tw" and code in revenue:
            r["rev"], r["revAt"] = revenue[code]
        elif yf.get("rev") is not None:
            r["rev"], r["revAt"] = yf["rev"], "最近一季"
        # 獲利：台股用 FinMind 近四季，外股用 Yahoo 最近一季
        eps = (extra.get("eps") or {}).get(code)
        if eps:
            r["eps"], loss = eps
            r["epsAt"] = "近四季"
            if loss:
                r["loss"] = True
        elif yf.get("eps") is not None:
            r["eps"], r["epsAt"] = yf["eps"], "最近一季"
        if yf.get("loss") or note.get("loss"):
            r["loss"] = True
        # 上修：Yahoo 共識 90 天變化；抓不到才用人工 NOTES
        if yf.get("est") is not None:
            r["est"] = yf["est"]
        elif note.get("est"):
            r["est"] = note["est"]
    # ETF 有申購買回，法人買賣超會被放大（0050 曾算出 -63%），不看
    net = (extra.get("inst") or {}).get(code)
    if mkt == "tw" and not etf and net is not None:
        r["inst"] = inst_ratio(net, (extra.get("vols") or {}).get(code, []))
    r["idx"] = index_of(code, mkt)
    r["rsx"] = excess_rs(r.get("rsraw"), (extra.get("index_raw") or {}).get(r["idx"]))


def assemble(prices, revenue, bench, extra=None):
    """各來源 → 給頁面的資料包。prices: {代號: (symbol, closes, volumes)}。
    extra: {"index": {指數: closes}, "yahoo": {代號: {...}}, "eps": {代號: (年增, 虧損)}, "inst": {代號: 買超股數}}"""
    extra = dict(extra or {})
    extra["vols"] = {c: v[2] for c, v in prices.items()}
    extra["index_raw"] = {k: price_metrics(v, []).get("rsraw") for k, v in (extra.get("index") or {}).items()}
    rows = []
    for code, name, mkt, seg, role in UNIVERSE:
        r = _row(code, name, mkt, seg, role, prices, revenue)
        _fundamentals(r, code, mkt, revenue, extra)
        note = NOTES.get(code, {})
        r["why"] = [{"t": t, "u": u} for t, u in note.get("why", [])]
        r["links"] = _links(code, mkt, r["sym"])
        rows.append(r)
    rank_rs(rows)
    pool_raw = [r["rsraw"] for r in rows if r.get("rsraw") is not None]
    watch = []
    for code, name, mkt, kind, role in WATCH:
        r = _row(code, name, mkt, None, role, prices, revenue)
        if kind == "etf":
            r["etf"] = True
        _fundamentals(r, code, mkt, revenue, extra, etf=kind == "etf")
        note = NOTES.get(code, {})
        r["why"] = [{"t": t, "u": u} for t, u in note.get("why", [])]
        r["links"] = _links(code, mkt, r["sym"])
        r["rs"] = rs_vs_pool(pool_raw, r.get("rsraw"))
        watch.append(r)
    for r in rows + watch:
        r.pop("rsraw", None)
    return {
        "at": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"),
        "regime": {k: regime(v) for k, v in bench.items()},
        "stocks": rows,
        "watch": watch,
        "mine": MINE,
        "missing": [r["c"] for r in rows + watch if r.get("px") is None],
    }


def _links(code, mkt, sym):
    if mkt == "tw":
        s = sym or f"{code}.TW"
        return [
            {"t": "Yahoo 股市", "u": f"https://tw.stock.yahoo.com/quote/{s}"},
            {"t": "月營收", "u": f"https://tw.stock.yahoo.com/quote/{s}/revenue"},
            {"t": "新聞", "u": f"https://tw.stock.yahoo.com/quote/{s}/news"},
        ]
    s = sym or code
    return [
        {"t": "Yahoo Finance", "u": f"https://finance.yahoo.com/quote/{s}"},
        {"t": "新聞", "u": f"https://finance.yahoo.com/quote/{s}/news"},
    ]


# ── collect（碰網路）──────────────────────────────────────

def _chart(sym):
    # 不走 http_utils 的重試：一檔掛掉就算了，不能讓整頁等 2+4+8 秒
    import requests
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                     params={"interval": "1d", "range": "1y"}, headers=UA, timeout=8)
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


_suffix = {}  # 台股代號 → 上次成功的 .TW / .TWO，下次直接打對的那個


def _fetch_one(code, mkt):
    if mkt == "tw":
        first = _suffix.get(code, ".TW")
        syms = [code + first, code + (".TWO" if first == ".TW" else ".TW")]
    else:
        syms = [code]
    for s in syms:
        try:
            got = _chart(s)
        except Exception as e:
            print(f"[radar] {s} 抓取失敗：{e}")
            got = None
        if got:
            if mkt == "tw":
                _suffix[code] = s[len(code):]
            return code, (s, got[0], got[1])
    return code, None


_REV_URLS = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
]


def _revenue():
    out = {}
    for u in _REV_URLS:
        try:
            out.update(parse_revenue(http_utils.get(u, headers=UA, timeout=20).json()))
        except Exception as e:
            print(f"[radar] 月營收抓取失敗 {u[:50]}：{e}")
    return out


def _yahoo_fund(syms):
    """Yahoo 財報摘要 → {symbol: {rev, eps, est, loss}}。要先拿 cookie＋crumb，拿不到就整批放棄。"""
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"})
    try:
        s.get("https://fc.yahoo.com", timeout=8)  # 本來就回 404，只是要它發的 cookie
    except Exception:
        pass
    try:
        crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=8).text.strip()
    except Exception as e:
        print(f"[radar] Yahoo crumb 取得失敗：{e}")
        return {}
    if not crumb or "<" in crumb or " " in crumb:
        print("[radar] Yahoo crumb 無效，跳過財報摘要")
        return {}

    def raw(d, *ks):
        for k in ks:
            d = (d or {}).get(k)
        return d

    def one(sym):
        try:
            r = s.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}",
                      params={"modules": "earningsTrend,financialData,defaultKeyStatistics", "crumb": crumb},
                      timeout=8)
            res = (r.json().get("quoteSummary") or {}).get("result") or []
        except Exception as e:
            print(f"[radar] {sym} 財報摘要失敗：{e}")
            return sym, None
        if not res:
            return sym, None
        x, out = res[0], {}
        for k, src in (("rev", "revenueGrowth"), ("eps", "earningsGrowth")):
            v = raw(x, "financialData", src, "raw")
            if v is not None:
                out[k] = round(v * 100, 1)
        year = [t for t in raw(x, "earningsTrend", "trend") or [] if t.get("period") == "0y"]
        if year:
            et = year[0].get("epsTrend") or {}
            out["est"] = est_change(raw(et, "current", "raw"), raw(et, "90daysAgo", "raw"))
        teps = raw(x, "defaultKeyStatistics", "trailingEps", "raw")
        if teps is not None and teps < 0:
            out["loss"] = True
        return sym, out

    with ThreadPoolExecutor(max_workers=8) as ex:
        return {k: v for k, v in ex.map(one, syms) if v}


_FINMIND = "https://api.finmindtrade.com/api/v4/data"


def _finmind(dataset, code, start):
    # 不帶 token 每小時 300 次；一次更新約 36 次。有設 FINMIND_TOKEN 就帶上
    headers = dict(UA)
    if os.environ.get("FINMIND_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['FINMIND_TOKEN']}"
    r = http_utils.get(_FINMIND, params={"dataset": dataset, "data_id": code, "start_date": start},
                       headers=headers, timeout=20)
    return r.json().get("data") or []


def _tw_extra(code):
    """FinMind：單季 EPS → 近四季年增；三大法人 → 近 20 個交易日買賣超股數。"""
    today = datetime.now(TPE).date()
    eps = net = None
    try:
        q = sorted((d["date"], d["value"]) for d in
                   _finmind("TaiwanStockFinancialStatements", code, str(today - timedelta(days=800)))
                   if d.get("type") == "EPS")
        if q:
            eps = ttm_eps_yoy([v for _, v in q])
    except Exception as e:
        print(f"[radar] {code} EPS 抓取失敗：{e}")
    try:
        rows = _finmind("TaiwanStockInstitutionalInvestorsBuySell", code, str(today - timedelta(days=45)))
        days = sorted({d["date"] for d in rows})[-20:]
        if len(days) == 20:
            net = sum(d["buy"] - d["sell"] for d in rows if d["date"] in days)
    except Exception as e:
        print(f"[radar] {code} 法人抓取失敗：{e}")
    return code, eps, net


def _tw_all(codes):
    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(_tw_extra, codes))


def collect():
    tw_codes = [u[0] for u in UNIVERSE + WATCH if u[2] == "tw"]
    indices = sorted({index_of(u[0], u[2]) for u in UNIVERSE + WATCH})
    with ThreadPoolExecutor(max_workers=12) as ex:
        rev = ex.submit(_revenue)
        tw = ex.submit(_tw_all, tw_codes)
        got = list(ex.map(lambda u: _fetch_one(u[0], u[2]), UNIVERSE + WATCH))
        bench = list(ex.map(lambda kv: (kv[0], _chart(kv[1])), BENCH.items()))
        idx = list(ex.map(lambda sym: (sym, _chart(sym)), indices))
        revenue = rev.result()
        prices = {c: v for c, v in got if v}
        yf = _yahoo_fund([v[0] for v in prices.values()])
        tw = tw.result()
    extra = {
        "index": {k: v[0] for k, v in idx if v},
        "yahoo": {c: yf[v[0]] for c, v in prices.items() if v[0] in yf},
        "eps": {c: e for c, e, _ in tw if e and (e[0] is not None or e[1])},
        "inst": {c: n for c, _, n in tw if n is not None},
    }
    return prices, revenue, {k: (v[0] if v else []) for k, v in bench}, extra


# ── 快照：每天排程更新一次，或按頁面上的更新鈕；打開頁面只讀快照，不連外 ──
SNAPSHOT_PATH = os.environ.get("RADAR_SNAPSHOT", "/tmp/radar_snapshot.json")
_lock = threading.Lock()
_snap = {"data": None}


def snapshot():
    """目前的快照；記憶體沒有就讀磁碟（行程重啟後）。都沒有回 None。"""
    if _snap["data"] is None and os.path.exists(SNAPSHOT_PATH):
        try:
            with open(SNAPSHOT_PATH, encoding="utf-8") as f:
                _snap["data"] = json.load(f)
        except Exception as e:
            print(f"[radar] 讀快照失敗：{e}")
    return _snap["data"]


def refresh():
    """重抓一次並存成快照。同時只跑一個；已在跑的話等它跑完直接用它的結果。"""
    if not _lock.acquire(blocking=False):
        with _lock:
            return _snap["data"]
    try:
        t0 = time.time()
        d = assemble(*collect())
        d["secs"] = round(time.time() - t0, 1)
        if len(d["missing"]) == len(d["stocks"]) and _snap["data"]:
            print("[radar] 這次一檔都沒抓到，保留舊快照")
            return _snap["data"]
        _snap["data"] = d
        try:
            with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
        except Exception as e:
            print(f"[radar] 寫快照失敗：{e}")
        print(f"[radar] 更新完成 {d['secs']}s，缺 {len(d['missing'])} 檔")
        return d
    finally:
        _lock.release()


def render(payload):
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        tpl = f.read()
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return tpl.replace("/*__RADAR_DATA__*/null", blob)


if __name__ == "__main__":
    # 本機預覽：python radar.py > radar_preview.html
    print(render(refresh()))
