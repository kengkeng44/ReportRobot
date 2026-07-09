"""
期交所（TAIFEX）盤前風向球：台指期夜盤 + 外資台指期未平倉淨部位。

⚠️ 現狀：TAIFEX 端點/欄位在開發環境無法實測（對外連線受限），且 TAIFEX
格式較 TWSE 更雜（HTML 表 / 多種 OpenAPI schema）。因此本模組一律採
「解析不確定就回 None（顯示 N/A）」，**絕不回錯數字**。真實欄位靠
server.py /admin/probe 端點部署後帶回，再把 parser 校準。

回 dict 或 None。
"""

import http_utils


HEADERS = {"User-Agent": "Mozilla/5.0"}

# TAIFEX OpenAPI（JSON，較 HTML 穩）。實際 path/欄位待 /admin/probe 校準。
TAIFEX_OPENAPI = "https://openapi.taifex.com.tw/v1"

# 期貨每日行情（含台指期 TX 收盤；夜盤另有盤後報告，待校準）
FUT_DAILY_CANDIDATES = [
    f"{TAIFEX_OPENAPI}/DailyMarketReportFut",
]
# 三大法人期貨契約未平倉（找外資 TX 淨口數）
INST_OI_CANDIDATES = [
    f"{TAIFEX_OPENAPI}/OpenInterestOfMajorInstitutionalTraders",
    f"{TAIFEX_OPENAPI}/MarketDataOfMajorInstitutionalTraders",
]


def _num(s):
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("+", "")
    if t in ("", "--", "---", "N/A"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _fetch_json(url, tag):
    try:
        r = http_utils.get(url, headers=HEADERS, timeout=10)
        return r.json()
    except Exception as e:
        print(f"  [taifex/{tag}] 抓取失敗（可能端點需校準）：{e}")
        return None


def get_txf_night():
    """台指期夜盤：回 {'price','change'} 或 None。
    TAIFEX 夜盤(盤後)行情端點/欄位待校準；目前對不上一律 None。"""
    for url in FUT_DAILY_CANDIDATES:
        rows = _fetch_json(url, "night")
        if not isinstance(rows, list) or not rows:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            # 找台指期 TX（契約代號 / 名稱）
            name = str(row.get("ContractId") or row.get("Contract") or
                       row.get("契約") or "")
            if name.strip().upper() not in ("TX", "TXF") and "臺股期貨" not in name:
                continue
            price = _num(row.get("Close") or row.get("收盤價") or row.get("LastPrice"))
            change = _num(row.get("Change") or row.get("漲跌價"))
            if price is None:
                continue
            if not (1000 <= price <= 100000):  # 台指期合理範圍
                continue
            print(f"  [taifex/night] TX 夜盤 {price} 漲跌 {change}")
            return {"price": price, "change": change}
    print("  [taifex/night] 未取得（端點/欄位需 /admin/probe 校準）")
    return None


def get_foreign_txf_oi():
    """外資台指期未平倉淨部位（口）：回 {'net_oi'} 或 None。待校準。"""
    for url in INST_OI_CANDIDATES:
        rows = _fetch_json(url, "oi")
        if not isinstance(rows, list) or not rows:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            who = str(row.get("投資人類別") or row.get("Investor") or "")
            contract = str(row.get("商品名稱") or row.get("契約") or
                           row.get("Contract") or "")
            if "外資" not in who:
                continue
            if "臺股期貨" not in contract and contract.strip().upper() not in ("TX", "TXF"):
                continue
            net = _num(row.get("多空淨額未平倉口數") or
                       row.get("未平倉口數淨額") or row.get("NetOI"))
            if net is None:
                continue
            print(f"  [taifex/oi] 外資 TX 未平倉淨 {net} 口")
            return {"net_oi": int(net)}
    print("  [taifex/oi] 未取得（端點/欄位需 /admin/probe 校準）")
    return None
