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
