"""Apple 健康 → Notion「📔 生活紀錄 / ❤️ 健康」。

iPhone 的健康資料沒有雲端 API，只能由手機自己推出來。支援兩種推法，都 POST 到 /health：

1. iPhone「捷徑」自動化（免費）：每天 23:50 跑一次，把今天的數字組成平的 JSON：
     {"date": "2026-11-17", "steps": 8123, "sleep_hours": 7.2, "resting_hr": 58, ...}
   欄位名用下面 _FIELDS 的英文 key（也收中文欄名）；值是字串也行（"8,123"、"7.2"）。
2. Health Auto Export App（付費、最省事）：REST API 自動化，格式選 JSON、
   彙總選「Day」，它送的 {"data": {"metrics": [...], "workouts": [...]}} 直接吃。

一天一列，用「日期」找；同一天再推只更新這次有帶的欄位，其他欄位不動
（早上推睡眠、晚上推步數可以分兩次）。順便連到那天的 📅 每日紀錄（沒有就建一列，
行程欄留空，晚上 OwnTracks 整理照樣會填）。

設定（Infisical）：
- HEALTH_TOKEN：手機送的時候帶 Authorization: Bearer <HEALTH_TOKEN>。沒設 = 端點關閉
- HEALTH_DB_ID：可不設，預設就是現在那張「❤️ 健康」
"""

import hmac
import os
import re
from datetime import date

import notion_db

HEALTH_DB_ID = os.environ.get("HEALTH_DB_ID", "41b7854a2f6c810fa853dbdd43bf9c53")

_WEEKDAYS = "一二三四五六日"

# key → (Notion 欄名, 型別)。型別 pct = 百分比欄（Notion 存 0.98 代表 98%）
_FIELDS = {
    "steps":          ("步數", "int"),
    "distance_km":    ("走路跑步公里", "num"),
    "flights":        ("爬樓層", "int"),
    "active_kcal":    ("活動卡路里", "int"),
    "exercise_min":   ("運動分鐘", "int"),
    "stand_hours":    ("站立小時", "int"),
    "sleep_hours":    ("睡眠小時", "num"),
    "deep_hours":     ("深層睡眠小時", "num"),
    "rem_hours":      ("REM睡眠小時", "num"),
    "resting_hr":     ("靜止心率", "int"),
    "avg_hr":         ("平均心率", "int"),
    "hrv_ms":         ("心率變異HRV", "int"),
    "spo2":           ("血氧", "pct"),
    "respiratory":    ("呼吸速率", "num"),
    "weight_kg":      ("體重", "num"),
    "body_fat":       ("體脂率", "pct"),
    "mindful_min":    ("正念分鐘", "int"),
    "water_ml":       ("喝水毫升", "int"),
}
_BY_ZH = {zh: key for key, (zh, _) in _FIELDS.items()}

# Health Auto Export 的 metric name → 我們的 key（取 qty，心率取 Avg）
_HAE_METRICS = {
    "step_count": "steps",
    "walking_running_distance": "distance_km",
    "flights_climbed": "flights",
    "active_energy": "active_kcal",
    "apple_exercise_time": "exercise_min",
    "apple_stand_hour": "stand_hours",
    "resting_heart_rate": "resting_hr",
    "heart_rate": "avg_hr",
    "heart_rate_variability": "hrv_ms",
    "blood_oxygen_saturation": "spo2",
    "respiratory_rate": "respiratory",
    "weight_body_mass": "weight_kg",
    "body_fat_percentage": "body_fat",
    "mindful_minutes": "mindful_min",
    "dietary_water": "water_ml",
}


def check_auth(authorization):
    expected = os.environ.get("HEALTH_TOKEN", "")
    if not expected or not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization[7:].strip().encode(), expected.encode())


def _to_float(v):
    """捷徑送來的數字常是字串，可能帶千分位、單位（"8,123 步"、"98 %"）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
        return float(m.group()) if m else None
    return None


def _clean(key, v):
    """單一數值正規化；不合理就丟掉（回 None）。"""
    f = _to_float(v)
    if f is None or f < 0:
        return None
    kind = _FIELDS[key][1]
    if kind == "pct":
        if f > 1.5:            # 98 → 0.98
            f /= 100
        return round(f, 3) if f <= 1 else None
    if kind == "int":
        return int(round(f))
    return round(f, 2)


def _parse_day(s):
    if isinstance(s, str):
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s.strip().replace("/", "-"))
        if m:
            try:
                return date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                return None
    return None


def parse_flat(payload, today=None):
    """捷徑格式 → {date: {key: value}}。沒帶 date 就當今天。"""
    day = _parse_day(payload.get("date")) or today
    if day is None:
        return {}, []
    vals = {}
    for k, v in payload.items():
        key = k if k in _FIELDS else _BY_ZH.get(k)
        if key:
            c = _clean(key, v)
            if c is not None:
                vals[key] = c
    workouts = payload.get("workouts")
    w = {day: [str(x)[:200] for x in workouts]} if isinstance(workouts, list) and workouts else {}
    if isinstance(workouts, str) and workouts.strip():
        w = {day: [workouts.strip()[:500]]}
    return ({day: vals} if vals else {}), w


def parse_hae(payload):
    """Health Auto Export 格式 → ({date: {key: value}}, {date: [體能訓練文字]})。"""
    data = payload.get("data") or {}
    out = {}
    for m in data.get("metrics") or []:
        name = (m or {}).get("name")
        units = str((m or {}).get("units") or "").lower()
        for row in (m or {}).get("data") or []:
            day = _parse_day((row or {}).get("date"))
            if day is None:
                continue
            vals = out.setdefault(day, {})
            if name == "sleep_analysis":
                total = row.get("totalSleep", row.get("asleep"))
                for key, raw in (("sleep_hours", total), ("deep_hours", row.get("deep")),
                                 ("rem_hours", row.get("rem"))):
                    c = _clean(key, raw) if raw is not None else None
                    if c is not None:
                        vals[key] = c
                continue
            key = _HAE_METRICS.get(name)
            if not key:
                continue
            raw = row.get("Avg") if name == "heart_rate" else row.get("qty")
            f = _to_float(raw)
            if f is None:
                continue
            if key == "active_kcal" and units == "kj":
                f /= 4.184
            if key == "distance_km" and units == "m":
                f /= 1000
            if key == "distance_km" and units == "mi":
                f *= 1.609344
            if key == "water_ml" and units in ("l", "liter"):
                f *= 1000
            c = _clean(key, f)
            if c is not None:
                vals[key] = c
    workouts = {}
    for w in data.get("workouts") or []:
        day = _parse_day((w or {}).get("start"))
        if day is None:
            continue
        dur = _to_float(w.get("duration"))
        mins = f"{round(dur / 60)} 分" if dur else ""
        kcal = _to_float(((w.get("activeEnergyBurned") or {}) if isinstance(w.get("activeEnergyBurned"), dict)
                          else {"qty": w.get("activeEnergyBurned")}).get("qty"))
        bits = [str(w.get("name") or "運動"), mins, f"{round(kcal)} kcal" if kcal else ""]
        workouts.setdefault(day, []).append(" ".join(b for b in bits if b))
    return {d: v for d, v in out.items() if v}, workouts


def build_props(day, vals, workouts, source):
    props = {
        "日期標題": {"title": [{"text": {"content": f"{day.isoformat()}（{_WEEKDAYS[day.weekday()]}）"}}]},
        "日期": {"date": {"start": day.isoformat()}},
        "來源": {"select": {"name": source}},
    }
    for key, v in vals.items():
        props[_FIELDS[key][0]] = {"number": v}
    if workouts:
        props["體能訓練"] = {"rich_text": [{"text": {"content": "、".join(workouts)[:1900]}}]}
    return props


def _find_row(client, day):
    res = client.databases.query(
        database_id=HEALTH_DB_ID,
        filter={"property": "日期", "date": {"equals": day.isoformat()}}, page_size=5)
    rows = res.get("results", [])
    return rows[0] if rows else None


def _daily_id(client, day):
    """那天的 📅 每日紀錄 id；沒有就建一列（跟 OwnTracks 同一把鎖，不會撞出兩列）。"""
    import owntracks
    if not owntracks._RUN_LOCK.acquire(timeout=60):
        return None
    try:
        row = owntracks._find_daily(client, day)
        if row is None:
            row = client.pages.create(parent={"database_id": owntracks.DAILY_DB_ID}, properties={
                "日期標題": {"title": [{"text": {"content": f"{day.isoformat()}（{_WEEKDAYS[day.weekday()]}）"}}]},
                "日期": {"date": {"start": day.isoformat()}},
                "星期": {"select": {"name": _WEEKDAYS[day.weekday()]}},
            })
        return row["id"]
    except Exception as e:
        print(f"[health] 每日紀錄連結失敗 {day}：{e}")
        return None
    finally:
        owntracks._RUN_LOCK.release()


def save_day(client, day, vals, workouts, source):
    props = build_props(day, vals, workouts, source)
    row = _find_row(client, day)
    if row is None:
        daily = _daily_id(client, day)
        if daily:
            props["每日紀錄"] = {"relation": [{"id": daily}]}
        client.pages.create(parent={"database_id": HEALTH_DB_ID}, properties=props)
        return "created"
    if not (row["properties"].get("每日紀錄", {}) or {}).get("relation"):
        daily = _daily_id(client, day)
        if daily:
            props["每日紀錄"] = {"relation": [{"id": daily}]}
    client.pages.update(page_id=row["id"], properties=props)
    return "updated"


def handle_post(authorization, payload, today=None):
    """回 (HTTP 狀態碼, body)。寫 Notion 失敗回 500，讓手機那邊知道要重送。"""
    if not check_auth(authorization):
        return 401, {"detail": "unauthorized"}
    if not isinstance(payload, dict):
        return 400, {"detail": "expect JSON object"}
    if today is None:
        from tz_utils import now_tpe
        today = now_tpe().date()
    if isinstance(payload.get("data"), dict):
        days, workouts, source = *parse_hae(payload), "Health Auto Export"
    else:
        (days, workouts), source = parse_flat(payload, today), "捷徑"
    all_days = sorted(set(days) | set(workouts))
    if not all_days:
        return 200, {"ok": True, "days": [], "note": "沒有認得的欄位"}
    client = notion_db._get_client()
    if not client:
        return 503, {"detail": "Notion 不可用"}
    result = {}
    try:
        for d in all_days:
            result[d.isoformat()] = save_day(client, d, days.get(d, {}), workouts.get(d, []), source)
    except Exception as e:
        print(f"[health] 寫入失敗：{e}")
        return 500, {"detail": "store failed", "done": result}
    print(f"[health] {source}：{result}")
    return 200, {"ok": True, "days": result,
                 "fields": sorted({_FIELDS[k][0] for v in days.values() for k in v})}
