"""雷達的計算與渲染。collect 要連 Yahoo／證交所，這裡只測不碰網路的部分。"""

import json
import re

import radar


def _rising(n=260, start=100.0, step=0.5):
    return [start + i * step for i in range(n)]


def test_price_metrics_uptrend():
    closes = _rising()
    m = radar.price_metrics(closes, [1000] * 230 + [2000] * 30)
    assert m["px"] == closes[-1]
    assert m["ma50"] > m["ma150"] > m["ma200"]
    assert m["ma200up"] is True
    assert m["hi52"] == closes[-1]
    assert m["lo52"] == min(closes[-252:])
    assert m["chg"] > 0
    assert m["vol"] == 1.33  # 20 日均 2000 ÷ 60 日均 1500
    assert m["r3m"] is not None and m["rsraw"] is not None


def test_price_metrics_short_history_has_nones():
    m = radar.price_metrics(_rising(30), [])
    assert m["ma50"] is None and m["ma200"] is None
    assert m["ma200up"] is False
    assert m["vol"] is None
    assert m["rsraw"] is None


def test_price_metrics_empty():
    assert radar.price_metrics([], []) == {}


def test_rank_rs_orders_by_raw():
    rows = [{"rsraw": 5}, {"rsraw": -3}, {"rsraw": 20}, {"rsraw": None}]
    radar.rank_rs(rows)
    assert rows[2]["rs"] > rows[0]["rs"] > rows[1]["rs"]
    assert rows[3]["rs"] is None
    assert rows[2]["rs"] == 99


def test_parse_revenue_twse_shape():
    rows = [
        {"出表日期": "1151105", "資料年月": "11510", "公司代號": "2049", "公司名稱": "上銀",
         "營業收入-去年同月增減(%)": "30.14"},
        {"資料年月": "11510", "公司代號": "1590", "營業收入-去年同月增減(%)": "-2.5"},
        {"資料年月": "11510", "公司代號": "9999", "營業收入-去年同月增減(%)": ""},
    ]
    got = radar.parse_revenue(rows)
    assert got["2049"] == (30.1, "2026/10")
    assert got["1590"] == (-2.5, "2026/10")
    assert "9999" not in got


def test_assemble_and_render_roundtrip():
    prices = {"2049": ("2049.TW", _rising(), [1000] * 260),
              "TSLA": ("TSLA", _rising(260, 300, -0.5), [5] * 260)}
    bench = {"tw": _rising(), "us": _rising(), "vix": [15.0] * 260}
    d = radar.assemble(prices, {"2049": (30.1, "2026/9")}, bench)
    by = {r["c"]: r for r in d["stocks"]}
    assert by["2049"]["rev"] == 30.1 and by["2049"]["est"] == "up"
    assert by["2049"]["rs"] > by["TSLA"]["rs"]
    assert by["9880.HK"]["loss"] is True
    assert "1597" in d["missing"] and "2049" not in d["missing"]
    assert all(link["u"].startswith("https://") for r in d["stocks"] for link in r["links"])
    html = radar.render(d)
    blob = re.search(r"var RADAR=(.*?);\n</script>", html, re.S).group(1)
    assert json.loads(blob)["stocks"][0]["c"] == "2049"
    assert "/*__RADAR_DATA__*/" not in html


def test_render_escapes_script_close():
    html = radar.render({"at": "x", "regime": {}, "missing": [],
                         "stocks": [{"c": "</script>"}]})
    assert "</script>\"" not in html
