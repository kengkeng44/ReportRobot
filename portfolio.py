"""
持倉概覽：用 Yahoo Finance 抓現價，算市值、損益、損益%，格式化成 LINE 區塊。

特殊代號（黃金存摺、興櫃）yfinance 抓不到，用 _MANUAL_PRICES 手動指定。
可用 env var MANUAL_PRICES 覆蓋（格式：AU9901=17818,XX=123）。
"""

import os
import re

import http_utils
from stock_news import get_stock_name


def _load_manual_prices():
    """env var MANUAL_PRICES 格式：KEY=VALUE,KEY=VALUE。
    AU9901 不在這裡 hardcode — 改用即時國際金價計算（見 _get_gold_ntd_per_gram）。"""
    out = {}
    raw = os.environ.get("MANUAL_PRICES", "") or ""
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        try:
            out[k.strip().upper()] = float(v.strip().replace(",", ""))
        except ValueError:
            pass
    return out


_MANUAL_PRICES = _load_manual_prices()


# 黃金存摺每張多少 g。
# 預設 3.75g（1 錢）— 從 user 成交 NT$17,360 / 國際金價反推最接近的 unit。
# 若實際是 1 兩（37.5g）→ 設 GOLD_GRAM_PER_SHARE=37.5 環境變數覆寫。
_GOLD_GRAM_PER_SHARE = float(os.environ.get("GOLD_GRAM_PER_SHARE", "3.75"))
_TROY_OZ_TO_GRAM = 31.1034768


# 台股代號：4-6 位數字 + 可選 1 個英文字母（槓桿/反向 ETF 如 00631L）
_TW_TICKER_RE = re.compile(r'^\d{4,6}[A-Z]?$')


def _is_special_security(ticker):
    """興櫃 / 黃金存摺等 yfinance 抓不到的特殊代號。
    AU9901 = 臺灣銀行黃金存摺，需用台銀牌告，這裡先 skip 顯 N/A。"""
    t = (ticker or "").upper()
    return t.startswith("AU")  # AU9901 黃金存摺


def _is_tw_ticker(ticker):
    # AU9901（臺銀金／黃金現貨）在證交所掛牌、以台幣計價，但代號帶字母，
    # 四碼數字的規則抓不到它。漏掉的後果是台幣金額被當成美元再乘匯率，
    # 一筆一萬七會膨脹成五十幾萬 —— 而且看起來就是個正常數字。
    if _is_special_security(ticker):
        return True
    return bool(_TW_TICKER_RE.fullmatch(ticker or ''))


def _to_yahoo_symbol(ticker):
    return f"{ticker}.TW" if _is_tw_ticker(ticker) else ticker


def get_live_price(ticker):
    """用 Yahoo Finance v8 chart API 抓最新股價，失敗回傳 None。
    特殊代號分流：
      AU 開頭 (黃金存摺) → 用國際金價 × USD/TWD ÷ 31.10 g/oz × g/張
      其他 in MANUAL_PRICES → 手動指定價
      _is_special_security 但都沒對應 → N/A"""
    t = (ticker or "").upper()
    if t in _MANUAL_PRICES:
        return _MANUAL_PRICES[t]
    if t.startswith("AU"):
        # 黃金存摺：用即時國際金價計算
        ntd_per_g = _get_gold_ntd_per_gram()
        if ntd_per_g is None:
            print(f"  {ticker} 金價抓取失敗，現價 N/A")
            return None
        price = ntd_per_g * _GOLD_GRAM_PER_SHARE
        print(
            f"  {ticker} 計算：NT${ntd_per_g:.0f}/g × "
            f"{_GOLD_GRAM_PER_SHARE}g/張 = NT${price:,.0f}/張"
        )
        return price
    if _is_special_security(ticker):
        print(f"  {ticker} 是興櫃 / 特殊代號，yfinance 抓不到，現價 N/A")
        return None
    symbol = _to_yahoo_symbol(ticker)
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        resp = http_utils.get(
            url,
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        data = resp.json()
        result = (data.get('chart', {}) or {}).get('result') or []
        if not result:
            return None
        meta = result[0].get('meta', {}) or {}
        return meta.get('regularMarketPrice') or meta.get('previousClose')
    except Exception as e:
        print(f"  Yahoo 股價失敗 {symbol}：{e}")
        return None


def _get_usd_twd():
    """抓即時 USD/TWD 匯率（Yahoo TWD=X），失敗回 None。"""
    try:
        r = http_utils.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/TWD=X",
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        meta = ((r.json().get('chart') or {}).get('result') or [{}])[0].get('meta', {})
        return meta.get('regularMarketPrice') or meta.get('previousClose')
    except Exception as e:
        print(f"  USD/TWD 匯率抓取失敗：{e}")
        return None


def _get_gold_ntd_per_gram():
    """抓國際金價（GC=F 黃金期貨 USD/oz）+ USD/TWD 匯率 → 換算 NT$/g。
    失敗回 None。"""
    try:
        r = http_utils.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        meta = ((r.json().get('chart') or {}).get('result') or [{}])[0].get('meta', {})
        usd_per_oz = meta.get('regularMarketPrice') or meta.get('previousClose')
        if not usd_per_oz:
            return None
        rate = _get_usd_twd()
        if not rate:
            return None
        ntd_per_g = usd_per_oz * rate / _TROY_OZ_TO_GRAM
        print(
            f"  金價：USD${usd_per_oz:.2f}/oz × {rate:.2f} TWD/USD ÷ "
            f"31.10 g/oz = NT${ntd_per_g:.0f}/g"
        )
        return ntd_per_g
    except Exception as e:
        print(f"  金價抓取失敗：{e}")
        return None


def _format_price(value, is_us):
    if value is None:
        return "N/A"
    prefix = "$" if is_us else ""
    if abs(value) >= 100:
        return f"{prefix}{value:,.0f}"
    return f"{prefix}{value:,.2f}"


def _format_pnl_amount(value, is_us):
    sign = "+" if value >= 0 else "-"
    prefix = "$" if is_us else ""
    return f"{sign}{prefix}{abs(value):,.0f}"


def _avg_display(avg_cost, is_us):
    """成本未知就寫「未知」。顯示 0 會被讀成零成本取得，是假資訊。"""
    return _format_price(avg_cost, is_us) if avg_cost is not None else "未知"


def _compute_portfolio_data(portfolio):
    """共用計算：跑 portfolio dict 算出每檔現價 / 損益 / 台美分區小計 / 淨值。
    給 build_portfolio_summary（文字版）與 build_portfolio_flex（Flex 版）共用。"""
    rows = []
    tw_cost = tw_value = 0.0
    us_cost = us_value = 0.0
    # 損益小計專用：只累加成本已知的部位，跟淨值用的 *_value 分開
    tw_priced_value = us_priced_value = 0.0
    unknown_cost_tickers = []

    for ticker, p in portfolio.items():
        is_us = not _is_tw_ticker(ticker)
        shares = p["shares"]
        avg_cost = p["avg_cost"]
        current = get_live_price(ticker)
        name = get_stock_name(ticker)
        display = ticker if is_us else (name or ticker)
        # 成本未知（庫存快照沒有成本欄）不能顯示成 0 —— 那看起來像零成本取得
        cost_known = avg_cost is not None
        avg_str = _format_price(avg_cost, is_us) if cost_known else "未知"
        if not cost_known:
            unknown_cost_tickers.append(ticker)

        if current is None:
            note = "（黃金存摺 / 興櫃）" if _is_special_security(ticker) else ""
            rows.append({
                "ticker": ticker, "display": display, "is_us": is_us,
                "shares": shares, "avg": avg_cost, "avg_str": avg_str,
                "current": None, "current_str": f"N/A{note}",
                "pnl": None, "pnl_pct": None,
                "sort_key": shares * avg_cost if cost_known else 0,
            })
            continue

        market_value = shares * current
        cost_value = shares * avg_cost if cost_known else None
        pnl = market_value - cost_value if cost_known else None
        pnl_pct = ((current - avg_cost) / avg_cost * 100
                   if cost_known and avg_cost > 0 else None)

        # 淨值算全部（股票是真的持有），損益只算成本已知的 ——
        # 有市值沒成本會把整筆市值當成獲利。
        if is_us:
            us_value += market_value
            if cost_known:
                us_cost += cost_value
                us_priced_value += market_value
        else:
            tw_value += market_value
            if cost_known:
                tw_cost += cost_value
                tw_priced_value += market_value

        rows.append({
            "ticker": ticker, "display": display, "is_us": is_us,
            "shares": shares, "avg": avg_cost, "avg_str": avg_str,
            "current": current, "current_str": _format_price(current, is_us),
            "pnl": pnl, "pnl_pct": pnl_pct,
            "sort_key": market_value,
        })

    rows.sort(key=lambda r: r["sort_key"], reverse=True)

    tw_summary = None
    us_summary = None
    if tw_cost > 0:
        tw_pnl = tw_priced_value - tw_cost
        tw_summary = {
            "cost": tw_cost, "value": tw_priced_value, "pnl": tw_pnl,
            "pct": tw_pnl / tw_cost * 100, "is_us": False,
        }
    if us_cost > 0:
        us_pnl = us_priced_value - us_cost
        us_summary = {
            "cost": us_cost, "value": us_priced_value, "pnl": us_pnl,
            "pct": us_pnl / us_cost * 100, "is_us": True,
        }

    rate = None
    net_ntd = None
    if tw_value > 0 or us_value > 0:
        rate = _get_usd_twd()
        if us_value > 0 and rate:
            net_ntd = tw_value + us_value * rate
        elif us_value == 0:
            net_ntd = tw_value
        # rate None + us_value > 0 → 留 net_ntd=None，呼叫端顯示分計

    return {
        "rows": rows,
        "tw_summary": tw_summary,
        "us_summary": us_summary,
        "net_value_ntd": net_ntd,
        "usd_twd_rate": rate,
        "tw_value_only_ntd": tw_value if tw_value > 0 else None,
        "us_value_only_usd": us_value if us_value > 0 else None,
        # 哪幾檔沒被算進損益 —— 少算要說得出是哪些，不能靜靜少算
        "unknown_cost_tickers": unknown_cost_tickers,
    }


def build_portfolio_flex(portfolio):
    """Flex 版持倉概覽。無持倉回 None；呼叫端可 fallback 文字版。"""
    if not portfolio:
        return None
    data = _compute_portfolio_data(portfolio)
    from flex_builder import portfolio_flex
    return portfolio_flex(data)


def build_portfolio_summary(portfolio):
    """
    portfolio: {ticker: {'shares': N, 'avg_cost': X}}
    回傳 HTML 字串。台股顯示中文名（不顯示代號），美股保留代號。
    最後加 NTD / USD 兩幣別總報酬（避免幣別混算誤導）。
    """
    if not portfolio:
        return ""

    lines = ["<b>📊 持倉概覽</b>"]
    rows = []
    tw_cost = tw_value = 0.0
    us_cost = us_value = 0.0
    # 淨值要含成本未知的部位（股票是真的持有），總報酬不能含 —— 兩組分開累加
    tw_net = us_net = 0.0
    unknown_cost = []

    for ticker, p in portfolio.items():
        is_us = not _is_tw_ticker(ticker)
        shares = p['shares']
        avg_cost = p['avg_cost']
        current = get_live_price(ticker)
        name = get_stock_name(ticker)
        # 台股只顯示中文名，美股顯示 ticker（沒對應中文名時 fallback ticker）
        display = ticker if is_us else (name or ticker)

        if current is None:
            note = "（黃金存摺/興櫃，現價需手動）" if _is_special_security(ticker) else ""
            rows.append({
                'sort_key': shares * avg_cost if avg_cost is not None else 0,
                'line': (
                    f"{display}｜{shares}股｜均價{_avg_display(avg_cost, is_us)}"
                    f"｜現價N/A{note}"
                ),
            })
            continue

        market_value = shares * current
        cost_known = avg_cost is not None
        if not cost_known:
            unknown_cost.append(display)
            if is_us:
                us_net += market_value
            else:
                tw_net += market_value
            # 成本未知的部位不進總報酬 —— 有市值沒成本會把整筆市值當成獲利
            line = (
                f"{display}｜{shares}股"
                f"｜均價未知"
                f"｜現價{_format_price(current, is_us)}"
                f"｜損益未知"
            )
            rows.append({'sort_key': market_value, 'line': line})
            continue

        cost_value = shares * avg_cost
        pnl = market_value - cost_value
        pnl_pct = (current - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
        sign = "+" if pnl_pct >= 0 else ""

        if is_us:
            us_cost += cost_value
            us_value += market_value
            us_net += market_value
        else:
            tw_cost += cost_value
            tw_value += market_value
            tw_net += market_value

        line = (
            f"{display}｜{shares}股"
            f"｜均價{_format_price(avg_cost, is_us)}"
            f"｜現價{_format_price(current, is_us)}"
            f"｜{sign}{pnl_pct:.1f}% {_format_pnl_amount(pnl, is_us)}"
        )
        rows.append({'sort_key': market_value, 'line': line})

    rows.sort(key=lambda r: r['sort_key'], reverse=True)
    lines.extend(r['line'] for r in rows)

    # 總報酬：台美分開算，避免 NTD/USD 混算
    summary_parts = []
    if tw_cost > 0:
        tw_pnl = tw_value - tw_cost
        tw_pct = tw_pnl / tw_cost * 100
        sign = "+" if tw_pct >= 0 else ""
        summary_parts.append(
            f"🇹🇼 台股｜成本 {tw_cost:,.0f}｜市值 {tw_value:,.0f}"
            f"｜{sign}{tw_pct:.1f}% {_format_pnl_amount(tw_pnl, False)}"
        )
    if us_cost > 0:
        us_pnl = us_value - us_cost
        us_pct = us_pnl / us_cost * 100
        sign = "+" if us_pct >= 0 else ""
        summary_parts.append(
            f"🇺🇸 美股｜成本 ${us_cost:,.0f}｜市值 ${us_value:,.0f}"
            f"｜{sign}{us_pct:.1f}% {_format_pnl_amount(us_pnl, True)}"
        )
    if summary_parts:
        lines.append("")
        lines.append("<b>💰 總報酬</b>")
        lines.extend(summary_parts)

    if unknown_cost:
        lines.append(
            f"⚠️ {'、'.join(unknown_cost)} 成本未知（庫存快照沒有成本欄），"
            "已計入淨值但未計入總報酬"
        )

    # 淨值合計（按即時 USD/TWD 折算成 NTD 一個總數）
    if tw_net > 0 or us_net > 0:
        lines.append("")
        rate = _get_usd_twd()
        if rate and us_net > 0:
            net_ntd = tw_net + us_net * rate
            lines.append(
                f"<b>💎 淨值合計</b>：NTD {net_ntd:,.0f}"
                f"（USD/TWD={rate:.2f}）"
            )
        elif rate is None and us_net > 0:
            lines.append(
                f"<b>💎 淨值</b>：台股 NTD {tw_net:,.0f}"
                f" + 美股 USD {us_net:,.0f}（匯率抓取失敗無法合計）"
            )
        else:
            lines.append(f"<b>💎 淨值</b>：NTD {tw_net:,.0f}")

    return "\n".join(lines)
