"""
記錄 Anthropic API 用量，方便估算月成本。
- 每次 client.messages.create 之後呼叫 track(model, message)
- /admin/cost-stats endpoint 顯示累積數字
- in-memory 計數，Railway redeploy 重啟會歸零（可接受，看當期用量趨勢就好）
"""

import threading
from datetime import datetime


_LOCK = threading.Lock()

_STATS = {
    "started_at": datetime.now().isoformat(timespec="seconds"),
    "calls": {},          # model -> count
    "tokens": {},         # model -> {"input": n, "output": n}
    "web_searches": 0,    # 累積 web_search 呼叫次數
}


# Anthropic 2025 公定價（每 1M tokens，USD）
PRICING = {
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
}
WEB_SEARCH_PRICE_USD = 0.01  # 每次 web_search 呼叫


def track(model, message):
    """從 anthropic SDK 的 message 物件抽 usage 欄位累加。失敗就靜默 skip。"""
    if message is None:
        return
    try:
        usage = message.usage
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
    except Exception:
        return

    # 試抽 web_search 用量（SDK 0.96+ 有 server_tool_use.web_search_requests）
    web_search_count = 0
    try:
        server_use = getattr(usage, "server_tool_use", None)
        if server_use:
            web_search_count = int(
                getattr(server_use, "web_search_requests", 0) or 0
            )
    except Exception:
        pass

    with _LOCK:
        _STATS["calls"][model] = _STATS["calls"].get(model, 0) + 1
        slot = _STATS["tokens"].setdefault(model, {"input": 0, "output": 0})
        slot["input"] += in_tok
        slot["output"] += out_tok
        _STATS["web_searches"] += web_search_count


def get_stats():
    """組成 dict 給 endpoint 回傳。含估算成本。"""
    with _LOCK:
        by_model = {}
        total_token_cost = 0.0
        for model, toks in _STATS["tokens"].items():
            price = PRICING.get(model, {"input": 0, "output": 0})
            cost = (toks["input"] * price["input"]
                    + toks["output"] * price["output"]) / 1_000_000
            total_token_cost += cost
            by_model[model] = {
                "calls": _STATS["calls"].get(model, 0),
                "input_tokens": toks["input"],
                "output_tokens": toks["output"],
                "estimated_cost_usd": round(cost, 4),
            }
        web_search_cost = round(WEB_SEARCH_PRICE_USD * _STATS["web_searches"], 4)
        return {
            "tracking_since": _STATS["started_at"],
            "now": datetime.now().isoformat(timespec="seconds"),
            "by_model": by_model,
            "web_search_calls": _STATS["web_searches"],
            "web_search_cost_usd": web_search_cost,
            "total_estimated_cost_usd": round(total_token_cost + web_search_cost, 4),
            "note": (
                "in-memory counter; Railway redeploy resets to 0. "
                "Cost = token-based pricing + $0.01 per web_search call."
            ),
        }
