"""AI 補位:語句庫沒有到期的句子時,現生一句。

「現生」不等於「拋棄式」—— 生出來的句子會寫回語句庫並進入複習循環,
否則使用者不貼檔的日子庫永遠長不大。
"""

import prompts


def test_prompt_takes_language_and_avoid_block():
    text = prompts.DAILY_PHRASE_PROMPT.format(
        language="西班牙文", avoid_block="",
    )

    assert "西班牙文" in text


def test_prompt_asks_for_three_labelled_lines():
    """解析靠這三個標籤,prompt 改掉標籤就會安靜地解析失敗。"""
    text = prompts.DAILY_PHRASE_PROMPT.format(language="英文", avoid_block="")

    assert "句子：" in text
    assert "意思：" in text
    assert "提示：" in text


def test_avoid_block_is_injected():
    text = prompts.DAILY_PHRASE_PROMPT.format(
        language="英文", avoid_block="- Play it by ear.",
    )

    assert "Play it by ear." in text


from datetime import date

import phrasebook


D = date(2026, 9, 1)


# ── 解析 AI 回覆 ──────────────────────────────────────────

def test_parse_ai_reads_three_lines():
    out = phrasebook.parse_ai(
        "句子：Play it by ear.\n意思：再看情況決定吧\n提示：口語很常用"
    )

    assert out == {
        "sentence": "Play it by ear.",
        "meaning": "再看情況決定吧",
        "note": "口語很常用",
    }


def test_parse_ai_tolerates_halfwidth_colon_and_spaces():
    """模型偶爾會回半形冒號。為了這個丟掉一句已經生好的句子不划算。"""
    out = phrasebook.parse_ai(
        "  句子: Me da igual. \n  意思: 我都可以\n  提示: 口語"
    )

    assert out["sentence"] == "Me da igual."
    assert out["meaning"] == "我都可以"


def test_parse_ai_returns_none_without_a_sentence():
    """沒有句子就沒有東西可教 —— 意思和提示都是配角。"""
    assert phrasebook.parse_ai("意思：某某\n提示：某某") is None
    assert phrasebook.parse_ai("") is None


def test_parse_ai_allows_missing_meaning_and_note():
    out = phrasebook.parse_ai("句子：Hello.")

    assert out == {"sentence": "Hello.", "meaning": "", "note": ""}


# ── daily_three:Notion + AI 的整合 ──────────────────────

class FakeStore:
    """把 notion_db 的五個函式換成記憶體版。"""

    def __init__(self, phrases=None, quotes=None):
        self._phrases = phrases or {}
        self.quotes = quotes or []
        self.advanced = []
        self.added = []
        self.marked = []

    def phrases_load(self, language, limit=500):
        return list(self._phrases.get(language, []))

    def phrase_advance(self, page_id, fields):
        self.advanced.append((page_id, fields))
        return True

    def phrase_add(self, sentence, language, meaning="", note="",
                   source="AI生成", day=None, due=None):
        self.added.append({"sentence": sentence, "language": language,
                           "source": source, "due": due})
        return True

    def quotes_load(self, limit=500):
        return list(self.quotes)

    def quote_mark_seen(self, page_id, today):
        self.marked.append((page_id, today))
        return True


def _install_store(monkeypatch, store, ai=None):
    monkeypatch.setattr(phrasebook, "_store", lambda: store)
    monkeypatch.setattr(phrasebook, "_ai", ai or (lambda prompt: ""))


def _prow(page_id, sentence, due=None, appeared=0):
    return {"page_id": page_id, "sentence": sentence, "meaning": "",
            "note": "", "appeared": appeared, "due": due}


def test_daily_three_uses_library_when_something_is_due(monkeypatch):
    store = FakeStore(phrases={
        "英文": [_prow("e1", "Play it by ear.", due="2026-08-01")],
        "西班牙文": [_prow("s1", "Me da igual.", due="2026-08-01")],
    }, quotes=[{"page_id": "q1", "sentence": "金句", "source": "",
                "last_seen": None}])
    called = []
    _install_store(monkeypatch, store, ai=lambda p: called.append(p) or "")

    text = phrasebook.daily_three(D)

    assert "Play it by ear." in text
    assert "Me da igual." in text
    assert "金句" in text
    assert called == []          # 庫裡有貨就不該花 AI 的錢


def test_daily_three_advances_schedule_for_picked_rows(monkeypatch):
    store = FakeStore(phrases={"英文": [_prow("e1", "A", due="2026-08-01")]})
    _install_store(monkeypatch, store)

    phrasebook.daily_three(D)

    page_id, fields = store.advanced[0]
    assert page_id == "e1"
    assert fields["appeared"] == 1
    assert fields["due"] == date(2026, 9, 2)


def test_daily_three_marks_quote_as_seen(monkeypatch):
    store = FakeStore(quotes=[{"page_id": "q1", "sentence": "金句",
                               "source": "", "last_seen": None}])
    _install_store(monkeypatch, store)

    phrasebook.daily_three(D)

    assert store.marked == [("q1", D)]


def test_daily_three_falls_back_to_ai_when_nothing_due(monkeypatch):
    """庫是空的、或都還沒到期 —— 使用者每天都該有一句可看。"""
    store = FakeStore()
    _install_store(monkeypatch, store,
                   ai=lambda p: "句子：Fresh one.\n意思：新的\n提示：測試")

    assert "Fresh one." in phrasebook.daily_three(D)


def test_ai_generated_rows_go_back_into_the_library(monkeypatch):
    """生完就丟的話,不貼檔的日子庫永遠長不大。"""
    store = FakeStore()
    _install_store(monkeypatch, store,
                   ai=lambda p: "句子：Fresh one.\n意思：新的\n提示：測試")

    phrasebook.daily_three(D)

    added = [a for a in store.added if a["language"] == "英文"][0]
    assert added["sentence"] == "Fresh one."
    assert added["source"] == "AI生成"
    assert added["due"] == date(2026, 9, 2)     # 明天,進入複習循環


def test_daily_three_survives_ai_failure(monkeypatch):
    """AI 掛掉不能讓整封信少掉別的區塊。"""
    store = FakeStore(quotes=[{"page_id": "q1", "sentence": "金句",
                               "source": "", "last_seen": None}])

    def boom(prompt):
        raise RuntimeError("anthropic down")

    _install_store(monkeypatch, store, ai=boom)

    text = phrasebook.daily_three(D)

    assert "金句" in text
    assert "[EN]" not in text


def test_daily_three_survives_notion_failure(monkeypatch):
    """Notion 掛掉時 phrases_load 回 [] —— 走 AI 補位,不是整段消失。"""
    store = FakeStore()
    _install_store(monkeypatch, store,
                   ai=lambda p: "句子：Fallback.\n意思：備援\n提示：x")

    assert "Fallback." in phrasebook.daily_three(D)


def test_daily_three_returns_none_when_everything_fails(monkeypatch):
    store = FakeStore()

    def boom(prompt):
        raise RuntimeError("down")

    _install_store(monkeypatch, store, ai=boom)

    assert phrasebook.daily_three(D) is None


def test_avoid_block_is_empty_for_an_empty_library(monkeypatch):
    """庫是空的時候不要附一段空的「別再生這些」。"""
    assert phrasebook._avoid_block([]) == ""


def test_avoid_block_lists_existing_sentences(monkeypatch):
    out = phrasebook._avoid_block([{"sentence": "A"}, {"sentence": "B"}])

    assert "- A" in out
    assert "- B" in out
