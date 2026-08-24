"""儀表板的統計與渲染。

collect / compute / render 拆開就是為了讓這兩段能在沒有 Notion 金鑰的
情況下測到 —— 本機沒有 NOTION_TOKEN（金鑰在 Infisical），如果統計邏輯
綁在抓資料裡，這些行為就只能靠上線後用眼睛看。
"""

from datetime import date

import pytest

import build_dashboard as bd


TODAY = date(2026, 8, 25)


def _txn(day, amount, category="餐飲", direction="支出", **kw):
    row = {
        "date": day, "amount": amount, "category": category,
        "direction": direction, "shop": "某店", "status": "授權中",
        "source": "國泰消費彙整", "currency": "TWD",
    }
    row.update(kw)
    return row


def _raw(**kw):
    base = {"generated_at": "2026-08-25T10:00:00+08:00",
            "transactions": [], "networth": [], "holdings": []}
    base.update(kw)
    return base


# ── 金額統計 ──────────────────────────────────────────────

def test_only_current_month_counts():
    raw = _raw(transactions=[_txn("2026-08-03", 100), _txn("2026-07-31", 999)])

    s = bd.compute(raw, today=TODAY)

    assert s["month_total"] == 100
    assert s["month_count"] == 1
    assert s["all_count"] == 2, "總筆數要算全部，不能只算本月"


@pytest.mark.parametrize("direction", ["轉帳", "還款", "收入"])
def test_non_spend_directions_excluded(direction):
    """轉帳與還款是錢在自己口袋間移動，計進去會讓本月支出憑空膨脹。"""
    raw = _raw(transactions=[_txn("2026-08-03", 100),
                             _txn("2026-08-04", 5000, direction=direction)])

    assert bd.compute(raw, today=TODAY)["month_total"] == 100


def test_missing_direction_treated_as_spend():
    """遷移前的舊資料沒有方向欄，當成支出比當成收入安全。"""
    row = _txn("2026-08-03", 100)
    del row["direction"]

    assert bd.compute(_raw(transactions=[row]), today=TODAY)["month_total"] == 100


def test_categories_sorted_by_amount_desc():
    raw = _raw(transactions=[
        _txn("2026-08-01", 100, "餐飲"),
        _txn("2026-08-02", 900, "旅遊"),
        _txn("2026-08-03", 300, "醫療"),
        _txn("2026-08-04", 50, "餐飲"),
    ])

    assert bd.compute(raw, today=TODAY)["categories"] == [
        ("旅遊", 900), ("醫療", 300), ("餐飲", 150)]


def test_missing_category_becomes_uncategorised():
    """空類別要顯示成「未分類」，不能靜靜消失讓總和對不起來。"""
    raw = _raw(transactions=[_txn("2026-08-01", 100, category=None)])

    s = bd.compute(raw, today=TODAY)
    assert s["categories"] == [("未分類", 100)]
    assert sum(v for _, v in s["categories"]) == s["month_total"]


def test_daily_is_sorted_ascending():
    raw = _raw(transactions=[_txn("2026-08-09", 20), _txn("2026-08-02", 10),
                             _txn("2026-08-02", 5)])

    assert bd.compute(raw, today=TODAY)["daily"] == [("2026-08-02", 15),
                                                     ("2026-08-09", 20)]


# ── 資料新鮮度 ────────────────────────────────────────────

def test_stale_days_measured_from_latest_transaction():
    raw = _raw(transactions=[_txn("2026-08-22", 100), _txn("2026-08-01", 100)])

    assert bd.compute(raw, today=TODAY)["stale_days"] == 3


def test_stale_days_none_without_data():
    assert bd.compute(_raw(), today=TODAY)["stale_days"] is None


@pytest.mark.parametrize("days,level", [
    (0, "good"), (1, "good"), (2, "warning"), (3, "warning"),
    (4, "critical"), (30, "critical"), (None, "warning"),
])
def test_freshness_levels(days, level):
    assert bd._freshness(days)[0] == level


def test_freshness_always_has_text():
    """狀態色不能單獨承載意義 —— 每個等級都要有文字說明。"""
    for days in (0, 2, 9, None):
        assert bd._freshness(days)[1].strip()


# ── 淨值與持倉 ────────────────────────────────────────────

def test_latest_net_takes_last_row():
    """networth_load 回傳由舊到新，所以最新的是最後一筆。"""
    raw = _raw(networth=[{"date": "2026-08-01", "net": 100},
                         {"date": "2026-08-20", "net": 250}])

    assert bd.compute(raw, today=TODAY)["latest_net"] == 250


def test_networth_rows_without_value_are_dropped():
    """畫折線前要先濾掉沒淨值的快照，否則會在圖上斷成兩截。"""
    raw = _raw(networth=[{"date": "2026-08-01", "net": None},
                         {"date": "2026-08-02", "net": 100}])

    assert bd.compute(raw, today=TODAY)["networth"] == [
        {"date": "2026-08-02", "net": 100}]


def test_holdings_value_skips_missing():
    raw = _raw(holdings=[{"ticker": "A", "value": 100},
                         {"ticker": "B", "value": None}])

    assert bd.compute(raw, today=TODAY)["holdings_value"] == 100


# ── 渲染 ──────────────────────────────────────────────────

def _full_stats():
    raw = _raw(
        transactions=[_txn("2026-08-24", 300, "餐飲"),
                      _txn("2026-08-20", 1200, "旅遊", source="手動")],
        networth=[{"date": "2026-08-01", "net": 100000},
                  {"date": "2026-08-20", "net": 118000}],
        holdings=[{"ticker": "2330", "display": "台積電", "market": "TW",
                   "shares": 100, "value": 100000, "pnl": 8000, "pnl_pct": 8.7}],
    )
    return bd.compute(raw, today=TODAY)


def test_render_is_self_contained():
    """單檔 HTML 的意義就在離線可開 —— 不能有任何外部資源請求。"""
    out = bd.render(_full_stats())

    assert "<!doctype html>" in out
    assert "http://" not in out
    assert "https://" not in out
    assert "<script src" not in out
    assert "@import" not in out


def test_render_handles_completely_empty_data():
    """沒資料時要好好說「沒資料」，不能炸掉或畫出空白圖。"""
    out = bd.render(bd.compute(_raw(), today=TODAY))

    assert "沒有交易資料" in out
    assert "畫不出趨勢" in out


def test_render_states_the_networth_caveat():
    """淨值其實只是股票市值。畫面上不講清楚，這個數字就會被誤讀。"""
    assert "淨值」目前等於股票市值" in bd.render(_full_stats())


def test_render_escapes_untrusted_text():
    """商店名稱來自信件內容，直接塞進 HTML 會是注入點。"""
    raw = _raw(transactions=[_txn("2026-08-24", 100, shop="<script>x</script>")])

    out = bd.render(bd.compute(raw, today=TODAY))

    assert "<script>x</script>" not in out
    assert "&lt;script&gt;" in out


def test_render_declares_both_dark_mode_scopes():
    """OS 設定與手動切換都要吃得到，缺一個就會有人看到半套配色。"""
    out = bd.render(_full_stats())

    assert "prefers-color-scheme: dark" in out
    assert '[data-theme="dark"]' in out


def test_networth_chart_needs_two_points():
    assert "畫不出趨勢" in bd._networth_chart([{"date": "2026-08-01", "net": 1}])
    assert "<path" in bd._networth_chart([{"date": "2026-08-01", "net": 1},
                                          {"date": "2026-08-02", "net": 2}])


def test_daily_chart_keeps_bars_inside_viewbox():
    """條的右緣不能超出 viewBox，否則最後一根會被切掉。"""
    import re

    daily = [(f"2026-08-{d:02d}", d * 10) for d in range(1, 29)]
    svg = bd._daily_chart(daily)

    for x, w in re.findall(r'<rect class="dbar" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', svg):
        assert float(x) + float(w) <= 720.001


def test_money_formatting():
    assert bd._money(1234567) == "1,234,567"
    assert bd._money(None) == "—"
    assert bd._money(1500, sign=True) == "+1,500"
    assert bd._money(-1500, sign=True).endswith("1,500")
