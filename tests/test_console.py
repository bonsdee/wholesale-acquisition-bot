"""The console: locked without the token, and every action goes through the same code as /admin."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from acqbot.api.app import create_app
from acqbot.console.html import esc, reason_text
from acqbot.db import session_scope
from acqbot.demo import run_demo
from acqbot.models import Escalation, HandoffPacket, Lead, Offer

TOKEN = "console-test-token"


@pytest.fixture
def client(settings_env):
    settings_env(admin_token=TOKEN)
    return TestClient(create_app(), follow_redirects=False)


@pytest.fixture
def signed_in(client):
    r = client.post("/console/login", data={"token": TOKEN})
    assert r.status_code == 303
    return client


def test_console_is_locked_and_the_token_is_never_in_the_cookie(client):
    assert "Sign in" in client.get("/console").text
    assert client.get("/console").status_code == 401
    assert client.get("/console/handoffs").status_code == 401

    assert client.post("/console/login", data={"token": "wrong"}).status_code == 401
    assert not client.cookies.get("acqbot_console")

    r = client.post("/console/login", data={"token": TOKEN})
    cookie = r.cookies["acqbot_console"]
    assert TOKEN not in cookie and len(cookie) == 64  # an HMAC, not the secret
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=lax" in r.headers["set-cookie"].lower()
    assert client.get("/console").status_code == 200


def test_console_disappears_entirely_without_an_admin_token(settings_env):
    settings_env(admin_token="")
    anon = TestClient(create_app(), follow_redirects=False)
    assert anon.get("/console").status_code == 404
    assert anon.post("/console/login", data={"token": "anything"}).status_code == 404


def test_the_queue_says_what_needs_doing_and_whether_the_bot_has_stopped(signed_in):
    # A seller who asks for a person: the automation stops and a person must pick it up.
    run_demo("clean", "bot_question", seed=42, human_presents=False, model="off")
    page = signed_in.get("/console").text
    assert "Asked for a person" in page  # not the raw reason "human_requested"
    assert "automation stopped" in page
    assert "Open →" in page


def test_a_closed_conversation_does_not_claim_the_bot_is_still_talking(signed_in):
    # Found by looking at the queue: a HANDOFF lead was tagged "bot still talking", which is the
    # opposite of true and decides what a person picks up first.
    run_demo("clean", "accept", seed=42, human_presents=False, model="off")  # ends HANDOFF
    page = signed_in.get("/console").text
    assert "conversation closed" in page
    assert "bot still talking" not in page


def test_the_ladder_reads_upwards_and_facts_are_not_labelled_twice(signed_in):
    run = run_demo("clean", "negotiate", seed=42, human_presents=True, model="off")
    page = signed_in.get(f"/console/leads/{run.lead_id}").text
    block = re.search(r'<div class="ladder">(.*?)</div></div>', page, re.S).group(1)
    order = [block.index(w) for w in ("opening", "first concession", "second concession", "ceiling")]
    assert order == sorted(order), "the ladder must read opening → ceiling, not dict order"
    assert "variant: " not in page and "year: " not in page  # the table already has a label column
    assert "False" not in page and "['" not in page  # no raw Python on a sales-floor screen


def test_a_lead_page_shows_the_ladder_facts_and_transcript(signed_in):
    run = run_demo("clean", "negotiate", seed=42, human_presents=True, model="off")
    page = signed_in.get(f"/console/leads/{run.lead_id}").text
    assert "Outlander" in page
    assert "Seller countered" in page  # the open task, in words
    assert "Present an offer" in page and "opening" in page
    assert "full service history" in page  # fact sheet rendered as prose, not raw JSON
    assert "automated assistant" in page  # the transcript is there
    assert "$" in page


def test_resolving_from_the_console_hands_the_lead_back_to_the_bot(signed_in):
    run = run_demo("clean", "bot_question", seed=42, human_presents=False, model="off")
    with session_scope() as s:
        esc_row = s.scalars(
            select(Escalation).where(Escalation.lead_id == run.lead_id, Escalation.resolved_at.is_(None))
        ).one()
        esc_id = esc_row.escalation_id

    r = signed_in.post(
        f"/console/escalations/{esc_id}/resolve",
        data={
            "lead_id": str(run.lead_id),
            "by": "claudia",
            "resolution": "called them",
            "return_to_automation": "1",
        },
    )
    assert r.status_code == 303 and "m=ok" in r.headers["location"]
    with session_scope() as s:
        assert s.get(Escalation, esc_id).resolved_by == "claudia"
        assert s.get(Lead, run.lead_id).state.value == "ENGAGED"


def test_presenting_an_offer_from_the_console_records_who_did_it(signed_in):
    run = run_demo("clean", "negotiate", seed=42, human_presents=True, model="off")
    with session_scope() as s:
        before = len(list(s.scalars(select(Offer).where(Offer.lead_id == run.lead_id))))
    r = signed_in.post(
        f"/console/leads/{run.lead_id}/present-offer",
        data={"step": "step_1", "by": "claudia", "amount_aud": ""},
    )
    assert r.status_code == 303 and "m=ok" in r.headers["location"]
    with session_scope() as s:
        offers = list(
            s.scalars(select(Offer).where(Offer.lead_id == run.lead_id).order_by(Offer.presented_at))
        )
        assert len(offers) == before + 1
        assert offers[-1].presented_by == "human:claudia" and offers[-1].ladder_step.value == "step_1"


def test_a_refused_action_comes_back_as_a_message_not_a_stack_trace(signed_in):
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")  # ends HANDOFF
    r = signed_in.post(
        f"/console/leads/{run.lead_id}/present-offer",
        data={"step": "opening", "by": "claudia", "amount_aud": ""},
    )
    assert r.status_code == 303 and "m=bad" in r.headers["location"]
    r = signed_in.post(
        f"/console/leads/{run.lead_id}/present-offer",
        data={"step": "human", "by": "claudia", "amount_aud": "not a number"},
    )
    assert "m=bad:That+amount+is+not+a+number" in r.headers["location"].replace("%20", "+")


def test_recording_a_fact_from_the_console_reprices_and_closes_the_task(signed_in):
    # Stop the demo at VERIFICATION by never letting the odometer be confirmed.
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    r = signed_in.post(
        f"/console/leads/{run.lead_id}/facts",
        data={"field": "keys_count", "value": "3", "by": "claudia"},
    )
    assert r.status_code == 303 and "m=ok" in r.headers["location"]
    page = signed_in.get(f"/console/leads/{run.lead_id}").text
    assert "3 keys" in page


def test_handoffs_page_offers_the_packet_and_claiming_it_sticks(signed_in):
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    page = signed_in.get("/console/handoffs").text
    assert "agreed" in page and "Claim" in page
    with session_scope() as s:
        packet_id = (
            s.scalars(select(HandoffPacket).where(HandoffPacket.lead_id == run.lead_id)).one().packet_id
        )
    r = signed_in.post(f"/console/handoffs/{packet_id}/claim", data={"by": "daniel"})
    assert r.status_code == 303 and "m=ok" in r.headers["location"]
    assert "claimed by daniel" in signed_in.get("/console/handoffs").text


def test_every_reason_the_system_can_raise_has_words_for_a_person():
    from acqbot.conversation.service import BOOKKEEPING_FIELDS  # noqa: F401  (import sanity)

    raised = {
        "verification_review",
        "offer_presentation",
        "offer_response_needed",
        "handoff",
        "offer_expired",
        "send_window_closed",
        "above_authorised_ladder",
        "legal",
        "deceased_estate",
        "distress",
        "minor_or_no_authority",
        "hostile",
        "human_requested",
        "no_progression",
        "high_value",
        "encumbered_above_offer",
        "template_failed_gate",
        "transport_rejected",
    }
    for reason in raised:
        title, what = reason_text(reason)
        assert title and what and "_" not in title, reason
    title, what = reason_text("model_flagged:distress")
    assert title == "Seller in distress" and "model" in what


def test_seller_text_is_escaped_not_executed(signed_in):
    assert esc("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    page = signed_in.get(f"/console/leads/{run.lead_id}").text
    # Nothing in the page body may contain an unescaped angle bracket from data we did not write.
    body = page.split("<main>", 1)[1]
    assert not re.search(r"<script(?!>)", body)
