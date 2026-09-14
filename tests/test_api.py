import json
import uuid

import pytest
from fastapi.testclient import TestClient

from acqbot.api.app import create_app
from acqbot.ingestion.security import SIGNATURE_HEADER, sign
from acqbot.simulator import make_lead

SECRET = "test-secret"


@pytest.fixture
def client():
    return TestClient(create_app())


def _post(client, payload, *, secret=SECRET, header=None):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if header is not None:
        headers[SIGNATURE_HEADER] = header
    elif secret is not None:
        headers[SIGNATURE_HEADER] = sign(secret, body)
    return client.post("/leads", content=body, headers=headers)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_signed_lead_is_accepted(client):
    payload = make_lead("clean", seed=20)
    r = _post(client, payload)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "accepted"

    r2 = client.get(f"/leads/{payload['lead_id']}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["state"] == "NEW"
    assert body["facts"]["claimed"]["make"] == payload["vehicle_claimed"]["make"]
    assert body["state_log"][0]["trigger"] == "lead_ingested"


def test_unsigned_lead_is_rejected(client):
    r = _post(client, make_lead("clean", seed=21), secret=None)
    assert r.status_code == 401


def test_wrong_secret_is_rejected(client):
    r = _post(client, make_lead("clean", seed=22), secret="not-the-secret")
    assert r.status_code == 401


def test_stale_signature_is_rejected(client):
    payload = make_lead("clean", seed=23)
    body = json.dumps(payload).encode()
    stale = sign(SECRET, body, timestamp=1_000_000)
    r = _post(client, payload, header=stale)
    assert r.status_code == 401


def test_malformed_payload_returns_422_with_field_errors(client):
    payload = make_lead("clean", seed=24)
    del payload["vehicle_claimed"]["odometer_km"]
    payload["vehicle_claimed"]["fuel"] = "steam"
    r = _post(client, payload)
    assert r.status_code == 422
    locs = {e["loc"] for e in r.json()["detail"]["errors"]}
    assert "vehicle_claimed.odometer_km" in locs and "vehicle_claimed.fuel" in locs


def test_invalid_json_returns_400(client):
    body = b"{not json"
    r = client.post("/leads", content=body, headers={SIGNATURE_HEADER: sign(SECRET, body)})
    assert r.status_code == 400


def test_duplicate_returns_200(client):
    payload = make_lead("clean", seed=25)
    assert _post(client, payload).status_code == 201
    relist = dict(payload, lead_id=str(uuid.uuid4()))
    r = _post(client, relist)
    assert r.status_code == 200 and r.json()["status"] == "duplicate"


def test_unknown_lead_404(client):
    assert client.get(f"/leads/{uuid.uuid4()}").status_code == 404


def test_unsigned_allowed_in_dev_mode(client, settings_env):
    settings_env(allow_unsigned_leads="true")
    r = _post(client, make_lead("clean", seed=26), secret=None)
    assert r.status_code == 201
