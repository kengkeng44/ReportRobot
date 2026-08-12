"""台股代號回填。

實際事故：台積電因為近期沒有成交回報，name_to_code 對照表裡沒有它，
於是中文名被當成 ticker 回傳。下游 _is_tw_ticker() 看它不是 4-6 位數字
就誤判成美股，導致抓不到現價、市值與損益全空、淨值快照也少算一檔。
"""

import gmail_reader as gr


def test_mapping_table_wins(monkeypatch):
    monkeypatch.setattr(gr, "lookup_tw_code_by_name", lambda n: "9999")

    assert gr._resolve_tw_code("台積電", {"台積電": "2330"}) == "2330"


def test_falls_back_to_twstock_lookup(monkeypatch):
    """對照表沒有時要反查，不能把中文名當代號。"""
    monkeypatch.setattr(gr, "lookup_tw_code_by_name",
                        lambda n: "2330" if n == "台積電" else None)

    assert gr._resolve_tw_code("台積電", {}) == "2330"
    assert gr._resolve_tw_code("台積電", None) == "2330"


def test_unresolvable_name_returns_name(monkeypatch):
    """真的查不到就維持原樣（至少看得出是哪一檔），但要留 log。"""
    monkeypatch.setattr(gr, "lookup_tw_code_by_name", lambda n: None)

    assert gr._resolve_tw_code("某不存在公司", {}) == "某不存在公司"


def test_empty_name_is_safe(monkeypatch):
    monkeypatch.setattr(gr, "lookup_tw_code_by_name", lambda n: None)
    assert gr._resolve_tw_code("", {}) == ""
    assert gr._resolve_tw_code(None, {}) is None


def test_resolved_code_is_recognised_as_tw():
    """回填後必須通過台股判斷，否則整條鏈還是壞的。"""
    from portfolio import _is_tw_ticker

    assert _is_tw_ticker("2330")
    assert not _is_tw_ticker("台積電"), "這就是修好前的狀態"


def test_real_twstock_resolves_common_names():
    """對真實 twstock 對照表驗一次，確認不是只有 mock 會過。"""
    gr._TW_CODE_CACHE.clear()
    assert gr.lookup_tw_code_by_name("台積電") == "2330"
    assert gr.lookup_tw_code_by_name("鴻海") == "2317"


def test_lookup_is_cached(monkeypatch):
    calls = []

    import twstock  # noqa: F401  確認環境有裝

    gr._TW_CODE_CACHE.clear()
    gr.lookup_tw_code_by_name("台積電")
    cached = dict(gr._TW_CODE_CACHE)

    # 第二次不該再打 twstock
    monkeypatch.setattr(gr, "_TW_CODE_CACHE", cached)
    assert gr.lookup_tw_code_by_name("台積電") == "2330"
