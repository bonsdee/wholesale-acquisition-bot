"""GoHighLevel: the workflow webhook in, the pipeline stage out. GHL itself is a fake on httpx."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from acqbot.api.app import create_app
from acqbot.config import get_settings
from acqbot.conversation.transitions import escalate, transition
from acqbot.crm import ghl
from acqbot.db import session_scope
from acqbot.models import CrmLink, Job, Lead, LeadState
from acqbot.queue.worker import drain

STAGES = [
    "New Lead",
    "Contacted",
    "In Conversation",
    "Verifying Details",
    "Offer Made",
    "Negotiating",
    "Offer Accepted",
    "Inspection / Pickup",
    "Purchased",
]


class FakeGhl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.calls.append((request.method, path, body))
        assert request.headers["Authorization"] == "Bearer pit-test"
        if path == "/opportunities/pipelines":
            stages = [{"id": f"st-{i}", "name": n} for i, n in enumerate(STAGES)]
            return httpx.Response(
                200,
                json={
                    "pipelines": [
                        {"id": "other", "name": "Marketing"},
                        {"id": "pl-1", "name": "Car Acquisition", "stages": stages},
                    ]
                },
            )
        if path == "/contacts/upsert":
            return httpx.Response(200, json={"contact": {"id": "c-upserted"}})
        if path == "/opportunities/" and request.method == "POST":
            return httpx.Response(201, json={"opportunity": {"id": "opp-1"}})
        return httpx.Response(200, json={})

    def find(self, method: str, prefix: str) -> list[dict]:
        return [b for m, p, b in self.calls if m == method and p.startswith(prefix)]


def stage_id(name: str) -> str:
    return f"st-{STAGES.index(name)}"


@pytest.fixture
def fake(settings_env):
    settings_env(ghl_api_key="pit-test", ghl_location_id="loc-1", ghl_inbound_token="tok")
    f = FakeGhl()
    ghl.set_client(ghl.GhlClient(get_settings(), http=httpx.Client(transport=httpx.MockTransport(f))))
    yield f
    ghl.set_client(None)


@pytest.fixture
def client(fake):
    return TestClient(create_app())


def webhook(**over):
    body = {
        "contact_id": "c-42",
        "full_name": "Sam Seller",
        "phone": "0412 345 678",
        "Make": "Toyota",
        "Model": "Corolla",
        "Year": "2018",
        "Odometer (km)": "85,000",
        "Asking Price": "$18,500",
        "postal_code": "3124",
        "city": "Camberwell",
        "customData": {"rego": "ABC123", "rego_state": "VIC"},
    }
    body.update(over)
    return body


def ghl_jobs(kind: str = "ghl_sync") -> int:
    with session_scope() as s:
        return s.query(Job).filter_by(kind=kind).count()


def test_webhook_needs_the_token(client, settings_env):
    assert client.post("/webhooks/ghl", json=webhook()).status_code == 401
    assert client.post("/webhooks/ghl?token=nope", json=webhook()).status_code == 401
    settings_env(ghl_inbound_token="")
    assert client.post("/webhooks/ghl", json=webhook(), headers={"X-Acqbot-Token": "tok"}).status_code == 404


def test_webhook_lead_is_ingested_linked_and_synced(client, fake):
    r = client.post("/webhooks/ghl", json=webhook(), headers={"X-Acqbot-Token": "tok"})
    assert r.status_code == 201, r.text
    lead_id = uuid.UUID(r.json()["lead_id"])
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        assert lead.source == "ghl"
        assert lead.upstream_payload["vehicle_claimed"]["asking_price_aud"] == 18500
        assert s.get(CrmLink, lead_id).contact_id == "c-42"

    # A workflow that fires twice lands on the same lead.
    again = client.post("/webhooks/ghl?token=tok", json=webhook())
    assert again.status_code == 200 and again.json()["status"] == "idempotent"

    drain(kinds=["ghl_sync"])
    assert fake.find("POST", "/contacts/upsert") == []  # the contact already exists in GHL
    (opp,) = fake.find("POST", "/opportunities/")
    assert opp["pipelineId"] == "pl-1" and opp["contactId"] == "c-42"
    assert opp["pipelineStageId"] == stage_id("New Lead") and opp["status"] == "open"
    assert opp["name"] == "2018 Toyota Corolla" and opp["monetaryValue"] == 18500
    fields = {f["key"]: f["field_value"] for f in fake.find("PUT", "/contacts/c-42")[0]["customFields"]}
    assert fields["contact.rego"] == "ABC123" and fields["contact.bot_lead_id"] == str(lead_id)


def test_webhook_missing_details_tags_the_contact(client, fake):
    r = client.post("/webhooks/ghl?token=tok", json=webhook(Year="", **{"Asking Price": ""}))
    assert r.status_code == 422
    assert r.json()["missing"] == ["year", "asking_price"]
    with session_scope() as s:
        assert s.query(Lead).count() == 0
    drain(kinds=["ghl_tag_contact"])
    assert fake.find("POST", "/contacts/c-42/tags") == [{"tags": ["bot-missing-details"]}]
    assert "year, asking_price" in fake.find("POST", "/contacts/c-42/notes")[0]["body"]


def test_state_changes_move_the_opportunity(client, fake):
    lead_id = uuid.UUID(client.post("/webhooks/ghl?token=tok", json=webhook()).json()["lead_id"])
    drain(kinds=["ghl_sync"])

    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        transition(s, lead, LeadState.CONTACTED, "test")
        transition(s, lead, LeadState.ENGAGED, "test")
    drain(kinds=["ghl_sync"])
    assert fake.find("PUT", "/opportunities/opp-1")[-1]["pipelineStageId"] == stage_id("In Conversation")

    with session_scope() as s:
        escalate(s, s.get(Lead, lead_id), "seller_asked_for_person")
    drain(kinds=["ghl_sync"])
    last = fake.find("PUT", "/opportunities/opp-1")[-1]
    assert "pipelineStageId" not in last and last["status"] == "open"  # keeps its place on the board
    assert fake.find("POST", "/contacts/c-42/tags") == [{"tags": ["needs-human"]}]
    assert "routed_to_human" in fake.find("POST", "/contacts/c-42/notes")[0]["body"]

    with session_scope() as s:
        transition(s, s.get(Lead, lead_id), LeadState.REJECTED, "test")
    drain(kinds=["ghl_sync"])
    assert fake.find("PUT", "/opportunities/opp-1")[-1]["status"] == "lost"
    assert fake.find("DELETE", "/contacts/c-42/tags") == [{"tags": ["needs-human"]}]


def test_lead_from_elsewhere_gets_a_contact_by_phone(fake, session):
    from acqbot.ingestion.service import ingest_lead

    payload = ghl.lead_from_webhook(webhook(contact_id="x"), get_settings())[0]
    payload["lead_id"] = str(uuid.uuid4())
    payload["source"] = "fb_marketplace"
    payload["seller"]["platform_id"] = "console:1"
    lead_id = ingest_lead(session, payload).lead_id
    transition(session, session.get(Lead, lead_id), LeadState.CONTACTED, "test")
    session.commit()
    drain(kinds=["ghl_sync"])
    assert fake.find("POST", "/contacts/upsert")[0]["phone"] == "+61412345678"
    assert fake.find("POST", "/opportunities/")[0]["pipelineStageId"] == stage_id("Contacted")


def test_nothing_is_queued_without_ghl(session, settings_env):
    settings_env(ghl_api_key="", ghl_location_id="")
    from acqbot.ingestion.service import ingest_lead

    payload = ghl.lead_from_webhook(webhook(), get_settings())[0]
    lead_id = ingest_lead(session, payload).lead_id
    transition(session, session.get(Lead, lead_id), LeadState.CONTACTED, "test")
    session.commit()
    assert ghl_jobs() == 0
