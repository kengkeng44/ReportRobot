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


def test_ttm_eps_yoy():
    assert radar.ttm_eps_yoy([1, 1, 1, 1, 2, 2, 2, 2]) == (100.0, False)
    # 去年同期虧損：年增算不出來，但近四季賺錢
    assert radar.ttm_eps_yoy([-1, -1, -1, 0, 1, 1, 1, 1]) == (None, False)
    # 近四季虧損
    assert radar.ttm_eps_yoy([1, 1, 1, 1, -1, -1, 0, 0]) == (-150.0, True)
    assert radar.ttm_eps_yoy([1, 1]) == (None, False)


def test_est_change_and_inst_ratio():
    assert radar.est_change(11, 10) == 10.0
    assert radar.est_change(-0.5, -1) == 50.0  # 虧損縮小也算上修
    assert radar.est_change(None, 10) is None and radar.est_change(1, 0) is None
    assert radar.inst_ratio(100, [100] * 20) == 5.0
    assert radar.inst_ratio(100, [100] * 10) is None


def test_index_of_local_market():
    assert radar.index_of("2049", "tw") == "^TWII"
    assert radar.index_of("6861.T", "gl") == "^N225"
    assert radar.index_of("300124.SZ", "gl") == "510300.SS"
    assert radar.index_of("NVDA", "gl") == "^GSPC"


def test_assemble_uses_extra_sources():
    prices = {"2049": ("2049.TW", _rising(), [1000] * 260),
              "6861.T": ("6861.T", _rising(), [10] * 260),
              "0050": ("0050.TW", _rising(), [1000] * 260)}
    extra = {
        "index": {"^TWII": _rising(260, 100, 0.1), "^N225": _rising(260, 100, 1.0)},
        "yahoo": {"6861.T": {"rev": 36.8, "eps": 47.9, "est": 8.6}, "2049": {"est": 8.9, "rev": 1.0}},
        "eps": {"2049": (124.4, False)},
        "inst": {"2049": 2000, "0050": -99999},
    }
    d = radar.assemble(prices, {"2049": (30.1, "2026/9")}, {}, extra)
    by = {r["c"]: r for r in d["stocks"] + d["watch"]}
    a, k, etf = by["2049"], by["6861.T"], by["0050"]
    assert a["rev"] == 30.1 and a["revAt"] == "2026/9"  # 台股月營收優先於 Yahoo
    assert a["eps"] == 124.4 and a["epsAt"] == "近四季" and a["est"] == 8.9
    assert a["inst"] == 10.0  # 2000 ÷ (1000 × 20)
    assert a["rsx"] > 0  # 漲得比加權快
    assert k["rev"] == 36.8 and k["revAt"] == "最近一季" and k["eps"] == 47.9
    assert k["rsx"] < 0  # 漲得比日經慢
    assert "inst" not in k and "inst" not in etf and "rev" not in etf


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


def test_refresh_saves_snapshot_and_keeps_old_on_total_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(radar, "SNAPSHOT_PATH", str(tmp_path / "snap.json"))
    monkeypatch.setattr(radar, "_snap", {"data": None})
    good = ({"2049": ("2049.TW", _rising(), [1000] * 260)}, {}, {})
    monkeypatch.setattr(radar, "collect", lambda: good)
    d = radar.refresh()
    assert d["stocks"][0]["px"] is not None
    # 行程重啟：記憶體清掉，從磁碟讀回來
    monkeypatch.setattr(radar, "_snap", {"data": None})
    assert radar.snapshot()["at"] == d["at"]
    # 一檔都沒抓到：保留舊的
    monkeypatch.setattr(radar, "collect", lambda: ({}, {}, {}))
    assert radar.refresh()["at"] == d["at"]


def test_render_without_snapshot():
    html = radar.render(None)
    assert "var RADAR=null;" in html
