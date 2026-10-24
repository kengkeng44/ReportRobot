"""OwnTracks 定位 → Notion 生活紀錄。

兩段：
1. 接收：iPhone 上的 OwnTracks（HTTP 模式）把定位 POST 到 /owntracks，
   每一點寫一列進「定位」區塊的「定位點」DB。原始點只是中間產物，30 天後封存。
2. 整理：每天 00:20 TPE 把昨天的點整理進「📔 生活紀錄」：
   - 📅 每日紀錄：外出／在家／在公司時數、移動公里、行程、去過的地點
   - 📍 地點：比對既有地點的座標；沒對到、但停留 ≥ 20 分鐘的地方新增「地點 #NNN」，
     之後改成看得懂的名字，下次再去就會自動對上

時數怎麼算：每一點代表「從這點到下一點之間都在這裡」。iPhone 預設的
significant-change 模式靜止時不送點，所以在家睡一整晚只會有睡前一點、
起床出門一點 —— 這樣算剛好是對的。第一點之前（往前 12 小時都沒有點）
的時間不算，三個時數加起來可能不到 24。

手動改過的每日紀錄不覆蓋：行程欄不是 📡 開頭（Google 時間軸匯入、或你自己
更正過的）就只補「去過的地點」，數字跟行程都不動。

設定（Infisical）：
- OWNTRACKS_PASSWORD：OwnTracks App 裡 Authentication 填的密碼（帳號隨意）
- LIFE_DAILY_DB_ID / LIFE_PLACES_DB_ID：可不設，預設就是現在那兩張表
- 「📔 生活紀錄」那頁要在 Notion 連到 integration，否則讀不到每日紀錄／地點
"""

import base64
import hmac
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import notion_db
from tz_utils import now_tpe

_TPE = timezone(timedelta(hours=8))

DAILY_DB_ID = os.environ.get("LIFE_DAILY_DB_ID", "d874622ec5bc4a92bbde839f8bc3f7c2")
PLACES_DB_ID = os.environ.get("LIFE_PLACES_DB_ID", "fa6d0bdf350b4be6bc74c0c920b445ad")

MARK = "📡 "               # 行程欄開頭有這個 = 程式寫的，可以覆蓋
PARTIAL = "📡⏳ "           # 當天還沒過完（私訊「整理定位」）寫的：地點統計還沒算
PLACE_RADIUS_M = 150       # 離既有地點多近算「在那裡」
STOP_MIN_MINUTES = 60      # 沒對到地點、停多久才新增地點（使用者 2026-10-24：只留待超過一小時的）
STOP_SINGLE_MIN_MINUTES = 60   # 只有 1 個點的段落要停更久才算：省電模式下路上
                               # 也常常一點之後隔 30 分鐘才有下一點，那只是在移動
VISIT_MIN_MINUTES = 60     # 待多久才算「去過」（同上，路過的店不算）
MAX_ACCURACY_M = 500       # 比這更不準的點直接丟掉
KM_MAX_ACCURACY_M = 200    # 算距離只用夠準的點，否則原地飄移會累積成好幾公里
KM_MAX_SPEED_KMH = 300     # 兩點之間換算速度超過這個 = 定位跳點，不算距離
KEEP_DAYS = 30             # 原始點保留天數
_PACE = 0.35               # Notion 限流 3 req/s

_WEEKDAYS = "一二三四五六日"
_RUN_LOCK = threading.Lock()   # 排程跟私訊同時整理會重複建地點


# ─────────────────────────────────────────────────────────
# 1. 接收
# ─────────────────────────────────────────────────────────

def check_auth(authorization):
    """驗 HTTP Basic 的密碼；帳號不看。沒設密碼 = 整支端點關閉。"""
    expected = os.environ.get("OWNTRACKS_PASSWORD", "")
    if not expected:
        return False
    if not authorization or not authorization.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization[6:]).decode("utf-8")
    except Exception:
        return False
    _, _, password = decoded.partition(":")
    return hmac.compare_digest(password.encode(), expected.encode())


def parse_location(payload):
    """OwnTracks 的 JSON → 點 dict；不是定位訊息或欄位壞掉就回 None。

    OwnTracks 還會送 transition / waypoint / lwt / status 等訊息，那些不用存。
    """
    if not isinstance(payload, dict) or payload.get("_type") != "location":
        return None
    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
        tst = int(payload["tst"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    acc = payload.get("acc")
    vel = payload.get("vel")
    return {
        "lat": lat, "lon": lon, "tst": tst,
        "acc": float(acc) if isinstance(acc, (int, float)) else None,
        "vel": float(vel) if isinstance(vel, (int, float)) and vel >= 0 else None,
        "tid": str(payload.get("tid") or "")[:8],
    }


def save_point(point):
    """寫一列進定位點 DB。失敗就 raise —— 端點回 500，手機會留著重送。"""
    client = notion_db._get_client()
    db_id = notion_db.get_or_create_db("定位點")
    if not client or not db_id:
        raise RuntimeError("Notion 定位點 DB 不可用")
    when = datetime.fromtimestamp(point["tst"], _TPE)
    props = {
        "點": {"title": [{"text": {"content": f"{point['tid'] or 'ot'}-{point['tst']}"}}]},
        "時間": {"date": {"start": when.isoformat()}},
        "緯度": {"number": point["lat"]},
        "經度": {"number": point["lon"]},
        "裝置": {"rich_text": [{"text": {"content": point["tid"]}}]},
    }
    if point["acc"] is not None:
        props["精確度"] = {"number": point["acc"]}
    if point["vel"] is not None:
        props["速度"] = {"number": point["vel"]}
    client.pages.create(parent={"database_id": db_id}, properties=props)


def handle_post(authorization, payload):
    """端點本體（server.py 只是轉接，因為 server.py 本機 import 不了）。

    回 (HTTP 狀態碼, body)。OwnTracks 要求成功時回 JSON 陣列。
    """
    if not check_auth(authorization):
        return 401, {"detail": "unauthorized"}
    point = parse_location(payload)
    if point is None:
        return 200, []
    try:
        save_point(point)
    except Exception as e:
        print(f"[owntracks] 寫入失敗，回 500 讓手機重送：{e}")
        return 500, {"detail": "store failed"}
    return 200, []


# ─────────────────────────────────────────────────────────
# 2. 整理（純函式，好測）
# ─────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _nearest_place(pt, places):
    best, best_d = None, None
    for pl in places:
        d = haversine_m(pt["lat"], pt["lon"], pl["lat"], pl["lon"])
        if d <= PLACE_RADIUS_M and (best_d is None or d < best_d):
            best, best_d = pl, d
    return best


def build_segments(points, places, day_start, day_end):
    """把點切成連續的段落，回 list of dict：
    {place（dict 或 None）, start, end（unix 秒，已裁到當天）, pts（這段的點）}

    points：當天的點 + 前一天最後一點（決定半夜在哪），已依 tst 排序、去重。
    """
    pts = [p for p in points
           if p["acc"] is None or p["acc"] <= MAX_ACCURACY_M]
    if not pts:
        return []

    raw = []   # 先不裁切，每段 start = 第一點時間
    for p in pts:
        pl = _nearest_place(p, places)
        last = raw[-1] if raw else None
        if last is not None:
            if pl is not None and last["place"] is pl:
                last["pts"].append(p)
                continue
            if pl is None and last["place"] is None:
                anchor = last["pts"][0]
                if haversine_m(p["lat"], p["lon"], anchor["lat"], anchor["lon"]) <= PLACE_RADIUS_M:
                    last["pts"].append(p)
                    continue
        raw.append({"place": pl, "start": p["tst"], "pts": [p]})

    segs = []
    for i, s in enumerate(raw):
        end = raw[i + 1]["start"] if i + 1 < len(raw) else day_end
        # 第一點之前沒有資料就不算（剛開始用、或手機關機），不要猜人在哪。
        # 半夜在哪由前一天最後一點決定 —— 它會被裁到 00:00 開始。
        start, end = max(s["start"], day_start), min(end, day_end)
        if end > start:
            segs.append({"place": s["place"], "start": start, "end": end, "pts": s["pts"]})
    return segs


def moved_km(points, day_start, day_end):
    good = [p for p in points
            if day_start <= p["tst"] < day_end
            and (p["acc"] is None or p["acc"] <= KM_MAX_ACCURACY_M)]
    total = 0.0
    for a, b in zip(good, good[1:]):
        d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        dt = max(b["tst"] - a["tst"], 1)
        if d / dt * 3.6 > KM_MAX_SPEED_KMH:
            continue
        total += d
    return round(total / 1000, 1)


def summarize(points, places, day_start, day_end):
    """回當天摘要 dict。新地點（沒對到、停夠久）放在 new_stops，place 為 None。"""
    segs = build_segments(points, places, day_start, day_end)
    home = work = 0
    stays = {}           # place id → 秒
    new_stops = []       # 沒對到既有地點的久停：{lat, lon, start, end}
    for s in segs:
        dur = s["end"] - s["start"]
        pl = s["place"]
        if pl is not None and pl.get("bad"):
            continue                     # 不準的地點：當成路上，不算停留也不新增
        if pl is not None:
            if pl.get("category") == "家":
                home += dur
            elif pl.get("category") == "公司/學校":
                work += dur
            stays[pl["id"]] = stays.get(pl["id"], 0) + dur
        elif dur >= (STOP_MIN_MINUTES if len(s["pts"]) >= 2 else STOP_SINGLE_MIN_MINUTES) * 60:
            lat = sum(p["lat"] for p in s["pts"]) / len(s["pts"])
            lon = sum(p["lon"] for p in s["pts"]) / len(s["pts"])
            s["stop"] = {"lat": lat, "lon": lon}
            new_stops.append(s)
    covered = sum(s["end"] - s["start"] for s in segs)
    return {
        "segments": segs,
        "home_h": round(home / 3600, 1),
        "work_h": round(work / 3600, 1),
        "out_h": round((covered - home) / 3600, 1),
        "km": moved_km(points, day_start, day_end),
        "stays": stays,
        "new_stops": new_stops,
    }


def itinerary(segments, names, day_end):
    """「08:10–09:02 家；09:30–18:40 公司」格式（跟 Google 時間軸匯入的一樣）。

    names：segment index → 顯示名稱；沒名字的（路上）略過。相鄰同名合併。
    """
    items = []
    for i, s in enumerate(segments):
        name = names.get(i)
        if not name or s["end"] - s["start"] < VISIT_MIN_MINUTES * 60:
            continue
        if items and items[-1][0] == name and s["start"] - items[-1][2] < 30 * 60:
            items[-1][2] = s["end"]
            continue
        items.append([name, s["start"], s["end"]])

    def hm(ts):
        if ts >= day_end:
            return "00:00"
        return datetime.fromtimestamp(ts, _TPE).strftime("%H:%M")

    return "；".join(f"{hm(a)}–{hm(b)} {n}" for n, a, b in items)


# ─────────────────────────────────────────────────────────
# 3. Notion 讀寫
# ─────────────────────────────────────────────────────────

def _title(props, name):
    return "".join(t.get("plain_text", "") for t in (props.get(name, {}) or {}).get("title", []))


def _text(props, name):
    return "".join(t.get("plain_text", "") for t in (props.get(name, {}) or {}).get("rich_text", []))


def _num(props, name):
    return (props.get(name, {}) or {}).get("number")


def load_points(client, start_ts, end_ts):
    db_id = notion_db.get_or_create_db("定位點")
    if not db_id:
        return []
    rows = notion_db._query_all(
        db_id, client, 5000,
        filter={"and": [
            {"property": "時間", "date": {"on_or_after": datetime.fromtimestamp(start_ts, _TPE).isoformat()}},
            {"property": "時間", "date": {"before": datetime.fromtimestamp(end_ts, _TPE).isoformat()}},
        ]},
        sorts=[{"property": "時間", "direction": "ascending"}],
    )
    seen, out = set(), []
    for r in rows:
        p = r["properties"]
        when = ((p.get("時間", {}) or {}).get("date") or {}).get("start")
        lat, lon = _num(p, "緯度"), _num(p, "經度")
        if not when or lat is None or lon is None:
            continue
        # Notion 日期欄會把秒數截掉（實測 06:08:41 存成 06:08:00），
        # 同一分鐘的點會撞在一起 —— 精確時間戳以標題「裝置-tst」為準
        m = re.search(r"-(\d{9,})$", _title(p, "點"))
        tst = int(m.group(1)) if m else int(datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp())
        if tst in seen:            # 手機重送會有同一點兩列
            continue
        seen.add(tst)
        out.append({"lat": lat, "lon": lon, "tst": tst,
                    "acc": _num(p, "精確度"), "vel": _num(p, "速度"), "tid": _text(p, "裝置")})
    out.sort(key=lambda x: x["tst"])
    return out


def load_places(client):
    rows = notion_db._query_all(PLACES_DB_ID, client, 2000)
    places, max_no = [], 0
    for r in rows:
        p = r["properties"]
        name = _title(p, "名稱")
        m = re.fullmatch(r"地點 #(\d+)", name.strip())
        if m:
            max_no = max(max_no, int(m.group(1)))
        lat, lon = _num(p, "緯度"), _num(p, "經度")
        if lat is None or lon is None:
            continue
        places.append({
            "id": r["id"], "name": name or "（沒有名稱）",
            "lat": lat, "lon": lon,
            "category": notion_db._read_select(p, "類別"),
            "visits": _num(p, "造訪次數") or 0,
            "hours": _num(p, "總停留時數") or 0,
            "first": ((p.get("首次造訪", {}) or {}).get("date") or {}).get("start"),
            "auto": _text(p, "備註").startswith("📡"),
            "note": _text(p, "備註"),
            # 勾了「不準」的地點仍拿來比對 —— 否則下次經過會被當成新地方再建一筆
            "bad": bool((p.get("不準", {}) or {}).get("checkbox")),
        })
    return places, max_no


# ── 反查地名（OpenStreetMap Nominatim）──────────────────
# 2026-10-24 使用者選的：要精確到店名／門牌，接受新地點座標送到 OSM。
# 只送「新地點」，不送原始軌跡；每天幾筆。Nominatim 規定 1 秒最多 1 次、要帶 User-Agent。
_NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
_UA = "ReportRobot-life-record/1.0 (+https://github.com/kengkeng44/ReportRobot)"
_GEOCODE_PACE = 1.1
_POI_CLASSES = {"amenity", "shop", "tourism", "leisure", "office", "craft",
                "healthcare", "railway", "aeroway", "historic", "building"}
_CATEGORY_MAP = {
    ("amenity", "restaurant"): "餐廳", ("amenity", "fast_food"): "餐廳",
    ("amenity", "food_court"): "餐廳", ("amenity", "bar"): "餐廳", ("amenity", "pub"): "餐廳",
    ("amenity", "cafe"): "咖啡廳", ("shop", "coffee"): "咖啡廳", ("shop", "tea"): "咖啡廳",
    ("leisure", "fitness_centre"): "運動", ("leisure", "sports_centre"): "運動",
    ("leisure", "stadium"): "運動", ("leisure", "swimming_pool"): "運動",
    ("amenity", "university"): "公司/學校", ("amenity", "school"): "公司/學校",
    ("amenity", "college"): "公司/學校",
}


def _area(addr):
    city = addr.get("city") or addr.get("county") or ""
    district = (addr.get("city_district") or addr.get("district")
                or addr.get("suburb") or addr.get("town") or "")
    return city + (district if district not in city else "")


def name_from_nominatim(d):
    """Nominatim 回傳 → (名稱, 類別)；什麼都抓不到回 (None, None)。"""
    if not isinstance(d, dict) or d.get("error"):
        return None, None
    addr = d.get("address") or {}
    cls, typ = d.get("category") or "", d.get("type") or ""
    area = _area(addr)
    poi = (d.get("name") or "").strip() if cls in _POI_CLASSES else ""
    if poi:
        name = f"{poi}（{area}）" if area else poi
    elif addr.get("road"):
        name = f"{area}{addr['road']}{addr.get('house_number', '')}"
        if addr.get("house_number") and not addr["house_number"].endswith("號"):
            name += "號"
    else:
        name = area or None
    if not name:
        return None, None
    if (cls, typ) in _CATEGORY_MAP:
        cat = _CATEGORY_MAP[(cls, typ)]
    elif cls == "shop":
        cat = "購物"
    elif cls in ("tourism", "leisure", "historic"):
        cat = "景點/玩樂"
    else:
        cat = "其他"
    return name[:80], cat


# ── 地點命名：地標附近 / 行政區（2026-10-25 使用者定的）──────────────
# 店家級名稱太不準（樓下的房仲、隔壁的蛋糕店），改成大一點的範圍：
#   800 公尺內（約走路 10 分鐘）有知名地標 → 「逢甲大學附近（臺中市西屯區）」
#   沒有 → 只寫行政區「臺中市東區」
# 地標用 Google Places 依熱門度排第一的；區名用 OSM（Google 地址字串切區名不穩）。
LANDMARK_RADIUS_M = 800
_PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"
LANDMARK_TYPES = [
    "train_station", "subway_station", "light_rail_station", "airport",
    "university", "shopping_mall", "department_store", "tourist_attraction",
    "park", "stadium", "museum", "zoo", "amusement_park", "night_club",
    "hospital", "city_hall", "historical_landmark", "market",
]
_LEISURE_TYPES = {"park", "tourist_attraction", "museum", "zoo", "amusement_park",
                  "stadium", "historical_landmark", "market"}


def _google_landmark(lat, lon, key):
    """800 公尺內最熱門的地標 → (名稱, 類型)；沒有回 (None, None)。"""
    import requests
    r = requests.post(_PLACES_URL, json={
        "includedTypes": LANDMARK_TYPES,
        "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lon},
                                           "radius": LANDMARK_RADIUS_M}},
        "maxResultCount": 1,
        "rankPreference": "POPULARITY",
        "languageCode": "zh-TW",
    }, headers={
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "places.displayName,places.primaryType",
    }, timeout=8)
    r.raise_for_status()
    places = r.json().get("places") or []
    if not places:
        return None, None
    name = ((places[0].get("displayName") or {}).get("text") or "").strip()
    return (name or None), places[0].get("primaryType")


def _osm_area(lat, lon):
    """行政區（臺中市東區）；失敗回空字串。"""
    try:
        import requests
        r = requests.get(_NOMINATIM, params={
            "format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
            "zoom": 14, "addressdetails": 1, "accept-language": "zh-TW",
        }, headers={"User-Agent": _UA}, timeout=8)
        r.raise_for_status()
        return _area((r.json() or {}).get("address") or {})
    except Exception as e:
        print(f"[owntracks] 查行政區失敗：{e}")
        return ""


def area_name(landmark, area):
    if landmark and area:
        return f"{landmark}附近（{area}）"
    return f"{landmark}附近" if landmark else (area or None)


def reverse_geocode(lat, lon):
    """回 (名稱, 類別)。地標附近 > 行政區；都查不到回 (None, None)，下次整理再試。"""
    area = _osm_area(lat, lon)
    landmark, kind = None, None
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if key:
        try:
            landmark, kind = _google_landmark(lat, lon, key)
        except Exception as e:
            # 金鑰放在 header 不在網址，錯誤訊息不會帶出來
            print(f"[owntracks] Google 地標查詢失敗，只用行政區：{e}")
    name = area_name(landmark, area)
    if not name:
        return None, None
    return name, ("景點/玩樂" if kind in _LEISURE_TYPES else "其他")


def _osm_reverse(lat, lon):
    try:
        import requests
        r = requests.get(_NOMINATIM, params={
            "format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
            "zoom": 18, "addressdetails": 1, "accept-language": "zh-TW",
        }, headers={"User-Agent": _UA}, timeout=8)
        r.raise_for_status()
        return name_from_nominatim(r.json())
    except Exception as e:
        print(f"[owntracks] 反查地名失敗：{e}")
        return None, None


_NEW_NOTE = "區域命名"


def _needs_rename(pl):
    if pl.get("bad") or not pl.get("auto"):
        return False
    if re.fullmatch(r"地點 #\d+", pl["name"]):
        return True
    # 2026-10-25 前用店家名命名的（備註寫「地圖反查」）一律換成地標／行政區
    note = pl.get("note") or ""
    return ("地圖反查" in note or "改個名字" in note) and _NEW_NOTE not in note


def rename_unnamed(client, places, limit=10):
    """還叫「地點 #NNN」、或還是舊的店家級名稱的自動地點，重新命名。你自己改過名字的不會動。"""
    n = 0
    for pl in places:
        if n >= limit:
            break
        if not _needs_rename(pl):
            continue
        n += 1
        time.sleep(_GEOCODE_PACE)
        name, cat = reverse_geocode(pl["lat"], pl["lon"])
        if not name:
            continue
        props = {"名稱": {"title": [{"text": {"content": name}}]},
                 "備註": {"rich_text": [{"text": {"content": f"📡 OwnTracks 自動新增（{_NEW_NOTE}）"}}]}}
        if pl.get("category") in ("", "其他", "餐廳", "購物", "咖啡廳") and cat:
            props["類別"] = {"select": {"name": cat}}
            pl["category"] = cat
        client.pages.update(page_id=pl["id"], properties=props)
        pl["name"] = name


def _create_place(client, number, lat, lon, day, name=None, cat=None):
    name, cat = name or f"地點 #{number:03d}", cat or "其他"
    page = client.pages.create(
        parent={"database_id": PLACES_DB_ID},
        properties={
            "名稱": {"title": [{"text": {"content": name}}]},
            "類別": {"select": {"name": cat}},
            "緯度": {"number": round(lat, 6)},
            "經度": {"number": round(lon, 6)},
            "Google Maps": {"url": f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"},
            "備註": {"rich_text": [{"text": {"content": f"📡 OwnTracks {day} 自動新增（{_NEW_NOTE}：{LANDMARK_RADIUS_M} 公尺內有地標寫「地標附近」，不然寫行政區）"}}]},
        },
    )
    return {"id": page["id"], "name": name, "lat": lat, "lon": lon, "auto": True,
            "category": cat, "visits": 0, "hours": 0, "first": None}


def _find_daily(client, day):
    res = client.databases.query(
        database_id=DAILY_DB_ID,
        filter={"property": "日期", "date": {"equals": day.isoformat()}},
        page_size=5,
    )
    rows = res.get("results", [])
    return rows[0] if rows else None


def run_for_day(day=None):
    """整理某一天（預設昨天，台北時間）。回摘要 dict 方便 admin 端點看。"""
    if not _RUN_LOCK.acquire(timeout=120):
        return {"ok": False, "reason": "另一個整理還在跑，等一下再試"}
    try:
        return _run_for_day(day)
    finally:
        _RUN_LOCK.release()


def _run_for_day(day=None):
    client = notion_db._get_client()
    if not client:
        return {"ok": False, "reason": "Notion 未設定"}
    day = day or (now_tpe().date() - timedelta(days=1))
    day_start = int(datetime(day.year, day.month, day.day, tzinfo=_TPE).timestamp())
    day_end = day_start + 86400
    # 今天還沒過完就整理到現在，不要把未來算進去
    day_end = min(day_end, int(time.time()))
    # 當天還沒過完：時數照寫，但地點的造訪次數／停留時數等那天過完的排程再算，
    # 不然下午算一次「待了 1 小時」，半夜那次看到已經連過就不再更新
    partial = day_end < day_start + 86400

    # 往前多抓 12 小時，只為了知道半夜 00:00 人在哪
    points = load_points(client, day_start - 12 * 3600, day_end)
    in_day = [p for p in points if p["tst"] >= day_start]
    if not in_day:
        return {"ok": True, "day": day.isoformat(), "points": 0, "skipped": "當天沒有定位點"}
    before = [p for p in points if p["tst"] < day_start][-1:]
    points = before + in_day

    places, max_no = load_places(client)
    try:
        rename_unnamed(client, places)
    except Exception as e:
        print(f"[owntracks] 補地名失敗（非致命）：{e}")
    summary = summarize(points, places, day_start, day_end)

    # 久停但沒對到地點 → 新增地點。同一天兩段停在同一個新地方只建一次。
    created = []
    for s in summary["new_stops"]:
        stop = s["stop"]
        dup = next((c for c in created
                    if haversine_m(stop["lat"], stop["lon"], c["lat"], c["lon"]) <= PLACE_RADIUS_M), None)
        if dup is None:
            name, cat = reverse_geocode(stop["lat"], stop["lon"])
            # 同一個地標附近／同一區已經有自動地點就併進去，不要一直長新的
            dup = next((pl for pl in places + created
                        if name and pl["name"] == name and not pl.get("bad")), None)
        if dup is None:
            max_no += 1
            dup = _create_place(client, max_no, stop["lat"], stop["lon"], day.isoformat(), name, cat)
            created.append(dup)
            time.sleep(_PACE)
        s["place"] = dup
        summary["stays"][dup["id"]] = summary["stays"].get(dup["id"], 0) + (s["end"] - s["start"])

    names = {i: s["place"]["name"] for i, s in enumerate(summary["segments"])
             if s["place"] and not s["place"].get("bad")}
    trip = itinerary(summary["segments"], names, day_start + 86400)
    visited = [pid for pid, sec in summary["stays"].items() if sec >= VISIT_MIN_MINUTES * 60]

    row = _find_daily(client, day)
    existing_rel = []
    row_was_partial = False
    if row is None:
        props = {
            "日期標題": {"title": [{"text": {"content": f"{day.isoformat()}（{_WEEKDAYS[day.weekday()]}）"}}]},
            "日期": {"date": {"start": day.isoformat()}},
            "星期": {"select": {"name": _WEEKDAYS[day.weekday()]}},
        }
        row = client.pages.create(parent={"database_id": DAILY_DB_ID}, properties=props)
        overwrite = True
    else:
        rp = row["properties"]
        existing_rel = [x["id"] for x in (rp.get("去過的地點", {}) or {}).get("relation", [])]
        old_trip = _text(rp, "行程")
        overwrite = (not old_trip) or old_trip.startswith("📡")
        row_was_partial = old_trip.startswith(PARTIAL)

    norm = lambda x: x.replace("-", "")
    have = {norm(x) for x in existing_rel}
    new_links = [pid for pid in visited if norm(pid) not in have]
    # 地點統計算哪些：「當天未完」那次連上的都還沒算過
    if partial:
        to_count = []
    elif row_was_partial:
        to_count = visited
    else:
        to_count = new_links
    update = {"去過的地點": {"relation": [{"id": x} for x in existing_rel + new_links]}}
    if overwrite:
        update.update({
            "外出時數": {"number": summary["out_h"]},
            "在家時數": {"number": summary["home_h"]},
            "在公司時數": {"number": summary["work_h"]},
            "移動公里": {"number": summary["km"]},
            "行程": {"rich_text": [{"text": {"content": ((PARTIAL if partial else MARK) + trip)[:2000]}}]},
        })
    client.pages.update(page_id=row["id"], properties=update)

    # 地點統計只在「這天第一次完整整理」時累加，重跑同一天不會重複算
    by_id = {pl["id"]: pl for pl in places + created}
    for pid in to_count:
        pl = by_id.get(pid)
        if not pl:
            continue
        time.sleep(_PACE)
        props = {
            "造訪次數": {"number": pl["visits"] + 1},
            "總停留時數": {"number": round(pl["hours"] + summary["stays"][pid] / 3600, 1)},
            "最近造訪": {"date": {"start": day.isoformat()}},
        }
        if not pl["first"]:
            props["首次造訪"] = {"date": {"start": day.isoformat()}}
        client.pages.update(page_id=pid, properties=props)

    result = {
        "ok": True, "day": day.isoformat(), "points": len(in_day),
        "out_h": summary["out_h"], "home_h": summary["home_h"], "work_h": summary["work_h"],
        "km": summary["km"], "visited": len(visited), "new_places": len(created),
        "places": [by_id[pid]["name"] for pid in visited if pid in by_id],
        "overwrote_numbers": overwrite,
    }
    print(f"[owntracks] 每日整理：{result}")
    return result


REFRESH_KEYWORDS = {"整理定位", "定位整理", "整理今天定位"}


def refresh_today_reply():
    """私訊「整理定位」：馬上整理今天到現在為止，回一句摘要。"""
    try:
        r = run_for_day(now_tpe().date())
    except Exception as e:
        print(f"[owntracks] 手動整理失敗：{e}")
        return f"📡 整理失敗：{str(e)[:150]}\n（常見原因：「📔 生活紀錄」那頁沒連到 Notion integration）"
    if not r.get("ok"):
        return f"📡 整理失敗：{r.get('reason')}"
    if r.get("skipped"):
        return f"📡 今天還沒有定位點（{r['skipped']}）"
    msg = (f"📡 今天到現在整理好了（{r['points']} 個點）\n"
           f"外出 {r['out_h']} 小時・在家 {r['home_h']}・在公司 {r['work_h']}\n"
           f"移動 {r['km']} 公里・去過 {r['visited']} 個地點")
    if r["new_places"]:
        msg += f"（新增 {r['new_places']} 個）"
    if r.get("places"):
        msg += "\n📍 " + "、".join(r["places"])
    if not r["overwrote_numbers"]:
        msg += "\n今天的行程是手動填的，只補了地點、沒動時數"
    return msg


def purge_old(keep_days=KEEP_DAYS):
    """封存超過 keep_days 的原始點。座標留太久沒用處，只是多一份隱私風險。"""
    client = notion_db._get_client()
    db_id = notion_db.get_or_create_db("定位點")
    if not client or not db_id:
        return 0
    cutoff = (now_tpe() - timedelta(days=keep_days)).isoformat()
    rows = notion_db._query_all(db_id, client, 2000,
                                filter={"property": "時間", "date": {"before": cutoff}})
    for r in rows:
        client.pages.update(page_id=r["id"], archived=True)
        time.sleep(_PACE)
    if rows:
        print(f"[owntracks] 封存 {len(rows)} 個舊定位點")
    return len(rows)


def nightly():
    """排程入口：整理昨天 + 清舊點。各自吞例外，一段壞了不拖另一段。"""
    try:
        # 前天也重跑：昨晚若剛好在部署、排程沒跑到，今天補上（重跑安全）
        today = now_tpe().date()
        for back in (2, 1):
            run_for_day(today - timedelta(days=back))
    except Exception as e:
        print(f"[owntracks] 每日整理失敗：{e}")
        try:
            from admin_notify import notify_admin
            notify_admin(e, context={"module": "owntracks", "function": "nightly"})
        except Exception:
            pass
    try:
        purge_old()
    except Exception as e:
        print(f"[owntracks] 清舊點失敗（非致命）：{e}")
