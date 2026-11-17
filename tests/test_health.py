"""Apple 健康接收：兩種格式的解析 + 驗證 + upsert。"""

from datetime import date

import health

DAY = date(2026, 11, 17)


def test_auth_closed_without_token(monkeypatch):
    monkeypatch.delenv("HEALTH_TOKEN", raising=False)
    assert health.check_auth("Bearer x") is False


def test_auth_bearer(monkeypatch):
    monkeypatch.setenv("HEALTH_TOKEN", "tok")
    assert health.check_auth("Bearer tok")
    assert not health.check_auth("Bearer nope")
    assert not health.check_auth("Basic tok")


def test_flat_strings_and_chinese_keys():
    days, w = health.parse_flat({
        "date": "2026/11/17", "steps": "8,123 步", "sleep_hours": "7.25",
        "spo2": "98 %", "體重": 70.4, "resting_hr": -3, "unknown": 1,
        "workouts": "跑步 30 分",
    })
    v = days[DAY]
    assert v["steps"] == 8123 and v["sleep_hours"] == 7.25
    assert v["spo2"] == 0.98 and v["weight_kg"] == 70.4
    assert "resting_hr" not in v
    assert w[DAY] == ["跑步 30 分"]


def test_flat_defaults_today():
    days, _ = health.parse_flat({"steps": 10}, today=DAY)
    assert days == {DAY: {"steps": 10}}


def test_hae_format():
    payload = {"data": {
        "metrics": [
            {"name": "step_count", "units": "count", "data": [
                {"date": "2026-11-17 00:00:00 +0800", "qty": 9000.4}]},
            {"name": "active_energy", "units": "kJ", "data": [
                {"date": "2026-11-17 00:00:00 +0800", "qty": 1673.6}]},
            {"name": "heart_rate", "units": "count/min", "data": [
                {"date": "2026-11-17 00:00:00 +0800", "Min": 50, "Avg": 72.3, "Max": 150}]},
            {"name": "blood_oxygen_saturation", "units": "%", "data": [
                {"date": "2026-11-17 00:00:00 +0800", "qty": 97}]},
            {"name": "sleep_analysis", "units": "hr", "data": [
                {"date": "2026-11-17 00:00:00 +0800", "totalSleep": 6.8, "deep": 1.1, "rem": 1.4}]},
        ],
        "workouts": [{"name": "Running", "start": "2026-11-17 07:00:00 +0800",
                      "duration": 1800, "activeEnergyBurned": {"qty": 300, "units": "kcal"}}],
    }}
    days, w = health.parse_hae(payload)
    v = days[DAY]
    assert v["steps"] == 9000 and v["active_kcal"] == 400 and v["avg_hr"] == 72
    assert v["spo2"] == 0.97
    assert (v["sleep_hours"], v["deep_hours"], v["rem_hours"]) == (6.8, 1.1, 1.4)
    assert w[DAY] == ["Running 30 分 300 kcal"]


class FakeClient:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.created, self.updated = [], []
        outer = self

        class DB:
            def query(self, database_id, filter, page_size):
                return {"results": [r for r in outer.rows if r["_db"] == database_id]}

        class Pages:
            def create(self, parent, properties):
                outer.created.append((parent["database_id"], properties))
                return {"id": f"new{len(outer.created)}"}

            def update(self, page_id, properties):
                outer.updated.append((page_id, properties))

        self.databases, self.pages = DB(), Pages()


def test_post_creates_row_and_links_daily(monkeypatch):
    import owntracks
    monkeypatch.setenv("HEALTH_TOKEN", "tok")
    fc = FakeClient()
    monkeypatch.setattr(health.notion_db, "_get_client", lambda: fc)
    status, body = health.handle_post("Bearer tok", {"date": "2026-11-17", "steps": 5}, today=DAY)
    assert status == 200 and body["days"] == {"2026-11-17": "created"}
    # 先建每日紀錄、再建健康列並連過去
    assert fc.created[0][0] == owntracks.DAILY_DB_ID
    db, props = fc.created[1]
    assert db == health.HEALTH_DB_ID
    assert props["步數"] == {"number": 5}
    assert props["每日紀錄"] == {"relation": [{"id": "new1"}]}


def test_post_updates_only_sent_fields(monkeypatch):
    monkeypatch.setenv("HEALTH_TOKEN", "tok")
    fc = FakeClient([{"_db": health.HEALTH_DB_ID, "id": "h1",
                      "properties": {"每日紀錄": {"relation": [{"id": "d1"}]}}}])
    monkeypatch.setattr(health.notion_db, "_get_client", lambda: fc)
    status, body = health.handle_post("Bearer tok", {"date": "2026-11-17", "sleep_hours": 7}, today=DAY)
    assert body["days"] == {"2026-11-17": "updated"}
    pid, props = fc.updated[0]
    assert pid == "h1" and "步數" not in props and props["睡眠小時"] == {"number": 7.0}
    assert "每日紀錄" not in props


def test_post_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("HEALTH_TOKEN", "tok")
    assert health.handle_post("Bearer x", {})[0] == 401
