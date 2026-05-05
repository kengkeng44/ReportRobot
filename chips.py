"""
台股盤後籌碼資料：三大法人買賣超。資料源 = 證交所 OpenAPI。
"""

from datetime import date, timedelta

import http_utils


HEADERS = {"User-Agent": "Mozilla/5.0"}
TWSE_BFI82U = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"


def _last_trading_day(today=None):
    """取最近一個交易日（週末推到週五，不考慮國定假日）。"""
    today = today or date.today()
    wd = today.weekday()  # Mon=0, Sun=6
    if wd == 0:           # Monday → 上週五
        return today - timedelta(days=3)
    if wd == 5:           # Saturday → 週五
        return today - timedelta(days=1)
    if wd == 6:           # Sunday → 週五
        return today - timedelta(days=2)
    return today - timedelta(days=1)


def get_institutional_trades(target_date=None):
    """
    抓三大法人買賣超。回 dict 或 None：
      {
        'date': '2026-05-02',
        'foreign': 12.34,            # 億元，買賣差額
        'investment_trust': -2.34,
        'dealer': 5.67,
        'total': 15.67,
      }
    證交所 API 會在收盤後（約 14:30）才有資料；找不到就往前 5 天試（吸收假日）。
    """
    target = target_date or _last_trading_day()
    for delta in range(5):
        d = target - timedelta(days=delta)
        date_str = d.strftime("%Y%m%d")
        try:
            r = http_utils.get(
                TWSE_BFI82U,
                params={"response": "json", "dayDate": date_str},
                headers=HEADERS,
                timeout=10,
            )
            data = r.json()
            rows = data.get("data") or []
            if not rows:
                # 印出完整 keys 跟 stat 訊息方便診斷（API 改欄位 / 資料未公布 / 假日）
                print(f"  [chips] {date_str} 無資料：stat={data.get('stat')} "
                      f"keys={list(data.keys())}")
                continue
            print(f"  [chips] {date_str} 拿到 {len(rows)} rows，"
                  f"category 樣本={[r[0] for r in rows[:6]]}")

            result = {"date": d.strftime("%Y-%m-%d")}
            # 證交所 BFI82U 把外資拆兩列（外資及陸資 + 外資自營商）、自營商也拆
            # 兩列（自行買賣 + 避險）。要累加，不能 overwrite。
            foreign_main = None
            foreign_sub = None
            dealer_self = None
            dealer_hedge = None
            dealer_total = None  # 萬一 API 改回單列「自營商」

            for row in rows:
                category = (row[0] or "").strip()
                try:
                    diff = float(row[3].replace(",", "")) / 1e8  # 億元
                except (ValueError, IndexError, AttributeError):
                    continue

                if "投信" in category:
                    result["investment_trust"] = diff
                elif "外資" in category and "自營商" in category:
                    foreign_sub = diff
                elif "外資" in category:
                    foreign_main = diff
                elif "自營商" in category and "自行買賣" in category:
                    dealer_self = diff
                elif "自營商" in category and "避險" in category:
                    dealer_hedge = diff
                elif "自營商" in category:
                    dealer_total = diff
                elif "合計" in category:
                    result["total"] = diff

            # 外資 = 外資及陸資 + 外資自營商（任一缺值就用另一個）
            if foreign_main is not None or foreign_sub is not None:
                result["foreign"] = (foreign_main or 0.0) + (foreign_sub or 0.0)
            # 自營商：優先拼 self+hedge；都沒拆才用單列總額
            if dealer_self is not None or dealer_hedge is not None:
                result["dealer"] = (dealer_self or 0.0) + (dealer_hedge or 0.0)
            elif dealer_total is not None:
                result["dealer"] = dealer_total

            if "foreign" in result or "investment_trust" in result or "dealer" in result:
                return result
        except Exception as e:
            print(f"三大法人抓取失敗 {date_str}: {e}")
            continue
    return None
