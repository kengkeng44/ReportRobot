"""OwnTracks 接收 + 每日整理。整理邏輯是純函式，直接餵點測。"""

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

import owntracks

TPE = timezone(timedelta(hours=8))
DAY0 = int(datetime(2026, 10, 25, tzinfo=TPE).timestamp())
DAY1 = DAY0 + 86400

HOME = {"id": "home", "name": "家", "lat": 25.0330, "lon": 121.5654, "category": "家"}
WORK = {"id": "work", "name": "公司", "lat": 25.0478, "lon": 121.5170, "category": "公司/學校"}


def at(h, m=0):
    return DAY0 + h * 3600 + m * 60


def pt(t, place=None, lat=None, lon=None, acc=10):
    lat = lat if lat is not None else place["lat"]
    lon = lon if lon is not None else place["lon"]
    return {"lat": lat, "lon": lon, "tst": t, "acc": acc, "vel": None, "tid": "ip"}


def basic(pw, user="me"):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


# ── 接收 ─────────────────────────────────────────────

def test_auth_closed_when_no_password(monkeypatch):
    monkeypatch.delenv("OWNTRACKS_PASSWORD", raising=False)
    assert owntracks.check_auth(basic("")) is False


def test_auth_checks_password_not_user(monkeypatch):
    monkeypatch.setenv("OWNTRACKS_PASSWORD", "s3cret")
    assert owntracks.check_auth(basic("s3cret", user="whatever"))
    assert not owntracks.check_auth(basic("wrong"))
    assert not owntracks.check_auth(None)
    assert not owntracks.check_auth("Bearer s3cret")


def test_non_location_messages_are_acked_not_stored(monkeypatch):
    monkeypatch.setenv("OWNTRACKS_PASSWORD", "pw")
    monkeypatch.setattr(owntracks, "save_point", lambda p: (_ for _ in ()).throw(AssertionError))
    assert owntracks.handle_post(basic("pw"), {"_type": "transition"}) == (200, [])


def test_location_saved_and_acked_with_array(monkeypatch):
    monkeypatch.setenv("OWNTRACKS_PASSWORD", "pw")
    saved = []
    monkeypatch.setattr(owntracks, "save_point", saved.append)
    body = {"_type": "location", "lat": 25.03, "lon": 121.56, "tst": 1761350400,
            "acc": 12, "vel": -1, "tid": "JH"}
    assert owntracks.handle_post(basic("pw"), body) == (200, [])
    assert saved[0]["lat"] == 25.03 and saved[0]["vel"] is None   # -1 = 沒有速度


def test_store_failure_returns_500_so_phone_retries(monkeypatch):
    monkeypatch.setenv("OWNTRACKS_PASSWORD", "pw")

    def boom(p):
        raise RuntimeError("notion down")
    monkeypatch.setattr(owntracks, "save_point", boom)
    body = {"_type": "location", "lat": 25.03, "lon": 121.56, "tst": 1761350400}
    status, _ = owntracks.handle_post(basic("pw"), body)
    assert status == 500


def test_wrong_password_401(monkeypatch):
    monkeypatch.setenv("OWNTRACKS_PASSWORD", "pw")
    assert owntracks.handle_post(basic("nope"), {})[0] == 401


def test_bad_coordinates_ignored():
    assert owntracks.parse_location({"_type": "location", "lat": 999, "lon": 0, "tst": 1}) is None
    assert owntracks.parse_location({"_type": "location", "lat": "x", "lon": 0, "tst": 1}) is None


# ── 整理 ─────────────────────────────────────────────

def _workday():
    return [
        pt(at(-2), HOME),                 # 前一晚 22:00 在家 → 決定半夜在哪
        pt(at(8, 30), WORK, lat=25.040, lon=121.540),   # 路上（沒對到地點、很快離開）
        pt(at(9), WORK),
        pt(at(12), WORK),
        pt(at(18), HOME, lat=25.040, lon=121.540),
        pt(at(18, 30), HOME),
    ]


def test_workday_hours_add_up():
    s = owntracks.summarize(_workday(), [HOME, WORK], DAY0, DAY1)
    assert s["home_h"] == 14.0            # 00:00–08:30 + 18:30–24:00
    assert s["work_h"] == 9.0             # 09:00–18:00
    assert s["out_h"] == 10.0             # 24 − 在家
    assert s["new_stops"] == []           # 路上兩段各 30 分鐘但都只有 1 點 → 是移動不是停留
    assert set(s["stays"]) == {"home", "work"}


def test_long_unknown_stop_becomes_new_place():
    cafe = dict(lat=25.0600, lon=121.5300)
    pts = [pt(at(0), HOME), pt(at(14), None, **cafe), pt(at(14, 40), None, **cafe), pt(at(16), HOME)]
    s = owntracks.summarize(pts, [HOME], DAY0, DAY1)
    assert len(s["new_stops"]) == 1
    stop = s["new_stops"][0]
    assert stop["end"] - stop["start"] == 2 * 3600


def test_single_point_long_stop_counts():
    pts = [pt(at(0), HOME), pt(at(14), None, lat=25.06, lon=121.53), pt(at(15, 30), HOME)]
    assert len(owntracks.summarize(pts, [HOME], DAY0, DAY1)["new_stops"]) == 1


def test_short_unknown_stop_is_just_travel():
    pts = [pt(at(0), HOME), pt(at(14), None, lat=25.06, lon=121.53), pt(at(14, 10), HOME)]
    s = owntracks.summarize(pts, [HOME], DAY0, DAY1)
    assert s["new_stops"] == []
    assert s["out_h"] == round(10 / 60, 1)


def test_inaccurate_points_dropped():
    pts = [pt(at(0), HOME), pt(at(12), WORK, acc=2000)]
    s = owntracks.summarize(pts, [HOME, WORK], DAY0, DAY1)
    assert s["home_h"] == 24.0


def test_no_data_before_first_point_is_not_counted():
    """2026-10-24 第一天開 OwnTracks：14:08 才有第一點，卻被算成外出 14.3 小時。"""
    cafe = dict(lat=24.1367, lon=120.6951)
    s = owntracks.summarize([pt(at(14), None, **cafe)], [HOME], DAY0, at(14, 20))
    assert s["out_h"] == 0.3
    assert owntracks.summarize([pt(at(7), HOME)], [HOME], DAY0, DAY1)["home_h"] == 17.0


def test_km_ignores_teleport_jumps():
    pts = [pt(at(1), HOME), pt(at(1, 1), None, lat=35.0, lon=121.56), pt(at(1, 2), HOME)]
    assert owntracks.moved_km(pts, DAY0, DAY1) == 0.0


def test_km_counts_real_travel():
    pts = [pt(at(8), HOME), pt(at(9), WORK)]
    km = owntracks.moved_km(pts, DAY0, DAY1)
    assert 4.5 < km < 5.5


def test_itinerary_format_matches_google_import():
    segs = owntracks.build_segments(_workday(), [HOME, WORK], DAY0, DAY1)
    names = {i: s["place"]["name"] for i, s in enumerate(segs) if s["place"]}
    trip = owntracks.itinerary(segs, names, DAY1)
    assert trip == "00:00–08:30 家；09:00–18:00 公司；18:30–00:00 家"


def test_nightly_job_registered():
    src = (Path(__file__).resolve().parent.parent / "server.py").read_text(encoding="utf-8")
    assert 'id="owntracks_nightly"' in src
    assert '@app.post("/owntracks")' in src


# ── run_for_day（假 Notion）────────────────────────────

class _FakeNotion:
    def __init__(self, daily_row=None):
        self.created, self.updated = [], []
        self.daily_row = daily_row
        fake = self

        class Pages:
            def create(self, parent, properties):
                fake.created.append((parent, properties))
                return {"id": f"new{len(fake.created)}"}

            def update(self, page_id, properties=None, **kw):
                fake.updated.append((page_id, properties))

        class Dbs:
            def query(self, **kw):
                return {"results": [fake.daily_row] if fake.daily_row else []}

        self.pages, self.databases = Pages(), Dbs()


def _place_row(pl, visits=3):
    return {"id": pl["id"], "lat": pl["lat"], "lon": pl["lon"], "name": pl["name"],
            "category": pl["category"], "visits": visits, "hours": 10, "first": "2026-06-01"}


def _setup(monkeypatch, fake, points):
    import notion_db
    from datetime import date
    monkeypatch.setattr(notion_db, "_get_client", lambda: fake)
    monkeypatch.setattr(owntracks, "load_points", lambda c, a, b: points)
    monkeypatch.setattr(owntracks, "load_places",
                        lambda c: ([_place_row(HOME), _place_row(WORK)], 64))
    monkeypatch.setattr(owntracks.time, "sleep", lambda s: None)
    monkeypatch.setattr(owntracks.time, "time", lambda: DAY1 + 3600)
    monkeypatch.setattr(owntracks, "reverse_geocode", lambda lat, lon: (None, None))
    return date(2026, 10, 25)


def test_new_day_row_created_and_places_counted(monkeypatch):
    fake = _FakeNotion()
    day = _setup(monkeypatch, fake, _workday())
    r = owntracks.run_for_day(day)
    assert r["overwrote_numbers"] and r["home_h"] == 14.0
    title = fake.created[0][1]["日期標題"]["title"][0]["text"]["content"]
    assert title == "2026-10-25（日）"
    day_update = fake.updated[0][1]
    assert day_update["行程"]["rich_text"][0]["text"]["content"].startswith("📡 ")
    place_updates = {pid: p for pid, p in fake.updated[1:]}
    assert place_updates["home"]["造訪次數"]["number"] == 4


def test_manual_itinerary_kept_and_rerun_does_not_double_count(monkeypatch):
    row = {"id": "d1", "properties": {
        "行程": {"rich_text": [{"plain_text": "白天上班（已更正）"}]},
        "去過的地點": {"relation": [{"id": "home"}]},
    }}
    fake = _FakeNotion(daily_row=row)
    day = _setup(monkeypatch, fake, _workday())
    r = owntracks.run_for_day(day)
    assert r["overwrote_numbers"] is False
    day_update = fake.updated[0][1]
    assert "外出時數" not in day_update and "行程" not in day_update
    assert {x["id"] for x in day_update["去過的地點"]["relation"]} == {"home", "work"}
    assert [pid for pid, _ in fake.updated[1:]] == ["work"]   # home 已連過，不再 +1


def test_new_place_numbered_after_existing(monkeypatch):
    cafe = dict(lat=25.0600, lon=121.5300)
    pts = [pt(at(0), HOME), pt(at(14), None, **cafe), pt(at(14, 40), None, **cafe), pt(at(16), HOME)]
    fake = _FakeNotion()
    day = _setup(monkeypatch, fake, pts)
    r = owntracks.run_for_day(day)
    assert r["new_places"] == 1
    names = [p["名稱"]["title"][0]["text"]["content"] for _, p in fake.created if "名稱" in p]
    assert names == ["地點 #065"]


def test_load_points_uses_title_timestamp_not_minute_truncated_date(monkeypatch):
    import notion_db

    def row(title, lat):
        return {"properties": {
            "點": {"title": [{"plain_text": title}]},
            "時間": {"date": {"start": "2026-10-24T06:08:00.000Z"}},   # Notion 截掉秒數
            "緯度": {"number": lat}, "經度": {"number": 120.69},
            "精確度": {"number": 8}, "速度": {"number": 0}, "裝置": {"rich_text": []},
        }}
    rows = [row("52-1792822117", 24.1), row("52-1792822117", 24.1), row("52-1792822121", 24.2)]
    monkeypatch.setattr(notion_db, "get_or_create_db", lambda n: "db")
    monkeypatch.setattr(notion_db, "_query_all", lambda *a, **k: rows)
    pts = owntracks.load_points(None, 0, 2**31)
    assert [p["tst"] for p in pts] == [1792822117, 1792822121]   # 重送的去重、同分鐘的不合併


def test_refresh_command_admin_only(monkeypatch):
    import command_router
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")
    monkeypatch.setattr(owntracks, "refresh_today_reply", lambda: "OK")
    assert command_router.handle("整理定位", {"source_type": "user", "user_id": "Uadmin"}) == "OK"
    assert command_router.handle("整理定位", {"source_type": "user", "user_id": "Uother"}) != "OK"


def test_refresh_reply_explains_failure(monkeypatch):
    def boom(day):
        raise RuntimeError("Could not find database")
    monkeypatch.setattr(owntracks, "run_for_day", boom)
    assert "生活紀錄" in owntracks.refresh_today_reply()


# ── 反查地名 ─────────────────────────────────────────

def test_poi_name_with_district():
    d = {"category": "amenity", "type": "cafe", "name": "路易莎咖啡",
         "address": {"city": "臺中市", "suburb": "西區", "road": "公益路"}}
    assert owntracks.name_from_nominatim(d) == ("路易莎咖啡（臺中市西區）", "咖啡廳")


def test_street_address_when_no_poi():
    d = {"category": "highway", "type": "residential", "name": "公益路",
         "address": {"city": "臺中市", "city_district": "西區", "road": "公益路", "house_number": "123"}}
    assert owntracks.name_from_nominatim(d) == ("臺中市西區公益路123號", "其他")


def test_area_only_and_errors():
    assert owntracks.name_from_nominatim({"address": {"county": "南投縣", "town": "仁愛鄉"}}) == ("南投縣仁愛鄉", "其他")
    assert owntracks.name_from_nominatim({"error": "Unable to geocode"}) == (None, None)
    assert owntracks.name_from_nominatim(None) == (None, None)


def test_shop_maps_to_shopping():
    d = {"category": "shop", "type": "supermarket", "name": "全聯", "address": {"city": "臺北市", "suburb": "大安區"}}
    assert owntracks.name_from_nominatim(d)[1] == "購物"


def test_new_place_gets_geocoded_name(monkeypatch):
    cafe = dict(lat=25.0600, lon=121.5300)
    pts = [pt(at(0), HOME), pt(at(14), None, **cafe), pt(at(14, 40), None, **cafe), pt(at(16), HOME)]
    fake = _FakeNotion()
    day = _setup(monkeypatch, fake, pts)
    monkeypatch.setattr(owntracks, "reverse_geocode", lambda lat, lon: ("某咖啡（臺北市中山區）", "咖啡廳"))
    owntracks.run_for_day(day)
    place = next(p for _, p in fake.created if "名稱" in p)
    assert place["名稱"]["title"][0]["text"]["content"] == "某咖啡（臺北市中山區）"
    assert place["類別"]["select"]["name"] == "咖啡廳"


def test_rename_only_touches_auto_unnamed(monkeypatch):
    monkeypatch.setattr(owntracks.time, "sleep", lambda s: None)
    monkeypatch.setattr(owntracks, "reverse_geocode", lambda lat, lon: ("新名字", "餐廳"))
    fake = _FakeNotion()
    places = [
        {"id": "a", "name": "地點 #065", "auto": True, "category": "其他", "lat": 1, "lon": 1},
        {"id": "b", "name": "地點 #001", "auto": False, "category": "其他", "lat": 1, "lon": 1},  # Google 匯入的
        {"id": "c", "name": "我改過的名字", "auto": True, "category": "其他", "lat": 1, "lon": 1},
    ]
    owntracks.rename_unnamed(fake, places)
    assert [pid for pid, _ in fake.updated] == ["a"]
    assert places[0]["name"] == "新名字"


# ── Google Places ─────────────────────────────────────

def test_google_name_with_district():
    d = {"places": [{"displayName": {"text": "春水堂 公益店"},
                     "formattedAddress": "403台灣臺中市西區公益路二段51號",
                     "primaryType": "tea_house", "types": ["tea_house", "food"]}]}
    assert owntracks.name_from_google(d) == ("春水堂 公益店（臺中市西區）", "咖啡廳")


def test_google_category_mapping():
    assert owntracks._google_category("ramen_restaurant", []) == "餐廳"
    assert owntracks._google_category("convenience_store", []) == "購物"
    assert owntracks._google_category(None, ["point_of_interest"]) == "其他"


def test_google_empty_falls_back_to_osm(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    monkeypatch.setattr(owntracks, "_google_nearby", lambda lat, lon, key: (None, None))
    monkeypatch.setattr(owntracks, "_osm_reverse", lambda lat, lon: ("臺中市西區公益路1號", "其他"))
    assert owntracks.reverse_geocode(24.1, 120.6) == ("臺中市西區公益路1號", "其他")


def test_google_error_falls_back_to_osm(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")

    def boom(lat, lon, key):
        raise RuntimeError("403")
    monkeypatch.setattr(owntracks, "_google_nearby", boom)
    monkeypatch.setattr(owntracks, "_osm_reverse", lambda lat, lon: ("OSM", "其他"))
    assert owntracks.reverse_geocode(24.1, 120.6) == ("OSM", "其他")


def test_no_key_skips_google(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.setattr(owntracks, "_google_nearby", lambda *a: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(owntracks, "_osm_reverse", lambda lat, lon: ("OSM", "其他"))
    assert owntracks.reverse_geocode(24.1, 120.6)[0] == "OSM"
