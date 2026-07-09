"""
台股大盤量能 / 廣度 / 融資融券指標。資料源 = 證交所 RWD JSON（與 chips.py 同 pattern）。

設計原則（重要）：**寧可回 None 顯示 N/A，也絕不回錯數字。**
- 欄位一律用「中文名稱比對」定位（不寫死 index，TWSE 偶爾插欄位）。
- 取到的值一律過「數值解析 + 合理範圍」驗證，對不上就整筆 None。
- 這樣即使 TWSE 改格式，報告最多顯示 N/A，不會像黃金那樣顯示錯的數字。

各函式回 dict 或 None。無法在開發環境實測（TWSE 對外連線受限），
故所有解析都印診斷 log，搭配 server.py /admin/probe 端點部署後校準。
"""

from datetime import timedelta

import http_utils
from tz_utils import today_tpe


HEADERS = {"User-Agent": "Mozilla/5.0"}

# RWD JSON 端點（回 {stat, fields:[...], data:[[...]]} 或 {tables:[{fields,data}]}）
FMTQIK_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
MI_MARGN_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_MARGN"


def _num(s):
    """'1,234,567.8' / '+123.45' / '--' → float 或 None。"""
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("+", "")
    if t in ("", "--", "---", "N/A", "不適用"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _last_trading_day(today=None):
    """最近交易日（週末推到週五；不考慮國定假日，抓不到會往前找）。"""
    today = today or today_tpe()
    wd = today.weekday()
    if wd == 5:
        return today - timedelta(days=1)
    if wd == 6:
        return today - timedelta(days=2)
    if wd == 0:
        return today - timedelta(days=3)
    return today - timedelta(days=1)


def _fetch_rwd(url, params, tag):
    """打 RWD JSON，回 parsed dict 或 None。統一診斷 log。"""
    try:
        p = dict(params)
        p["response"] = "json"
        r = http_utils.get(url, params=p, headers=HEADERS, timeout=10)
        data = r.json()
        stat = data.get("stat")
        if stat and stat != "OK":
            print(f"  [{tag}] stat={stat}")
            return None
        return data
    except Exception as e:
        print(f"  [{tag}] 抓取失敗：{e}")
        return None


def _row_map(fields, row):
    """把 fields + row 併成 {欄位名: 值}；長度不符回 {}。"""
    if not fields or not row or len(fields) != len(row):
        return {}
    return {str(f).strip(): v for f, v in zip(fields, row)}


def _find_col(fields, *keywords):
    """回第一個「名稱含任一 keyword」的欄位 index，找不到回 None。"""
    if not fields:
        return None
    for i, f in enumerate(fields):
        name = str(f)
        if any(k in name for k in keywords):
            return i
    return None


# ════════════════════════════════════════
# 1. 大盤成交金額 + 加權指數（FMTQIK）
# ════════════════════════════════════════

def get_market_turnover(target_date=None):
    """回 {'date','turnover_yi','index','change_pt'} 或 None。
    FMTQIK 回當月每日資料，取最後一筆（最新交易日）。成交金額(元)→億。"""
    target = target_date or _last_trading_day()
    data = _fetch_rwd(
        FMTQIK_URL, {"date": target.strftime("%Y%m%d")}, "turnover"
    )
    if not data:
        return None
    fields = data.get("fields") or []
    rows = data.get("data") or []
    if not fields or not rows:
        print("  [turnover] 無 fields/data")
        return None

    i_date = _find_col(fields, "日期")
    i_amt = _find_col(fields, "成交金額")
    i_idx = _find_col(fields, "發行量加權股價指數", "加權股價指數")
    i_chg = _find_col(fields, "漲跌點數")
    if i_amt is None:
        print(f"  [turnover] 找不到成交金額欄，fields={fields}")
        return None

    row = rows[-1]  # 最新交易日
    amt = _num(row[i_amt]) if i_amt < len(row) else None
    if amt is None or amt <= 0:
        print(f"  [turnover] 成交金額解析失敗 row={row}")
        return None

    turnover_yi = amt / 1e8  # 元 → 億
    # 合理範圍驗證：台股單日成交約 1,000~10,000 億，超出視為解析錯
    if not (100 <= turnover_yi <= 100000):
        print(f"  [turnover] 成交金額 {turnover_yi:.0f} 億 超出合理範圍，判為解析錯")
        return None

    result = {
        "date": str(row[i_date]).strip() if i_date is not None and i_date < len(row) else "",
        "turnover_yi": turnover_yi,
        "index": _num(row[i_idx]) if i_idx is not None and i_idx < len(row) else None,
        "change_pt": _num(row[i_chg]) if i_chg is not None and i_chg < len(row) else None,
    }
    print(f"  [turnover] {result['date']} 成交 {turnover_yi:,.0f} 億 "
          f"指數 {result['index']} 漲跌 {result['change_pt']}")
    return result


# ════════════════════════════════════════
# 2. 漲跌家數（MI_INDEX，type=MS 的「漲跌證券數合計」）
# ════════════════════════════════════════

def get_updown_counts(target_date=None):
    """回 {'up','down','flat'} 或 None。
    MI_INDEX 回多張表，找含「上漲/下跌」的『漲跌證券數』表整體市場列。"""
    target = target_date or _last_trading_day()
    data = _fetch_rwd(
        MI_INDEX_URL,
        {"date": target.strftime("%Y%m%d"), "type": "MS"},
        "updown",
    )
    if not data:
        return None

    # 新格式 tables:[{title,fields,data}]；舊格式 data1..data9 + fields1..
    tables = data.get("tables")
    if not tables:
        tables = []
        for k in list(data.keys()):
            if k.startswith("data") and k[4:].isdigit():
                idx = k[4:]
                tables.append({
                    "fields": data.get(f"fields{idx}") or [],
                    "data": data.get(k) or [],
                    "title": "",
                })

    for tbl in tables:
        fields = tbl.get("fields") or []
        i_up = _find_col(fields, "上漲")
        i_dn = _find_col(fields, "下跌")
        i_flat = _find_col(fields, "持平", "平盤", "未成交")
        if i_up is None or i_dn is None:
            continue
        # 取第一列有效數字（通常第一列＝整體/股票）
        for row in (tbl.get("data") or []):
            up = _num(row[i_up]) if i_up < len(row) else None
            dn = _num(row[i_dn]) if i_dn < len(row) else None
            if up is None or dn is None:
                continue
            # 合理範圍：台股上市約 1,000 檔，家數 0~2,000
            if not (0 <= up <= 3000 and 0 <= dn <= 3000):
                continue
            flat = _num(row[i_flat]) if (i_flat is not None and i_flat < len(row)) else None
            result = {"up": int(up), "down": int(dn),
                      "flat": int(flat) if flat is not None else None}
            print(f"  [updown] 紅 {result['up']} 綠 {result['down']} 平 {result['flat']}")
            return result

    print("  [updown] 找不到漲跌家數表（需 /admin/probe 校欄位）")
    return None


# ════════════════════════════════════════
# 3. 融資餘額 + 增減（MI_MARGN，selectType=MS 彙總）
# ════════════════════════════════════════

def get_margin_balance(target_date=None):
    """回 {'margin_bal_yi','margin_chg_yi'} 或 None。
    融資今日餘額 vs 前日餘額（單位仟元 → 億）。"""
    target = target_date or _last_trading_day()
    data = _fetch_rwd(
        MI_MARGN_URL,
        {"date": target.strftime("%Y%m%d"), "selectType": "MS"},
        "margin",
    )
    if not data:
        return None

    tables = data.get("tables")
    if not tables:
        tables = []
        for k in list(data.keys()):
            if k.startswith("data") and k[4:].isdigit():
                idx = k[4:]
                tables.append({
                    "fields": data.get(f"fields{idx}") or [],
                    "data": data.get(k) or [],
                })

    for tbl in tables:
        fields = tbl.get("fields") or []
        i_today = _find_col(fields, "今日餘額")
        i_prev = _find_col(fields, "前日餘額")
        i_name = _find_col(fields, "項目", "融資融券", "類型") or 0
        if i_today is None:
            continue
        for row in (tbl.get("data") or []):
            name = str(row[i_name]) if i_name < len(row) else ""
            if "融資金額" not in name and "融資(" not in name and name.strip() != "融資":
                continue
            today_bal = _num(row[i_today]) if i_today < len(row) else None
            prev_bal = _num(row[i_prev]) if (i_prev is not None and i_prev < len(row)) else None
            if today_bal is None:
                continue
            bal_yi = today_bal / 1e5  # 仟元 → 億
            if not (100 <= bal_yi <= 100000):
                continue
            chg_yi = ((today_bal - prev_bal) / 1e5) if prev_bal is not None else None
            result = {"margin_bal_yi": bal_yi, "margin_chg_yi": chg_yi}
            print(f"  [margin] 融資餘額 {bal_yi:,.0f} 億 增減 {chg_yi}")
            return result

    print("  [margin] 找不到融資彙總列（需 /admin/probe 校欄位）")
    return None
