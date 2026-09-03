"""每日個人報的卡片版型。

版型來自使用者提供的 digest_preview.html（2026-08-26）：米色底 + 白卡 +
圓角 + 棕色標題。使用者指定的區塊順序是 **待辦 → 財務 → 買菜**，
跟範本原本的順序不同。

內容一律當純文字處理再 escape —— 卡片內會出現商家名、食材名這類來自
Notion 與信件解析的字串，含 & 或 < 會把版面弄壞。escape 之後才把換行
轉成 <br>，排版保留、結構不受內容影響。
"""

import digest


def test_blocks_render_in_given_order():
    html = digest.build_digest_html("2026-08-26", [
        ("📋 今日待辦", "回覆房東租約"),
        ("💳 財務概況", "本月合計 NT$18,600"),
        ("🍳 冰箱快過期", "高麗菜　今天到期"),
    ])

    assert html.index("今日待辦") < html.index("財務概況") < html.index("冰箱快過期")


def test_empty_blocks_are_dropped():
    """沒資料的區塊不要留一張空卡片。"""
    html = digest.build_digest_html("2026-08-26", [
        ("📋 今日待辦", "回覆房東租約"),
        ("💳 財務概況", ""),
        ("🍳 冰箱快過期", None),
    ])

    assert "今日待辦" in html
    assert "財務概況" not in html
    assert "冰箱快過期" not in html


def test_date_appears_in_header():
    html = digest.build_digest_html("2026-08-26", [("📋 待辦", "x")])

    assert "2026-08-26" in html


def test_newlines_become_br():
    html = digest.build_digest_html("2026-08-26", [("📋 待辦", "第一行\n第二行")])

    assert "第一行<br>第二行" in html


def test_content_is_escaped():
    """商家名含 & 或 < 不能把版面弄壞。"""
    html = digest.build_digest_html("2026-08-26", [("🧾 消費", "全家 & Co. <測試>")])

    assert "&amp;" in html
    assert "&lt;測試&gt;" in html
    assert "<測試>" not in html


def test_title_is_escaped_too():
    html = digest.build_digest_html("2026-08-26", [("A & B", "x")])

    assert "A &amp; B" in html


def test_all_blocks_empty_returns_none():
    """全空就不要寄一封只有標題的信。"""
    assert digest.build_digest_html("2026-08-26", [("📋 待辦", "")]) is None
    assert digest.build_digest_html("2026-08-26", []) is None


def test_uses_template_card_styling():
    """版型要跟使用者給的範本一致 —— 米色底、白卡、圓角。"""
    html = digest.build_digest_html("2026-08-26", [("📋 待辦", "x")])

    assert "#f5f2ec" in html      # 背景
    assert "border-radius:12px" in html
    assert "#5b4636" in html      # 標題色


def test_footer_present():
    html = digest.build_digest_html("2026-08-26", [("📋 待辦", "x")])

    assert "ReportRobot" in html


# ── 圖片卡片(2026-09-01)────────────────────────────────

def test_three_tuple_block_renders_an_image():
    html = digest.build_digest_html("2026-09-01", [
        ("📊 本月消費分布", "本月合計 NT$12,345", "spending"),
    ])

    assert 'src="cid:spending"' in html
    assert "本月合計 NT$12,345" in html


def test_two_tuple_blocks_still_work():
    """既有呼叫端全是兩元組 —— 向後相容不能破。"""
    html = digest.build_digest_html("2026-09-01", [("📋 待辦", "x")])

    assert "待辦" in html
    assert "<img" not in html


def test_mixed_tuples_render_together():
    html = digest.build_digest_html("2026-09-01", [
        ("📋 待辦", "繳費"),
        ("📊 分布", "合計 NT$100", "pie"),
    ])

    assert html.index("待辦") < html.index("分布")
    assert 'cid:pie' in html


def test_image_block_without_cid_is_plain():
    """cid 給 None 時退回純文字卡片,不要產出壞掉的 <img src="cid:None">。"""
    html = digest.build_digest_html("2026-09-01", [("📊 分布", "合計", None)])

    assert "<img" not in html
    assert "合計" in html


def test_image_block_with_empty_text_is_dropped():
    """有圖但沒文字仍然算空 —— 純文字版會看到一張空卡片。"""
    html = digest.build_digest_html("2026-09-01", [
        ("📋 待辦", "x"), ("📊 分布", "", "pie"),
    ])

    assert "分布" not in html


def test_image_is_responsive():
    """信在手機上開的機會比桌機高,圖不能撐破卡片。"""
    html = digest.build_digest_html("2026-09-01", [("📊 分布", "合計", "pie")])

    assert "max-width:100%" in html
