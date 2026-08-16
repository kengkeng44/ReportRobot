"""PTT joke 板抓取的純函式測試。不打網路,HTML 用 fixture。"""

import joke_sources as js


# ── 標題過濾 ──────────────────────────────────────────────

def test_keeps_text_joke_tags():
    assert js._wanted("[猜謎] 音速小子去墾丁 猜一公司")
    assert js._wanted("[耍冷] 劉備怎麼死的")
    assert js._wanted("[ＸＤ] 中國可以玩皮克敏嗎？")


def test_drops_image_and_admin_tags():
    """趣圖/影音的笑點在圖裡,推到 LINE 只會看到標題。"""
    assert not js._wanted("[趣圖] 爆笑漫畫趣圖第一百二十五篇")
    assert not js._wanted("[影音] 這部超好笑")
    assert not js._wanted("[公告] 就可板規")
    assert not js._wanted("[閒聊] 置底閒聊文")


def test_drops_forwarded_and_deleted():
    assert not js._wanted("Fw: [分享] 麥當勞蝦堡 緊急下架！")
    assert not js._wanted("(本文已被刪除) [somebody]")


def test_keeps_untagged():
    """板上約一成的文沒下 tag,不該整批丟掉。"""
    assert js._wanted("沒有標籤的笑話")


# ── 推文數 ────────────────────────────────────────────────

def test_parse_heat():
    assert js._parse_heat("爆") == 100
    assert js._parse_heat("37") == 37
    assert js._parse_heat("") == 0
    assert js._parse_heat("XX") == -100      # 被噓爆的排到最後


# ── 系列文去重 ────────────────────────────────────────────

def test_series_key_ignores_numbers():
    """「推特上在夯什麼 Part.2269 / 2287」要被認成同一個系列。"""
    a = js._series_key("[耍冷] 推特上在夯什麼 Part.2269")
    b = js._series_key("[耍冷] 推特上在夯什麼 Part.2287")
    assert a == b


def test_series_key_keeps_different_titles_apart():
    assert js._series_key("[猜謎] 劉備怎麼死的") != js._series_key("[猜謎] 曹操怎麼死的")


# ── 內文清理 ──────────────────────────────────────────────

def _html(body):
    return f"""<html><body><div id="main-content">
<div class="article-metaline"><span class="article-meta-tag">作者</span></div>
{body}
<div class="push"><span class="push-tag">推</span><span class="push-content">: 好笑</span></div>
</div></body></html>"""


def test_clean_body_strips_signature():
    """PTT 簽名檔分隔線長度不一,--/----/----- 都要切掉。"""
    body, _ = js._clean_body(_html("劉備怎麼死的\n備 害死的\n--\n我的簽名檔\n粉絲團"))
    assert "備 害死的" in body
    assert "簽名檔" not in body


def test_clean_body_strips_app_signature():
    """JPTT / BePTT 會自己加一行 Sent from,不是標準分隔線。"""
    body, _ = js._clean_body(_html("小明的笑話\n----- \nSent from JPTT on my iPhone"))
    assert "小明的笑話" in body
    assert "JPTT" not in body
    assert "iPhone" not in body


def test_clean_body_drops_pushes_and_meta():
    body, _ = js._clean_body(_html("正文在這"))
    assert "正文在這" in body
    assert "好笑" not in body      # 推文不是內文
    assert "作者" not in body


def test_clean_body_removes_spoiler_dots():
    """猜謎用一整排 . 防雷,留著會佔滿推播版面。"""
    body, _ = js._clean_body(_html("什麼鳥生不出小鳥\n.\n.\n.\n燕子(swallow)"))
    assert "什麼鳥生不出小鳥" in body
    assert "燕子" in body
    assert "\n.\n" not in body


def test_clean_body_counts_links():
    body, links = js._clean_body(_html("看圖\nhttps://i.imgur.com/a.jpg\nhttps://i.imgur.com/b.jpg"))
    assert links == 2
    assert "imgur" not in body


def test_clean_body_no_main_content():
    body, links = js._clean_body("<html><body>沒有 main-content</body></html>")
    assert body == ""
    assert links == 0


# ── 圖片文判定 ────────────────────────────────────────────

def test_image_post_detected():
    """圖片系列文洗完只剩「今天只有兩篇/大概是這樣」這種陪襯字。"""
    assert js._is_image_post("今天只有兩篇\n第2269集\n大概是這樣", 3)


def test_long_text_with_links_is_not_image_post():
    """有附連結但正文夠長的,還是真的笑話。"""
    assert not js._is_image_post("字" * 120, 3)


def test_text_only_post_is_not_image_post():
    assert not js._is_image_post("備 害死的", 0)
