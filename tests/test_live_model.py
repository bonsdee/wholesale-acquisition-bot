"""Against the real Claude API. Skipped unless ACQBOT_ANTHROPIC_API_KEY is set; costs a fraction of a cent.

ACQBOT_ANTHROPIC_API_KEY=sk-ant-... uv run pytest tests/test_live_model.py -v
"""

import os

import pytest

from acqbot.config import Settings
from acqbot.conversation.gate import GateContext, validate
from acqbot.facts.fields import spec_for
from acqbot.llm.client import AnthropicClient, ModelRequest
from acqbot.llm.prompts import (
    extraction_messages,
    extraction_system,
    generation_system,
    turn_context,
)
from acqbot.llm.schemas import EXTRACTION_SCHEMA, GENERATION_SCHEMA, parse_extraction, parse_generation
from acqbot.models import LeadState

KEY = os.environ.get("ACQBOT_ANTHROPIC_API_KEY", "")
pytestmark = pytest.mark.skipif(not KEY, reason="ACQBOT_ANTHROPIC_API_KEY not set")


@pytest.fixture(scope="module")
def cfg():
    return Settings(anthropic_api_key=KEY)


@pytest.fixture(scope="module")
def client(cfg):
    return AnthropicClient(KEY, timeout=cfg.llm_timeout_seconds, max_retries=cfg.llm_max_retries)


def test_extraction_reads_a_typical_answer(client, cfg):
    req = ModelRequest(
        purpose="extract",
        model=cfg.extraction_model,
        system=extraction_system(),
        messages=extraction_messages(
            vehicle="2015 Mitsubishi Outlander ES",
            pending_field="odometer_km",
            last_question=spec_for("odometer_km").ask,
            recent_turns=[("assistant", spec_for("odometer_km").ask)],
            body="It's on about 200,600 km. Full logbook too, and it comes with two keys. Do you pick up?",
            image_count=0,
        ),
        schema=EXTRACTION_SCHEMA,
        max_tokens=600,
        effort=cfg.llm_effort,
    )
    resp = client.complete(req)
    assert resp.stop_reason == "end_turn" and resp.parsed, resp.text
    out = parse_extraction(resp.parsed)
    facts = {f.field: f for f in out.facts}
    assert facts["odometer_km"].value == 200600
    assert facts["service_history"].value == "full" and facts["keys_count"].value == 2
    assert out.seller_question and "pick" in out.seller_question.lower()
    assert not out.flags


def test_generation_asks_the_field_and_passes_the_gate(client, cfg):
    system = generation_system(
        agent="Alex", dealership="Placeholder Motors", lmct="00000", channel="messenger", max_length=600
    ) + turn_context(
        stage="DISCOVERY",
        outstanding=["service_history", "keys_count", "photos"],
        sheet={
            "confirmed": {"make": "Mitsubishi", "model": "Outlander", "year": 2015},
            "claimed": {"odometer_km": 200600},
            "contradicted": {},
        },
        instruction=(
            'Ask for service history: "Service history — full logbook, partial, or none?" Put it in your own '
            "words. Offer the options: full / partial / none. Acknowledge in a few words what they just gave "
            "you (odometer_km) — no fuss."
        ),
        seller_first_name="Mei",
        channel="messenger",
        max_length=600,
    )
    req = ModelRequest(
        purpose="generate",
        model=cfg.conversation_model,
        system=system,
        messages=[
            {"role": "user", "content": "Hi, saw you're keen on my car"},
            {
                "role": "assistant",
                "content": "Hi Mei, Alex here. What's the exact odometer reading right now, in km?",
            },
            {"role": "user", "content": "It's on 200,600 km right now"},
        ],
        schema=GENERATION_SCHEMA,
        max_tokens=cfg.llm_max_output_tokens,
        effort=cfg.llm_effort,
    )
    resp = client.complete(req)
    assert resp.stop_reason == "end_turn" and resp.parsed, resp.text
    out = parse_generation(resp.parsed)
    assert out is not None and not out.escalate
    gate = validate(
        out.message,
        GateContext(
            stage=LeadState.DISCOVERY,
            vehicle_year=2015,
            vehicle_make="Mitsubishi",
            vehicle_odometer_km=200600,
            max_length=600,
        ),
    )
    assert gate.ok, (gate.violations, out.message)
    assert "?" in out.message and any(w in out.message.lower() for w in ("logbook", "service"))
