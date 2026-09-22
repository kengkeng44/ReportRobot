from types import SimpleNamespace

import pytest
from anthropic.types import Message, TextBlock, Usage

import sonnet_client


def _msg(text, stop_reason):
    return Message(
        id="msg_x", type="message", role="assistant", model=sonnet_client.MODEL,
        content=[TextBlock(type="text", text=text)],
        stop_reason=stop_reason, stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=5),
    )


@pytest.fixture
def fake_api(monkeypatch):
    state = SimpleNamespace(calls=[], replies=[], tracked=[])

    def create(**kwargs):
        state.calls.append(kwargs)
        return state.replies.pop(0)

    class FakeClient:
        def __init__(self, api_key):
            self.messages = SimpleNamespace(create=create)

    monkeypatch.setattr(sonnet_client.anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(sonnet_client.usage_tracker, "track",
                        lambda model, msg, feature=None: state.tracked.append(model))
    return state


def test_single_turn_request_shape(fake_api):
    fake_api.replies = [_msg("答案", "end_turn")]
    msg = sonnet_client.create("k", "問題", max_tokens=3000, effort="low",
                               tools=[sonnet_client.web_search_tool(3)])
    call = fake_api.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert call["output_config"] == {"effort": "low"}
    assert call["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    assert "temperature" not in call
    assert [b.text for b in msg.content] == ["答案"]
    assert fake_api.tracked == ["claude-sonnet-5"]


def test_no_tools_key_when_not_given(fake_api):
    fake_api.replies = [_msg("ok", "end_turn")]
    sonnet_client.create("k", "q", max_tokens=100, effort="medium")
    assert "tools" not in fake_api.calls[0]


def test_pause_turn_continues_and_merges_content(fake_api):
    fake_api.replies = [_msg("前半", "pause_turn"), _msg("後半", "end_turn")]
    msg = sonnet_client.create("k", "q", max_tokens=100, effort="low")
    assert len(fake_api.calls) == 2
    second = fake_api.calls[1]["messages"]
    assert second[0] == {"role": "user", "content": "q"}
    assert second[1]["role"] == "assistant"
    assert [b.text for b in msg.content] == ["前半", "後半"]
    assert msg.stop_reason == "end_turn"
    assert len(fake_api.tracked) == 2


def test_pause_turn_gives_up_after_limit(fake_api):
    fake_api.replies = [_msg(str(i), "pause_turn") for i in range(10)]
    msg = sonnet_client.create("k", "q", max_tokens=100, effort="low")
    assert len(fake_api.calls) == sonnet_client.MAX_CONTINUATIONS + 1
    assert msg.stop_reason == "pause_turn"


def test_haiku_call_has_no_effort_and_tracks_haiku(fake_api):
    fake_api.replies = [_msg("ok", "end_turn")]
    sonnet_client.create("k", "q", max_tokens=100, model=sonnet_client.HAIKU)
    call = fake_api.calls[0]
    assert call["model"] == sonnet_client.HAIKU
    assert "output_config" not in call
    assert fake_api.tracked == [sonnet_client.HAIKU]
