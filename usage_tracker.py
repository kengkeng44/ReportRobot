"""
記錄 Anthropic API 用量，估算月成本，並依「功能 + 月份」持久化到 Notion。

- 每次 client.messages.create 之後呼叫 track(model, message, feature=...)
  （走 sonnet_client 的呼叫點傳 label= 進來，其餘呼叫點直接帶 feature=）
- /admin/cost-stats endpoint 與 LINE「/用量」顯示當月數字
- 持久化：每月每功能一列寫進 Notion ApiCost DB，沿用 LineQuota 的
  base + 本期 delta 模式 —— 啟動時 load 當月 base，之後 in-memory 累積，
  背景執行緒每隔一段時間把 base+delta 寫回 Notion（不阻塞 API 回應）。
- Notion 不可用時 fallback 純 in-memory（redeploy 歸零，但不影響主流程）。
"""

import threading
import time
from datetime import datetime

from tz_utils import today_tpe


_LOCK = threading.Lock()

# 本期（這次 deploy 之後）的 in-memory 累積，keyed by month → feature
# _MONTHLY[month][feature] = {"calls": n, "web_searches": n,
#                             "tokens": {model: {"input": n, "output": n}}}
_MONTHLY = {}

# 從 Notion load 的當月 base（每月每功能的累計總數），keyed by month → feature
# _NOTION_BASE[month][feature] = {"calls","input_tokens","output_tokens",
#                                 "web_searches","cost_usd"}
_NOTION_BASE = {}
_LOADED_MONTHS = set()          # 已 load 過 Notion base 的月份
_STARTED_AT = datetime.now().isoformat(timespec="seconds")

# 背景持久化
_DIRTY_MONTHS = set()           # 有變動、待寫回 Notion 的月份
_FLUSH_INTERVAL = 30            # 秒；coalesce 多次 track 成一次寫入
_flusher_started = False


# Anthropic 2026 公定價（每 1M tokens，USD）
PRICING = {
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
}
WEB_SEARCH_PRICE_USD = 0.01  # 每次 web_search 呼叫

DEFAULT_FEATURE = "其他"


def _month_now():
    return today_tpe().strftime("%Y-%m")


def _token_cost(tokens_by_model):
    """{model: {"input","output"}} → 估算 USD。"""
    cost = 0.0
    for model, toks in tokens_by_model.items():
        price = PRICING.get(model, {"input": 0, "output": 0})
        cost += (toks.get("input", 0) * price["input"]
                 + toks.get("output", 0) * price["output"]) / 1_000_000
    return cost


# ─────────────────────────────────────────────────────────
# Notion 持久化：load base / 背景 flush
# ─────────────────────────────────────────────────────────

def _ensure_loaded(month):
    """確保當月 base 已從 Notion load 過（每月只 load 一次）。"""
    if month in _LOADED_MONTHS:
        return
    try:
        import notion_db
        if not notion_db.is_configured():
            with _LOCK:
                _LOADED_MONTHS.add(month)
            return
        base = notion_db.api_cost_get_month(month)  # {feature: {...}}
        with _LOCK:
            _NOTION_BASE[month] = base or {}
            _LOADED_MONTHS.add(month)
        if base:
            print(f"[usage] 從 Notion load {month}：{len(base)} 個功能")
    except Exception as e:
        print(f"[usage] load Notion 失敗：{e}")
        with _LOCK:
            _LOADED_MONTHS.add(month)  # 標記避免每次都試


def _start_flusher():
    global _flusher_started
    if _flusher_started:
        return
    _flusher_started = True

    def _loop():
        while True:
            time.sleep(_FLUSH_INTERVAL)
            try:
                flush()
            except Exception as e:
                print(f"[usage] 背景 flush 失敗：{e}")

    t = threading.Thread(target=_loop, name="usage-flusher", daemon=True)
    t.start()


def flush():
    """把有變動的月份的 base+delta 寫回 Notion（best-effort）。"""
    with _LOCK:
        months = list(_DIRTY_MONTHS)
        _DIRTY_MONTHS.clear()
    if not months:
        return
    try:
        import notion_db
        if not notion_db.is_configured():
            return
        for month in months:
            totals = _feature_totals(month)
            for feature, data in totals.items():
                notion_db.api_cost_set(
                    month, feature,
                    calls=data["calls"],
                    input_tokens=data["input_tokens"],
                    output_tokens=data["output_tokens"],
                    web_searches=data["web_searches"],
                    cost_usd=data["cost_usd"],
                )
    except Exception as e:
        print(f"[usage] flush 寫入 Notion 失敗：{e}")
        # 沒寫成功就把月份放回 dirty，下輪再試
        with _LOCK:
            _DIRTY_MONTHS.update(months)


# ─────────────────────────────────────────────────────────
# 累積與統計
# ─────────────────────────────────────────────────────────

def track(model, message, feature=DEFAULT_FEATURE):
    """從 anthropic SDK 的 message 物件抽 usage 累加到（當月, 功能）。失敗靜默 skip。"""
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

    feature = feature or DEFAULT_FEATURE
    month = _month_now()
    _ensure_loaded(month)

    with _LOCK:
        feats = _MONTHLY.setdefault(month, {})
        slot = feats.setdefault(
            feature, {"calls": 0, "web_searches": 0, "tokens": {}}
        )
        slot["calls"] += 1
        slot["web_searches"] += web_search_count
        toks = slot["tokens"].setdefault(model, {"input": 0, "output": 0})
        toks["input"] += in_tok
        toks["output"] += out_tok
        _DIRTY_MONTHS.add(month)

    _start_flusher()


def _feature_delta(month, feature):
    """某月某功能本期 in-memory delta（含估算成本）。"""
    slot = _MONTHLY.get(month, {}).get(feature)
    if not slot:
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                "web_searches": 0, "cost_usd": 0.0}
    in_tok = sum(t["input"] for t in slot["tokens"].values())
    out_tok = sum(t["output"] for t in slot["tokens"].values())
    cost = _token_cost(slot["tokens"]) + WEB_SEARCH_PRICE_USD * slot["web_searches"]
    return {
        "calls": slot["calls"],
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "web_searches": slot["web_searches"],
        "cost_usd": cost,
    }


def _feature_totals(month):
    """某月每功能總數 = Notion base + 本期 delta。回 {feature: {...}}。"""
    _ensure_loaded(month)
    with _LOCK:
        base = dict(_NOTION_BASE.get(month, {}))
        features = set(base) | set(_MONTHLY.get(month, {}))
    out = {}
    for feature in features:
        b = base.get(feature, {})
        d = _feature_delta(month, feature)
        out[feature] = {
            "calls": int(b.get("calls", 0)) + d["calls"],
            "input_tokens": int(b.get("input_tokens", 0)) + d["input_tokens"],
            "output_tokens": int(b.get("output_tokens", 0)) + d["output_tokens"],
            "web_searches": int(b.get("web_searches", 0)) + d["web_searches"],
            "cost_usd": round(float(b.get("cost_usd", 0.0)) + d["cost_usd"], 4),
        }
    return out


def get_stats(month=None):
    """組成 dict 給 endpoint / LINE 指令。含當月每功能與各模型（本期）明細。"""
    month = month or _month_now()
    by_feature = _feature_totals(month)

    total_cost = round(sum(f["cost_usd"] for f in by_feature.values()), 4)
    web_calls = sum(f["web_searches"] for f in by_feature.values())
    web_cost = round(WEB_SEARCH_PRICE_USD * web_calls, 4)

    # 各模型明細只反映本期 deploy 的 in-memory（Notion base 不留模型維度）
    by_model = {}
    with _LOCK:
        for slot in _MONTHLY.get(month, {}).values():
            for model, toks in slot["tokens"].items():
                m = by_model.setdefault(
                    model, {"calls": 0, "input_tokens": 0, "output_tokens": 0}
                )
                m["input_tokens"] += toks["input"]
                m["output_tokens"] += toks["output"]
    for model, m in by_model.items():
        price = PRICING.get(model, {"input": 0, "output": 0})
        m["estimated_cost_usd"] = round(
            (m["input_tokens"] * price["input"]
             + m["output_tokens"] * price["output"]) / 1_000_000, 4
        )

    try:
        import notion_db
        persisted = notion_db.is_configured()
    except Exception:
        persisted = False

    return {
        "month": month,
        "tracking_since": _STARTED_AT,
        "now": datetime.now().isoformat(timespec="seconds"),
        "by_feature": {
            f: {
                "calls": d["calls"],
                "input_tokens": d["input_tokens"],
                "output_tokens": d["output_tokens"],
                "web_searches": d["web_searches"],
                "estimated_cost_usd": round(d["cost_usd"], 4),
            }
            for f, d in sorted(
                by_feature.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True
            )
        },
        "by_model": by_model,
        "web_search_calls": web_calls,
        "web_search_cost_usd": web_cost,
        "total_estimated_cost_usd": total_cost,
        "persisted": persisted,
        "note": (
            "當月累計（Notion 持久化＝跨 redeploy 保留；各模型明細只計本期 deploy）。"
            "成本＝token 定價 + 每次 web_search $0.01。"
        ),
    }
