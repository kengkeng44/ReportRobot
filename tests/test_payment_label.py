"""付款／載具欄：誰的卡、誰的載具、誰記的。"""

import payment_label as pl


def test_labels():
    assert pl.label("📧 國泰通知信", "6120") == "💳 國泰 *6120（仁和）"
    assert pl.label("📧 國泰通知信", "6120", "EM07743420") == "💳 國泰 *6120（仁和）＋🧾 仁和的載具"
    assert pl.label("🧾 載具發票", "", "FU31813259") == "🧾 仁和的載具"
    assert pl.label("📧 富邦轉帳") == "🏦 富邦轉帳（仁和）"
    assert pl.label("📸 截圖辨識") == "📸 截圖辨識（仁和）"
    assert pl.label("💬 LINE私訊", recorder="小螢") == "✍️ 小螢記的"
    assert pl.label("💬 LINE私訊", recorder="🤖 自動") == "✍️ 仁和記的"
    assert pl.label("") == ""


def test_from_props():
    props = {"來源": {"type": "select", "select": {"name": "🧾 載具發票"}},
             "卡末四碼": {"type": "rich_text", "rich_text": []},
             "發票號碼": {"type": "rich_text", "rich_text": [{"plain_text": "FU1"}]},
             "記錄人": {"type": "rich_text", "rich_text": [{"plain_text": "🤖 自動"}]}}
    assert pl.from_props(props) == "🧾 仁和的載具"
