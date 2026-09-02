"""舊「每日一句」→ 金句庫的搬遷邏輯。

370 筆資料沒辦法逐筆人工檢查,只能靠測試把拆解規則釘住。
所以這份測試的重點不是「有沒有跑」,是**拆錯的時候要看得出來**。

真實樣本(取自使用者搬過來的 ExportBlock)：
    妳當像鳥飛往妳的山                                    ← 沒有出處
    心態買不到,只能靠自己創造。—詹姆士.惠特克              ← 全形破折號
    語言是有聲無形的文章…。-葉聖陶                        ← 半形連字號
    …最微小的事情。—Mel Robbins《https://readingoutpost…》 ← 出處後面卡網址
    一旦你學會放棄,你就會習慣性的https://cmy.tw/007n4L
    。—文斯 · 蘭巴迪                                      ← 句子中間就有網址 + 換行
"""

import import_quotes as iq


# ── 出處拆解 ──────────────────────────────────────────────

def test_no_marker_keeps_whole_sentence():
    """沒有破折號的句子整句留著,出處留空 —— 不要硬拆。"""
    assert iq.split_source("妳當像鳥飛往妳的山") == ("妳當像鳥飛往妳的山", "")


def test_splits_on_em_dash():
    quote, source = iq.split_source("心態買不到,只能靠自己創造。—詹姆士.惠特克")

    assert quote == "心態買不到,只能靠自己創造。"
    assert source == "詹姆士.惠特克"


def test_splits_on_ascii_hyphen():
    """舊資料混用全形破折號與半形連字號,兩種都要認。"""
    quote, source = iq.split_source("語言是有聲無形的文章。-葉聖陶")

    assert quote == "語言是有聲無形的文章。"
    assert source == "葉聖陶"


def test_keeps_book_title_in_source():
    """真實資料裡最長的出處是「書名+作者」13 字。

    書名號是出處的一部分,不能剝掉 —— 剝了會變成殘句。
    """
    quote, source = iq.split_source("讀書是最好的投資。-《寫作是最好的投資》陳立飛")

    assert quote == "讀書是最好的投資。"
    assert source == "《寫作是最好的投資》陳立飛"


def test_handles_double_dash():
    """「--泰戈爾」:句子尾巴不能留一個孤兒破折號。"""
    quote, source = iq.split_source("你又要再錯過繁星了--泰戈爾")

    assert quote == "你又要再錯過繁星了"
    assert source == "泰戈爾"


def test_dash_inside_a_url_is_not_a_split_point():
    """真實樣本:結尾是「…the-5-second-rule-book/》」。

    不遮網址的話,最後一個連字號在網址裡,會拆出一個叫「book/」的作者。
    """
    quote, source = iq.split_source(
        "最微小的事情。—Mel Robbins"
        "《https://readingoutpost.com/recommends/the-5-second-rule-book/》"
    )

    assert quote == "最微小的事情。"
    assert source == "Mel Robbins"


def test_mid_sentence_url_stays_in_the_quote():
    """句子中間的網址是被連結蓋住的某個詞,拿掉句子就破了。

    留著,交給 needs_review 挑出來人工看。
    """
    quote, source = iq.split_source(
        "一旦你學會放棄,你就會習慣性的https://cmy.tw/007n4L\n。—文斯 · 蘭巴迪"
    )

    assert "cmy.tw" in quote
    assert source == "文斯 · 蘭巴迪"


def test_strips_url_from_source():
    """出處後面常卡一個推薦連結。作者名要留,網址要丟 ——
    信裡出現一串裸網址很醜,而且點不了。
    """
    quote, source = iq.split_source(
        "改變一切的是最微小的事情。—Mel Robbins《https://readingoutpost.com/x》"
    )

    assert quote == "改變一切的是最微小的事情。"
    assert source == "Mel Robbins"


def test_long_tail_is_not_a_source():
    """破折號後面太長就不是作者名,是句子的一部分 ——
    寧可不拆,也不要把半句話塞進「出處」欄。
    """
    text = "人生就是這樣—你以為的終點往往只是另一段路的起點而已啦"

    quote, source = iq.split_source(text)

    assert quote == text
    assert source == ""


def test_tail_with_sentence_punctuation_is_not_a_source():
    """尾巴裡有句號逗號 → 那是句子不是人名。"""
    text = "先做再說—想太多,不如動手。"

    assert iq.split_source(text) == (text, "")


def test_only_the_last_marker_counts():
    """句子中間有連字號時,只認最後一個。"""
    quote, source = iq.split_source("知行合一 — 說到做到。—王陽明")

    assert quote == "知行合一 — 說到做到。"
    assert source == "王陽明"


def test_collapses_newlines_inside_the_quote():
    """舊資料的網址後面帶換行,原樣搬進去信裡會斷行斷得很怪。"""
    quote, source = iq.split_source(
        "一旦你學會放棄,你就會習慣性的\n放棄。—文斯 · 蘭巴迪"
    )

    assert "\n" not in quote
    assert source == "文斯 · 蘭巴迪"


def test_marker_at_start_is_not_a_split():
    """整句就是「—某某」的怪資料:不要拆出一個空句子。"""
    quote, source = iq.split_source("—某某某")

    assert quote == "—某某某"
    assert source == ""


def test_blank_input():
    assert iq.split_source("") == ("", "")
    assert iq.split_source(None) == ("", "")


# ── 主題標籤 ──────────────────────────────────────────────

def test_splits_comma_joined_themes():
    assert iq.split_themes("休息, 力量, 思考") == ["休息", "力量", "思考"]


def test_single_theme():
    assert iq.split_themes("改變") == ["改變"]


def test_blank_themes():
    assert iq.split_themes("") == []
    assert iq.split_themes(None) == []


def test_drops_duplicate_and_blank_themes():
    assert iq.split_themes("力量, , 力量, 休息") == ["力量", "休息"]


# ── 匯入計畫 ──────────────────────────────────────────────

def _legacy(text, themes=""):
    return {"text": text, "themes": themes}


def test_plan_skips_quotes_already_in_notion():
    """搬遷要能重跑。第二次跑不該把 370 筆再寫一遍。"""
    rows = [_legacy("已經有了"), _legacy("還沒有")]
    existing = [{"sentence": "已經有了"}]

    to_add, skipped = iq.plan_import(rows, existing)

    assert [r["sentence"] for r in to_add] == ["還沒有"]
    assert len(skipped) == 1


def test_plan_dedups_within_the_source_file():
    """舊資料自己就有重複(370 筆裡只有 368 句不同)。"""
    rows = [_legacy("同一句"), _legacy("同一句")]

    to_add, _ = iq.plan_import(rows, [])

    assert len(to_add) == 1


def test_plan_matches_on_the_split_quote_not_the_raw_text():
    """去重要比對拆完的句子。

    不然同一句話因為出處寫法不同(「—愛因斯坦」vs「-愛因斯坦」)
    會被當成兩句,重跑一次就多一筆。
    """
    rows = [_legacy("用製造問題的腦筋去解決問題是行不通的。—愛因斯坦")]
    existing = [{"sentence": "用製造問題的腦筋去解決問題是行不通的。"}]

    to_add, skipped = iq.plan_import(rows, existing)

    assert to_add == []
    assert len(skipped) == 1


def test_plan_drops_blank_rows():
    rows = [_legacy(""), _legacy("   "), _legacy("真的有句子")]

    to_add, _ = iq.plan_import(rows, [])

    assert [r["sentence"] for r in to_add] == ["真的有句子"]


def test_plan_carries_source_and_themes_through():
    rows = [_legacy("句子。—作者", themes="改變, 力量")]

    to_add, _ = iq.plan_import(rows, [])

    assert to_add[0] == {
        "sentence": "句子。",
        "source": "作者",
        "themes": ["改變", "力量"],
    }


# ── 預覽 ──────────────────────────────────────────────────

def test_review_flags_rows_that_did_not_split():
    """沒拆出出處的要單獨列出來 —— 那些是人工要掃一眼的。"""
    planned = [
        {"sentence": "沒出處", "source": "", "themes": []},
        {"sentence": "有出處", "source": "某人", "themes": []},
    ]

    flagged = iq.needs_review(planned)

    assert [r["sentence"] for r in flagged] == ["沒出處"]


def test_review_flags_leftover_urls():
    """句子本體裡還留著網址的也要看 —— 信裡出現裸網址很醜。"""
    planned = [{"sentence": "句子 https://x.tw/a 後面", "source": "某人",
                "themes": []}]

    assert len(iq.needs_review(planned)) == 1
