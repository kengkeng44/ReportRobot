"""「買了」不帶參數時回一排常買清單按鈕，點兩下入庫。

原本要打「買了 高麗菜1顆」，手機上打中文是最大的摩擦。改成按「買了」→
跳一排常買的 → 點一下就送出「買了 高麗菜」。

常買清單來自自己的庫存歷史（在庫 + 用完），不夠 10 樣才用預設補滿 ——
第一天沒歷史就給空按鈕列，等於功能不存在。
"""

import pytest

import command_router as cr
import flex_builder
import kitchen
import line_sender


def _row(name, status="在庫"):
    return {"page_id": f"p-{name}-{status}", "name": name, "qty": 1,
            "unit": "顆", "category": "蔬菜", "days_left": 3}


# ── 常買清單（純邏輯）─────────────────────────────────────

def test_frequent_items_ranks_by_count():
    rows = [_row("高麗菜"), _row("高麗菜"), _row("高麗菜"),
            _row("番茄"), _row("番茄"),
            _row("洋蔥")]

    assert kitchen.frequent_items(rows, limit=3, pad=False) == ["高麗菜", "番茄", "洋蔥"]


def test_frequent_items_dedupes():
    rows = [_row("高麗菜"), _row("高麗菜")]

    assert kitchen.frequent_items(rows, limit=10, pad=False) == ["高麗菜"]


def test_frequent_items_respects_limit():
    rows = [_row(f"菜{i}") for i in range(20)]

    assert len(kitchen.frequent_items(rows, limit=10, pad=False)) == 10


def test_frequent_items_ties_keep_first_seen_order():
    """同次數時順序要穩定，不然每次跳出來的按鈕位置都在動。"""
    rows = [_row("洋蔥"), _row("番茄"), _row("高麗菜")]

    assert kitchen.frequent_items(rows, limit=3, pad=False) == ["洋蔥", "番茄", "高麗菜"]


def test_frequent_items_ignores_blank_names():
    rows = [_row("高麗菜"), {"name": ""}, {"name": None}, {}]

    assert kitchen.frequent_items(rows, limit=10, pad=False) == ["高麗菜"]


def test_frequent_items_pads_when_history_is_thin():
    """第一天沒歷史 —— 給空按鈕列等於功能不存在。"""
    items = kitchen.frequent_items([], limit=10)

    assert len(items) == 10


def test_padding_never_duplicates_real_history():
    items = kitchen.frequent_items([_row("雞蛋")], limit=10)

    assert items[0] == "雞蛋"
    assert len(items) == len(set(items))


def test_history_always_outranks_padding():
    rows = [_row("滷味"), _row("滷味")]

    items = kitchen.frequent_items(rows, limit=5)

    assert items[0] == "滷味"


# ── quick reply 訊息結構 ──────────────────────────────────

def test_quick_reply_message_shape():
    msg = flex_builder.quick_reply_text("要加什麼？", [("高麗菜", "買了 高麗菜")])

    assert msg["type"] == "text"
    assert msg["text"] == "要加什麼？"
    item = msg["quickReply"]["items"][0]
    assert item["type"] == "action"
    assert item["action"] == {"type": "message", "label": "高麗菜", "text": "買了 高麗菜"}


def test_quick_reply_caps_at_line_limit():
    """LINE 一則最多 13 顆 quick reply，超過整包會被打回。"""
    opts = [(f"菜{i}", f"買了 菜{i}") for i in range(20)]

    msg = flex_builder.quick_reply_text("x", opts)

    assert len(msg["quickReply"]["items"]) == 13


def test_quick_reply_truncates_long_labels():
    """label 上限 20 字，超過整則會被 LINE 拒收。"""
    long_name = "有機無毒溫室栽培大顆牛番茄特別長的名字"

    msg = flex_builder.quick_reply_text("x", [(long_name, f"買了 {long_name}")])

    assert len(msg["quickReply"]["items"][0]["action"]["label"]) <= 20


def test_quick_reply_omits_key_when_no_options():
    """空的 quickReply 物件會被 LINE 當成格式錯誤，寧可不要這個 key。"""
    msg = flex_builder.quick_reply_text("純文字", [])

    assert "quickReply" not in msg


# ── line_sender 要送得出去 ────────────────────────────────

def test_to_messages_preserves_quick_reply():
    msg = flex_builder.quick_reply_text("要加什麼？", [("高麗菜", "買了 高麗菜")])

    out = line_sender._to_messages(msg)

    assert out[0]["quickReply"]["items"][0]["action"]["text"] == "買了 高麗菜"


def test_to_messages_keeps_quick_reply_on_last_message_only():
    """LINE 只認最後一則的 quickReply，掛在前面那則會靜默消失。"""
    msg = flex_builder.quick_reply_text("要加什麼？", [("高麗菜", "買了 高麗菜")])

    out = line_sender._to_messages(["先講一句", msg])

    assert "quickReply" not in out[0]
    assert "quickReply" in out[-1]


# ── 接進「買了」 ───────────────────────────────────────────

class FakeNotion:
    def __init__(self, rows=None, configured=True):
        self._rows = rows or []
        self.added = []
        self._configured = configured

    def is_configured(self):
        return self._configured

    def pantry_load(self, status="在庫"):
        return [r for r in self._rows if r.get("status", "在庫") == status]

    def pantry_add(self, item):
        self.added.append(item)
        return True


@pytest.fixture
def fake_notion(monkeypatch):
    def _install(**kwargs):
        import sys
        fake = FakeNotion(**kwargs)
        monkeypatch.setitem(sys.modules, "notion_db", fake)
        return fake
    return _install


def _ctx():
    return {"source_type": "user", "user_id": "U1"}


def test_bare_buy_returns_quick_reply(fake_notion):
    fake_notion(rows=[_row("高麗菜"), _row("高麗菜"), _row("番茄")])

    reply = cr.handle("買了", _ctx())

    labels = [i["action"]["label"] for i in reply["quickReply"]["items"]]
    assert labels[0] == "高麗菜"


def test_quick_reply_buttons_send_a_real_buy_command(fake_notion):
    """按鈕送出的字串必須自己 parse 得回來，不然點了沒反應。"""
    fake_notion(rows=[_row("高麗菜")])

    reply = cr.handle("買了", _ctx())

    for item in reply["quickReply"]["items"]:
        sent = item["action"]["text"]
        assert cr.parse(sent) == ("pantry_add", sent.split(" ", 1)[1])


def test_quick_reply_button_actually_adds_to_pantry(fake_notion):
    """整條線走完：點按鈕 → 送出字串 → 真的進庫存。"""
    fake = fake_notion(rows=[_row("高麗菜")])
    reply = cr.handle("買了", _ctx())
    sent = reply["quickReply"]["items"][0]["action"]["text"]

    cr.handle(sent, _ctx())

    assert [i["name"] for i in fake.added] == ["高麗菜"]


def test_bare_buy_hint_is_one_short_line(fake_notion):
    """使用者要的是「只跳按鈕」。quick reply 一定要掛在一則訊息上，
    所以說明砍到一行，完整用法退到 Notion 掛掉時的 fallback。"""
    fake_notion(rows=[_row("高麗菜")])

    reply = cr.handle("買了", _ctx())

    assert "\n" not in reply["text"] and len(reply["text"]) <= 12
    assert reply["quickReply"]["items"]


def test_bare_buy_falls_back_to_text_when_notion_down(fake_notion):
    fake_notion(rows=[], configured=False)

    reply = cr.handle("買了", _ctx())

    assert isinstance(reply, str) and "買了" in reply


def test_buy_cell_sends_bare_command():
    """選單的「買了」要送出裸指令才會跳出常買清單；prompt 只會開鍵盤。"""
    import setup_richmenu as rm

    cells = rm.MENUS["kitchen"]["cells"]
    kind, param = next(c[3] for c in cells if c[0] == "買了")

    assert (kind, param) == ("message", "買了")
    assert cr.parse(param) == ("pantry_add", None)


def test_buy_with_argument_is_unchanged(fake_notion):
    """有帶東西時行為完全照舊，不要因為加按鈕改掉主要路徑。"""
    fake = fake_notion()

    reply = cr.handle("買了 高麗菜1顆", _ctx())

    assert isinstance(reply, str)
    assert [i["name"] for i in fake.added] == ["高麗菜"]
