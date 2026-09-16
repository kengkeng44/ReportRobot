"""Sonnet 5 共用呼叫:模型名稱、web_search 設定、pause_turn 續跑、用量記錄。

- claude-sonnet-5 預設會先思考(adaptive thinking),思考也算輸出、也吃 max_tokens,
  所以各呼叫點用 effort 控制思考深度,max_tokens 要比 sonnet-4-5 時代留更多空間。
- 不接受 temperature / top_p / top_k(非預設值會 400)。
- 搜尋用舊版 web_search_20250305。2026-09-16 實測同一題:新版 20260209(動態過濾)
  會多跑 code execution、多吃搜尋次數,輸入 25.6k token 且撞到 max_uses 沒答出來;
  舊版輸入 12k token、答對。新版不適合這種「搜 1-3 次就整理」的用法。
- 搜尋類 server tool 回合太長時 stop_reason 會是 pause_turn,要把 assistant 內容送回去續跑。
"""

import anthropic

import usage_tracker

MODEL = "claude-sonnet-5"
MAX_CONTINUATIONS = 3


def web_search_tool(max_uses):
    return {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}


def create(api_key, prompt, *, max_tokens, effort, tools=None):
    """呼叫 Sonnet 5;回傳最後一個 message,content 換成所有回合累積的 blocks。

    每個回合都各自記進 usage_tracker,呼叫端不用再 track。
    """
    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]
    kwargs = {"model": MODEL, "max_tokens": max_tokens, "output_config": {"effort": effort}}
    if tools:
        kwargs["tools"] = tools

    blocks = []
    msg = None
    for _ in range(MAX_CONTINUATIONS + 1):
        msg = client.messages.create(messages=messages, **kwargs)
        usage_tracker.track(MODEL, msg)
        blocks.extend(msg.content)
        if msg.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": msg.content}]
    return msg.model_copy(update={"content": blocks})
