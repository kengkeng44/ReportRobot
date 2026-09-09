"""pantry_load 的分頁。Notion 單頁上限 100，庫存已經超過 1,100 筆。"""


def _fake_page(name, days):
    return {
        "id": f"page-{name}",
        "properties": {
            "名稱": {"title": [{"plain_text": name}]},
            "剩餘天數": {"formula": {"type": "number", "number": days}},
        },
    }


def _install(monkeypatch, pages_by_call):
    """pages_by_call: [(results, has_more, next_cursor), ...]"""
    import notion_db

    calls = []

    class _FakeDatabases:
        def query(self, **kwargs):
            calls.append(kwargs)
            results, has_more, nxt = pages_by_call[len(calls) - 1]
            out = {"results": results, "has_more": has_more}
            if nxt:
                out["next_cursor"] = nxt
            return out

    class _FakeClient:
        databases = _FakeDatabases()

    monkeypatch.setattr(notion_db, "get_or_create_db", lambda name: "db1")
    monkeypatch.setattr(notion_db, "_get_client", lambda: _FakeClient())
    return calls


def test_pantry_load_follows_next_cursor(monkeypatch):
    """不分頁的話 1,184 筆只會讀到 100 筆 —— 不報錯，庫存只是靜靜變小，
    而「快過期」就變成從 8% 的樣本裡挑。"""
    import notion_db

    first = [_fake_page(f"A{i}", 10) for i in range(100)]
    second = [_fake_page(f"B{i}", 10) for i in range(30)]
    calls = _install(monkeypatch, [(first, True, "c2"), (second, False, None)])

    rows = notion_db.pantry_load()

    assert len(rows) == 130
    assert calls[1]["start_cursor"] == "c2"


def test_pantry_load_keeps_the_status_filter_on_every_page(monkeypatch):
    """第二頁少帶篩選條件的話會把「用完」的也混進來。"""
    import notion_db

    first = [_fake_page("A", 1)] * 100
    second = [_fake_page("B", 1)]
    calls = _install(monkeypatch, [(first, True, "c2"), (second, False, None)])

    notion_db.pantry_load()

    assert calls[0]["filter"] == calls[1]["filter"]
    assert calls[1]["filter"]["select"]["equals"] == "在庫"


def test_pantry_load_stops_without_next_cursor(monkeypatch):
    """has_more 是 True 但沒給 cursor —— 不停下來就是無窮迴圈。"""
    import notion_db

    page = [_fake_page("A", 1)]
    calls = _install(monkeypatch, [(page, True, None)])

    rows = notion_db.pantry_load()

    assert len(rows) == 1
    assert len(calls) == 1


def test_pantry_load_respects_limit(monkeypatch):
    """安全閥：庫存長到上萬筆時不該把整個資料庫拉進記憶體。"""
    import notion_db

    first = [_fake_page(f"A{i}", 10) for i in range(100)]
    calls = _install(monkeypatch, [(first, True, "c2")])

    rows = notion_db.pantry_load(limit=100)

    assert len(rows) == 100
    assert len(calls) == 1
