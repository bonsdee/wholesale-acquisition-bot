"""Running before the data contracts exist — "humans occupying the not-yet-built stages" [12].

Three properties, each of which would be a quiet disaster if it failed:
  * made-up data never reaches a real seller: production refuses to start on stub providers;
  * with the PPSR set to "manual", a person records the certificate and the lead neither stalls
    nor skips the hard gate — written off or stolen stops it, before contact or after;
  * with pricing set to "manual", every figure a seller sees was typed by a person, and no
    automation — not the opening, not a concession — ever produces one.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from acqbot.api import admin
from acqbot.api.app import create_app
from acqbot.cli import app as cli_app
from acqbot.config import get_settings
from acqbot.db import session_scope
from acqbot.demo import ANSWERS, _asked_field, _seller_says, _state
from acqbot.enrichment.providers import UnsafeConfiguration, assert_no_test_data, get_providers
from acqbot.facts.store import fact_sheet
from acqbot.ingestion.service import ingest_lead
from acqbot.models import Escalation, Lead, LeadState, Message, Offer
from acqbot.observability import checks
from acqbot.queue.worker import drain
from acqbot.simulator import make_lead
from acqbot.transport.registry import reset_console_transport
from acqbot.valuation.service import is_manual, latest_valuation

MANUAL = dict(
    vin_provider="manual",
    ppsr_provider="manual",
    rego_provider="manual",
    guide_provider="manual",
    comps_provider="manual",
)
TOKEN = "pilot-console-token"


# --------------------------------------------------------------------------- test data


def test_production_refuses_to_start_on_made_up_data(settings_env):
    settings_env(environment="production")
    with pytest.raises(UnsafeConfiguration, match="PPSR check"):
        assert_no_test_data()
    with pytest.raises(UnsafeConfiguration):
        create_app()
    with pytest.raises(UnsafeConfiguration):
        get_providers()
    result = CliRunner().invoke(cli_app, ["worker", "--once"])
    assert result.exit_code == 2 and "refusing to start" in result.output


def test_production_starts_when_a_person_does_the_checks_and_the_pricing(settings_env, session):
    settings_env(environment="production", **MANUAL)
    create_app()  # no exception
    providers = get_providers()
    assert providers.ppsr is None and providers.guide is None
    check = next(c for c in checks(session).checks if c.name == "test_data")
    assert check.ok and "done by a person: rego check, VIN decode, PPSR check" in check.detail


def test_development_may_use_test_data_and_the_console_says_so(settings_env, session):
    settings_env(admin_token=TOKEN)
    check = next(c for c in checks(session).checks if c.name == "test_data")
    assert check.ok and "fine for development" in check.detail
    client = TestClient(create_app(), follow_redirects=False)
    client.post("/console/login", data={"token": TOKEN})
    assert "Test data: the rego check, VIN decode, PPSR check" in client.get("/console").text


# --------------------------------------------------------------------------- the flow


def _start(seed: int, seller_id: str):
    console = reset_console_transport()
    payload = make_lead("clean", seed=seed)
    payload["seller"]["platform_id"] = seller_id
    with session_scope() as s:
        lead_id = ingest_lead(s, payload).lead_id
    drain("test")
    vc = payload["vehicle_claimed"]
    fill = {
        "variant": vc.get("variant") or "GX",
        "rego": vc.get("rego") or "1AB2CD",
        "odometer": f"{vc['odometer_km'] + 400:,}",
    }

    def say(text, **kw):
        return _seller_says(console, lead_id, text, seller_id=seller_id, **kw)

    return lead_id, say, fill


def _through_discovery(lead_id, say, fill):
    say("Hi, saw you're keen on my car", ref=True)
    photos = [{"type": "image", "url": f"https://example.invalid/{lead_id}/{i}.jpg"} for i in range(5)]
    for _ in range(25):
        if _state(lead_id) != "DISCOVERY" and _state(lead_id) != "CONTACTED":
            break
        asked = _asked_field(lead_id)
        if asked == "photos":
            say("Here you go", attachments=photos)
        else:
            say(ANSWERS.get(asked, "Sure").format(**fill))


def _open(lead_id, reason):
    with session_scope() as s:
        return s.scalars(
            select(Escalation).where(
                Escalation.lead_id == lead_id, Escalation.reason == reason, Escalation.resolved_at.is_(None)
            )
        ).first()


def _last_out(lead_id) -> Message:
    with session_scope() as s:
        m = s.scalars(
            select(Message)
            .where(Message.lead_id == lead_id, Message.direction == "outbound")
            .order_by(Message.sent_at.desc(), Message.msg_id.desc())
        ).first()
        s.expunge(m)
        return m


def _clean_checks(lead_id, **over) -> admin.RecordChecks:
    with session_scope() as s:
        sheet = fact_sheet(s, lead_id)
        year, odo = int(sheet.get("year")), int(sheet.get("odometer_km"))
    fields = dict(
        finance_owing=False,
        written_off="no",
        stolen=False,
        rego_status="current",
        rego_expiry=date(2027, 3, 31),
        year=year,
        odometer_km=odo,
        certificate="PPSR-123456",
        by="vic",
    )
    return admin.RecordChecks(**{**fields, **over})


def test_a_person_runs_the_ppsr_and_sets_every_figure(settings_env):
    # Automatic presentation switched ON, to prove it cannot price a car nobody has priced.
    settings_env(**MANUAL, auto_present_offer="true")
    lead_id, say, fill = _start(61, "pilot-seller-1")
    _through_discovery(lead_id, say, fill)

    # Discovery is done and nothing automated can verify the PPSR: a person is asked, the seller
    # has been told a number is coming, and nothing has been priced.
    assert _state(lead_id) == "VERIFICATION"
    task = _open(lead_id, "checks_needed")
    assert task is not None and set(task.details["outstanding"]) >= {"finance_owing", "write_off_status"}
    assert "running the checks now" in _last_out(lead_id).body
    with session_scope() as s:
        assert latest_valuation(s, lead_id) is None

    out = admin.record_checks(lead_id, _clean_checks(lead_id))
    assert out["result"] == "clear"
    drain("test")
    assert _open(lead_id, "checks_needed") is None
    with session_scope() as s:
        val = latest_valuation(s, lead_id)
        assert is_manual(val) and val.basis.value == "verified" and val.ladder == {}
        assert not list(s.scalars(select(Offer).where(Offer.lead_id == lead_id)))
    assert _state(lead_id) == "PRICED"
    assert _open(lead_id, "price_needed") is not None
    assert "working out a firm number" in _last_out(lead_id).body

    # No ladder to present from, whoever asks.
    with pytest.raises(Exception, match="Pricing is manual"):
        admin.present_offer(lead_id, admin.PresentOffer(step="opening", by="vic"))

    # The first figure a person types is the offer itself, not "I can go to".
    admin.present_offer(lead_id, admin.PresentOffer(step="human", by="vic", amount_aud=12_345))
    first = _last_out(lead_id)
    assert first.body.startswith("Here's where we've landed: $12,345 for the")
    assert "firm offer" in first.body and first.validated
    assert _open(lead_id, "price_needed") is None

    # A counter goes to a person, not up an automated ladder that does not exist.
    say("Too low, I was hoping for more")
    assert _open(lead_id, "offer_response_needed") is not None
    with session_scope() as s:
        assert len(list(s.scalars(select(Offer).where(Offer.lead_id == lead_id)))) == 1

    admin.present_offer(lead_id, admin.PresentOffer(step="human", by="vic", amount_aud=13_000))
    assert _last_out(lead_id).body.startswith("I can go to $13,000")


def test_a_registry_result_the_seller_disputed_goes_to_the_person_not_back_to_the_seller(settings_env):
    settings_env(**MANUAL)
    lead_id, say, fill = _start(62, "pilot-seller-2")
    _through_discovery(lead_id, say, fill)  # the seller said "No finance"
    out = admin.record_checks(lead_id, _clean_checks(lead_id, finance_owing=True, amount_owing_aud=5_000))
    assert out["result"] == "clear"
    drain("test")
    with session_scope() as s:
        sheet = fact_sheet(s, lead_id)
        assert "finance_owing" in sheet.contradicted
        assert "finance_owing" in (sheet.get("contradictions_acknowledged") or [])
        bodies = [m.body for m in s.scalars(select(Message).where(Message.lead_id == lead_id))]
    assert not any("Which is right?" in (b or "") for b in bodies)
    task = _open(lead_id, "price_needed")
    assert task is not None and task.details["finance_owing"]["amount_aud"] == 5_000


def test_written_off_before_contact_ends_the_lead_untouched(settings_env):
    settings_env(**MANUAL)
    lead_id, _, _ = _start(63, "pilot-seller-3")
    out = admin.record_checks(lead_id, _clean_checks(lead_id, written_off="statutory"))
    assert out["result"] == "terminated"
    assert _state(lead_id) == "TERMINATED"
    with session_scope() as s:
        assert not list(s.scalars(select(Message).where(Message.lead_id == lead_id)))


def test_stolen_after_contact_goes_to_a_person_who_tells_the_seller(settings_env):
    settings_env(**MANUAL)
    lead_id, say, fill = _start(64, "pilot-seller-4")
    say("Hi, saw you're keen on my car", ref=True)
    out = admin.record_checks(lead_id, _clean_checks(lead_id, stolen=True))
    assert out["result"] == "escalated" and out["reason"] == "ppsr_stolen"
    assert _state(lead_id) == "HUMAN"


def test_checks_the_form_would_let_through_by_mistake_are_refused(settings_env):
    settings_env(**MANUAL)
    lead_id, _, _ = _start(65, "pilot-seller-5")
    with pytest.raises(Exception, match="expiry"):
        admin.record_checks(lead_id, _clean_checks(lead_id, rego_expiry=None))
    with pytest.raises(Exception, match="VIN"):
        admin.record_checks(lead_id, _clean_checks(lead_id, vin="NOT-A-VIN"))


# --------------------------------------------------------------------------- the console


@pytest.fixture
def console(settings_env):
    settings_env(admin_token=TOKEN, messenger_page_username="directautoau", **MANUAL)
    client = TestClient(create_app(), follow_redirects=False)
    client.post("/console/login", data={"token": TOKEN})
    return client


LISTING = {
    "listing_url": "https://www.facebook.com/marketplace/item/1234567890123/",
    "seller_name": "Mei Chen",
    "seller_phone": "",
    "make": "Toyota",
    "model": "RAV4",
    "variant": "GX",
    "year": "2019",
    "odometer_km": "84,000 km",
    "asking_price": "$24,500",
    "transmission": "auto",
    "fuel": "petrol",
    "rego": "1AB2CD",
    "vin": "",
    "suburb": "Box Hill",
    "state": "VIC",
    "postcode": "3128",
    "description": "One owner, full service history.",
    "notes": "",
    "by": "Peter",
}


def test_a_salesperson_adds_a_listing_and_gets_the_message_to_send(console):
    assert "Add a lead from Marketplace" in console.get("/console/leads/new").text
    r = console.post("/console/leads/new", data=LISTING)
    assert r.status_code == 303
    lead_id = re.search(r"/console/leads/([0-9a-f-]{36})", r.headers["location"]).group(1)
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        assert lead.source == "fb_marketplace" and lead.state == LeadState.NEW
        assert lead.seller_platform_id == "fb-marketplace-item:1234567890123"
        assert fact_sheet(s, lead_id).get("asking_price_aud") == 24_500

    page = console.get(f"/console/leads/{lead_id}").text
    assert "Start the conversation" in page
    assert f"https://m.me/directautoau?ref={lead_id}" in page
    names = get_settings().agent_names
    assert any(f"Hi Mei, {n} from Direct Auto here (licensed dealer, LMCT 0013130)" in page for n in names)
    assert "Our automated assistant can take the details" in page
    assert "Record the PPSR check" in page and "run the PPSR check first" in page
    assert "Waiting for the seller to tap the link" in console.get("/console").text

    # The same listing again is the same lead, not a second one.
    again = console.post("/console/leads/new", data=LISTING)
    where = unquote(again.headers["location"])
    assert again.status_code == 303 and lead_id in where and "Already added" in where


def test_a_mistyped_listing_comes_back_with_what_was_typed(console):
    r = console.post("/console/leads/new", data={**LISTING, "postcode": "31", "year": "twenty"})
    assert r.status_code == 422
    assert "Year (km)" not in r.text and "Year: needs a number." in r.text
    assert 'value="Mei Chen"' in r.text  # nothing the salesperson typed is lost
    r = console.post("/console/leads/new", data={**LISTING, "postcode": "31"})
    assert r.status_code == 422 and "Postcode" in r.text


def test_the_console_records_the_ppsr_and_prices_by_hand(console):
    r = console.post("/console/leads/new", data=LISTING)
    lead_id = re.search(r"/console/leads/([0-9a-f-]{36})", r.headers["location"]).group(1)
    form = {
        "finance": "no",
        "written_off": "statutory",
        "stolen": "no",
        "rego_status": "current",
        "rego_expiry": "2027-03-31",
        "year": "2019",
        "by": "Peter",
    }
    r = console.post(f"/console/leads/{lead_id}/checks", data=form)
    assert r.status_code == 303 and "the seller was never contacted" in unquote(r.headers["location"])
    assert _state(lead_id) == "TERMINATED"
    assert "Record the PPSR check" not in console.get(f"/console/leads/{lead_id}").text

    # A form sent without a deliberate answer is refused rather than read as "no".
    r = console.post(
        "/console/leads/new",
        data={**LISTING, "listing_url": "https://www.facebook.com/marketplace/item/999/"},
    )
    other = re.search(r"/console/leads/([0-9a-f-]{36})", r.headers["location"]).group(1)
    r = console.post(f"/console/leads/{other}/checks", data={**form, "written_off": "", "stolen": ""})
    assert "m=bad" in r.headers["location"] and _state(other) == "NEW"


def test_a_manual_price_shows_an_amount_box_and_no_ladder(console):
    settings = get_settings()
    assert settings.guide_provider == "manual"
    lead_id, say, fill = _start(66, "pilot-seller-6")
    _through_discovery(lead_id, say, fill)
    admin.record_checks(lead_id, _clean_checks(lead_id))
    drain("test")
    page = console.get(f"/console/leads/{lead_id}").text
    assert "Pricing is manual" in page and "Set the price" in page
    assert "above the ceiling" not in page and "first concession" not in page
    assert "every figure a seller sees was typed in by a person" in page
    r = console.post(
        f"/console/leads/{lead_id}/present-offer",
        data={"step": "human", "by": "Peter", "amount_aud": "$15,500"},
    )
    assert r.status_code == 303 and "Offer sent: $15,500" in unquote(r.headers["location"])
    assert _last_out(lead_id).body.startswith("Here's where we've landed: $15,500")


def test_rego_disagrees_only_when_its_status_does():
    from acqbot.facts.store import materially_different

    said = {"status": "current", "expiry": "2027-03-01"}
    certificate = {"status": "current", "expiry": "2027-03-31", "state": "VIC", "checked_by": "Peter"}
    assert not materially_different("rego_status", said, certificate)
    assert materially_different("rego_status", said, {**certificate, "status": "expired"})


def test_the_suite_never_reads_a_developers_env_file():
    # A real API key or ACQBOT_ENVIRONMENT=production in someone's .env must not reach the tests.
    from acqbot.config import Settings

    assert Settings.model_config["env_file"] is None


@pytest.mark.parametrize(
    "args",
    [
        ["demo", "--quiet"],
        ["review", "--model", "fake", "--persona", "plain"],
        ["simulate-lead", "--ingest"],
        ["chat"],
    ],
)
def test_nothing_that_invents_sellers_runs_against_production(settings_env, args):
    settings_env(environment="production", **MANUAL)
    result = CliRunner().invoke(cli_app, args, input="quit\n")
    assert result.exit_code == 2, result.output
    assert "refusing to" in result.output and "local-test.ps1" in result.output
    with session_scope() as s:
        assert not list(s.scalars(select(Lead)))
