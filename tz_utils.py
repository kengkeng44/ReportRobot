"""
時區工具：所有「今天是哪天」「現在幾點」一律走台北時間。

Railway 容器預設 UTC。直接用 date.today() / datetime.now() 在 TPE 凌晨 0-8 點
跑時會回前一天，導致報告標題日期錯、chips._last_trading_day() 跳過 Monday
等 bug。集中用這裡的 helper 才是 source of truth。
"""

from datetime import date, datetime, timedelta, timezone


_TPE = timezone(timedelta(hours=8))


def now_tpe() -> datetime:
    """現在的 datetime（aware，tz=+08:00）。"""
    return datetime.now(_TPE)


def today_tpe() -> date:
    """今天的日期，依台北時區判斷。"""
    return now_tpe().date()
