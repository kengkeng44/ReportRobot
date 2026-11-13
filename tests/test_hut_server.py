from datetime import date

import pytest
from fastapi.testclient import TestClient

import hut_monitor
import server
from hut_sources import Slot


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "t0k")
    monkeypatch.setenv("HUT_CHECK_ON_INGEST", "0")   # 不在測試裡真的去抓山屋網站
    hut_monitor.reset()
    yield TestClient(server.app)
    hut_monitor.reset()


def _payload():
    return {"slots": [Slot("onestop_99", "九九山莊", "bed", date(2026, 12, 5), 2, 7).to_dict()]}


def test_ingest_requires_token(client):
    assert client.post("/admin/hut-ingest", json=_payload()).status_code == 403


def test_ingest_ok(client):
    r = client.post("/admin/hut-ingest", json=_payload(), headers={"X-Admin-Token": "t0k"})
    assert r.status_code == 200 and r.json()["sites"] == ["onestop_99"]


def test_ingest_bad_site_400(client):
    bad = {"slots": [dict(_payload()["slots"][0], site="evil")]}
    r = client.post("/admin/hut-ingest", json=bad, headers={"X-Admin-Token": "t0k"})
    assert r.status_code == 400


def test_ingest_non_object_body_400(client):
    r = client.post("/admin/hut-ingest", json=[1, 2], headers={"X-Admin-Token": "t0k"})
    assert r.status_code == 400


def test_probe_and_run_require_token(client):
    assert client.post("/admin/hut-probe").status_code == 403
    assert client.post("/admin/hut-run").status_code == 403
