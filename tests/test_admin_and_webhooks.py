import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from acqbot.api.app import create_app
from acqbot.db import session_scope
from acqbot.demo import run_demo
from acqbot.models import Lead, LeadState, Message
from acqbot.queue.worker import drain
from acqbot.transport.registry import reset_console_transport

ADMIN = {"X-Admin-Token": "adm"}


@pytest.fixture
def client(settings_env):
    settings_env(admin_token="adm", messenger_app_secret="appsecret", messenger_verify_token="vtok")
    return TestClient(create_app())


def test_admin_requires_token(client, settings_env):
    assert client.get("/admin/escalations").status_code == 401
    assert client.get("/admin/escalations", headers={"X-Admin-Token": "nope"}).status_code == 401
    settings_env(admin_token="")
    assert client.get("/admin/escalations", headers=ADMIN).status_code == 404


def test_human_offer_loop_through_the_api(client):
    run = run_demo("clean", "negotiate", seed=42, human_presents=True)
    assert run.final_state == "HUMAN"
    lead_id = str(run.lead_id)

    esc = client.get("/admin/escalations", headers=ADMIN).json()
    open_reasons = {e["reason"] for e in esc if e["lead_id"] == lead_id}
    assert open_reasons == {"offer_response_needed"}

    # The human concedes one step through the console; the automation takes the thread back.
    r = client.post(
        f"/admin/leads/{lead_id}/present-offer", json={"step": "step_1", "by": "sam"}, headers=ADMIN
    )
    assert r.status_code == 200, r.text
    assert r.json()["step"] == "step_1" and r.json()["state"] == "OFFER_MADE"
    assert not [e for e in client.get("/admin/escalations", headers=ADMIN).json() if e["lead_id"] == lead_id]

    t = client.get(f"/admin/leads/{lead_id}/transcript", headers=ADMIN).json()
    assert t["messages"][-1]["direction"] == "outbound" and "I can go to" in t["messages"][-1]["body"]

    # Above-ladder amount is a human decision, recorded as step=human.
    r = client.post(
        f"/admin/leads/{lead_id}/present-offer",
        json={"step": "human", "by": "sam", "amount_aud": 99999},
        headers=ADMIN,
    )
    assert r.status_code == 200 and r.json()["step"] == "human"
    r = client.post(
        f"/admin/leads/{lead_id}/present-offer", json={"step": "floor", "by": "system"}, headers=ADMIN
    )
    assert r.status_code in {200, 409}

    r = client.post(
        f"/admin/leads/{lead_id}/offer-outcome", json={"outcome": "accepted", "by": "sam"}, headers=ADMIN
    )
    assert r.status_code == 200 and r.json()["state"] == "ACCEPTED"


def test_manual_fact_resolves_verification_and_revalues(client):
    run = run_demo("clean", "accept", seed=42, human_presents=True, max_turns=9)  # stops around verification
    lead_id = str(run.lead_id)
    r = client.post(
        f"/admin/leads/{lead_id}/facts",
        json={"field": "odometer_km", "value": 123456, "source": "inspection", "verified": True, "by": "sam"},
        headers=ADMIN,
    )
    assert r.status_code == 200 and r.json()["created"] is True
    assert r.json()["facts"]["confirmed"]["odometer_km"] == 123456
    drain("test")
    v = client.get(f"/admin/leads/{lead_id}/valuation", headers=ADMIN)
    assert v.status_code == 200 and set(v.json()["ladder"]) == {"opening", "step_1", "step_2", "floor"}


def test_resolve_escalation_returns_lead_to_automation(client):
    run = run_demo("clean", "legal", seed=42, human_presents=False)
    lead_id = str(run.lead_id)
    esc = [e for e in client.get("/admin/escalations", headers=ADMIN).json() if e["lead_id"] == lead_id][0]
    r = client.post(
        f"/admin/escalations/{esc['escalation_id']}/resolve",
        json={"by": "sam", "resolution": "spoke to seller, all fine", "return_to_automation": True},
        headers=ADMIN,
    )
    assert r.status_code == 200 and r.json()["resolved_by"] == "sam"
    with session_scope() as s:
        assert s.get(Lead, run.lead_id).state == LeadState.ENGAGED
    assert (
        client.post(
            f"/admin/escalations/{esc['escalation_id']}/resolve",
            json={"by": "sam", "resolution": "again"},
            headers=ADMIN,
        ).status_code
        == 409
    )


def test_messenger_webhook_verify_and_inbound(client, monkeypatch):
    console = reset_console_transport()
    monkeypatch.setattr("acqbot.transport.registry.get_transport", lambda channel: console)  # job handlers

    r = client.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "vtok", "hub.challenge": "42"},
    )
    assert r.status_code == 200 and r.text == "42"
    assert (
        client.get(
            "/webhooks/messenger",
            params={"hub.mode": "subscribe", "hub.verify_token": "bad", "hub.challenge": "42"},
        ).status_code
        == 403
    )

    from acqbot.ingestion.service import ingest_lead
    from acqbot.simulator import make_lead

    payload = make_lead("clean", seed=21)
    with session_scope() as s:
        lead_id = ingest_lead(s, payload).lead_id
    drain("test")

    event = {
        "object": "page",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "psid-77"},
                        "timestamp": 1_757_800_000_000,
                        "message": {"mid": "m1", "text": "hi", "referral": {"ref": str(lead_id)}},
                    }
                ]
            }
        ],
    }
    body = json.dumps(event).encode()
    sig = "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()
    assert (
        client.post(
            "/webhooks/messenger", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}
        ).status_code
        == 401
    )
    r = client.post(
        "/webhooks/messenger",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200 and r.json()["received"] == 1 and len(r.json()["queued"]) == 1
    assert console.last_sent("psid-77") is None  # nothing happens on the webhook itself
    # A redelivery of the same event is deduplicated by message id.
    r2 = client.post(
        "/webhooks/messenger",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert r2.json()["queued"] == ["duplicate"]
    drain("test")
    assert console.last_sent("psid-77") and "automated assistant" in console.last_sent("psid-77")
    with session_scope() as s:
        assert s.get(Lead, lead_id).state == LeadState.CONTACTED
        inbound = [m for m in s.query(Message).filter_by(lead_id=lead_id) if m.direction.value == "inbound"]
        assert len(inbound) == 1


def test_unlinked_thread_is_listed_and_linkable(client, monkeypatch):
    console = reset_console_transport()
    monkeypatch.setattr("acqbot.transport.registry.get_transport", lambda channel: console)  # job handlers
    event = {
        "object": "page",
        "entry": [
            {"messaging": [{"sender": {"id": "psid-orphan"}, "message": {"mid": "m1", "text": "hello?"}}]}
        ],
    }
    body = json.dumps(event).encode()
    sig = "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()
    r = client.post("/webhooks/messenger", content=body, headers={"X-Hub-Signature-256": sig})
    assert r.json()["received"] == 1
    drain("test")
    unlinked = client.get("/admin/threads/unlinked", headers=ADMIN).json()
    assert any(t["external_id"] == "psid-orphan" for t in unlinked)

    from acqbot.ingestion.service import ingest_lead
    from acqbot.simulator import make_lead

    with session_scope() as s:
        lead_id = ingest_lead(s, make_lead("clean", seed=22)).lead_id
    drain("test")
    tid = next(t["thread_id"] for t in unlinked if t["external_id"] == "psid-orphan")
    r = client.post(f"/admin/threads/{tid}/link", json={"lead_id": str(lead_id)}, headers=ADMIN)
    assert r.status_code == 200
    drain("test")  # maybe_send_opening
    assert console.last_sent("psid-orphan") and "automated assistant" in console.last_sent("psid-orphan")
