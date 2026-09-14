import pytest
from sqlalchemy import select

from acqbot.db import session_scope
from acqbot.enrichment.pipeline import enrich_lead
from acqbot.enrichment.protocols import ProviderError, Providers
from acqbot.enrichment.stubs import (
    StubAuctionComps,
    StubGuideValuation,
    StubPpsrChecker,
    StubRegoLookup,
    StubVinDecoder,
)
from acqbot.facts.store import fact_sheet
from acqbot.ingestion.service import ingest_lead
from acqbot.models import Escalation, Job, JobStatus, Lead, LeadState, MarketData
from acqbot.queue.worker import drain
from acqbot.simulator import make_lead


def _ingest_and_enrich(session, scenario, seed):
    lead_id = ingest_lead(session, make_lead(scenario, seed=seed)).lead_id
    session.commit()
    summary = enrich_lead(session, lead_id)
    session.commit()
    return session.get(Lead, lead_id), summary


def test_clean_lead_is_fully_enriched(session):
    lead, summary = _ingest_and_enrich(session, "clean", 50)
    assert lead.state == LeadState.NEW and lead.enriched_at is not None and lead.enrichment_errors is None
    sheet = fact_sheet(session, lead.lead_id)
    assert sheet.confirmed["finance_owing"]["owing"] is False
    assert sheet.confirmed["write_off_status"]["written_off"] is False
    assert sheet.confirmed["stolen"] is False
    assert sheet.confirmed["rego_status"]["status"] == "current"
    assert sheet.verified_from("finance_owing", ("ppsr",))
    assert "variant" in sheet.confirmed and "build_month" in sheet.confirmed
    kinds = {
        m.kind.value for m in session.scalars(select(MarketData).where(MarketData.lead_id == lead.lead_id))
    }
    assert kinds == {"guide", "comps"}
    assert summary["ppsr_pending"] is False


@pytest.mark.parametrize("scenario,trigger", [("written-off", "ppsr_written_off"), ("stolen", "ppsr_stolen")])
def test_ppsr_hard_gate_terminates_before_contact(session, scenario, trigger):
    lead, summary = _ingest_and_enrich(session, scenario, 51)
    assert lead.state == LeadState.TERMINATED
    assert summary["terminated"] == trigger
    # No market data is fetched for a dead lead.
    assert session.query(MarketData).filter_by(lead_id=lead.lead_id).count() == 0


def test_encumbered_vehicle_is_flagged_not_terminated(session):
    lead, _ = _ingest_and_enrich(session, "encumbered", 52)
    assert lead.state == LeadState.NEW
    fo = fact_sheet(session, lead.lead_id).confirmed["finance_owing"]
    assert fo["owing"] is True and fo["amount_aud"] > 0 and fo["secured_parties"]


def test_vin_decode_contradiction_is_recorded(session):
    lead, summary = _ingest_and_enrich(session, "contradiction", 53)
    sheet = fact_sheet(session, lead.lead_id)
    assert "year" in sheet.contradicted
    assert sheet.contradicted["year"]["actual"] == sheet.contradicted["year"]["claimed"] - 1
    assert "year" in summary["contradictions"]


def test_no_identifiers_defers_ppsr(session):
    lead, summary = _ingest_and_enrich(session, "no-identifiers", 54)
    sheet = fact_sheet(session, lead.lead_id)
    assert summary["ppsr_pending"] is True
    assert "finance_owing" not in sheet.confirmed and "vin" not in sheet.facts
    assert lead.state == LeadState.NEW and lead.enriched_at is not None


def test_rego_lookup_can_supply_the_vin(session):
    payload = make_lead("clean", seed=55)
    payload["vehicle_claimed"]["vin"] = None
    lead_id = ingest_lead(session, payload).lead_id
    session.commit()
    enrich_lead(session, lead_id)
    session.commit()
    sheet = fact_sheet(session, lead_id)
    assert sheet.facts["vin"].source.value == "rego" and sheet.facts["vin"].verified
    assert "finance_owing" in sheet.confirmed  # PPSR ran on the rego-derived VIN


def test_expired_rego_is_recorded(session):
    lead, _ = _ingest_and_enrich(session, "expired-rego", 56)
    assert fact_sheet(session, lead.lead_id).confirmed["rego_status"]["status"] == "expired"


def test_high_value_escalates_to_human(session):
    lead, summary = _ingest_and_enrich(session, "high-value", 57)
    assert lead.state == LeadState.HUMAN and summary["escalated"] == "high_value"
    esc = session.query(Escalation).filter_by(lead_id=lead.lead_id).one()
    assert esc.reason == "high_value" and esc.resolved_at is None


class _FlakyPpsr:
    name = "flaky"

    def __init__(self):
        self.calls = 0

    def check(self, vin):
        self.calls += 1
        raise ProviderError("PPSR timeout", retryable=True)


class _BrokenGuide:
    name = "broken"

    def lookup(self, *a, **k):
        raise ProviderError("guide licence expired", retryable=False)


def test_soft_gate_failure_is_recorded_and_lead_continues(session):
    providers = Providers(
        vin=StubVinDecoder(),
        ppsr=StubPpsrChecker(),
        rego=StubRegoLookup(),
        guide=_BrokenGuide(),
        comps=StubAuctionComps(),
    )
    lead_id = ingest_lead(session, make_lead("clean", seed=58)).lead_id
    session.commit()
    enrich_lead(session, lead_id, providers)
    session.commit()
    lead = session.get(Lead, lead_id)
    assert lead.enriched_at is not None
    assert lead.enrichment_errors == [{"step": "guide", "error": "guide licence expired", "retryable": False}]
    kinds = {m.kind.value for m in session.scalars(select(MarketData).where(MarketData.lead_id == lead_id))}
    assert kinds == {"comps"}


def test_ppsr_failure_retries_the_job(session, monkeypatch):
    flaky = _FlakyPpsr()
    providers = Providers(
        vin=StubVinDecoder(),
        ppsr=flaky,
        rego=StubRegoLookup(),
        guide=StubGuideValuation(),
        comps=StubAuctionComps(),
    )
    monkeypatch.setattr("acqbot.enrichment.pipeline.get_providers", lambda: providers)
    with session_scope() as s:
        lead_id = ingest_lead(s, make_lead("clean", seed=59)).lead_id
    assert drain("w") == 1
    with session_scope() as s:
        job = s.query(Job).filter_by(kind="enrich_lead").one()
        assert job.status == JobStatus.FAILED and job.attempts == 1 and "PPSR timeout" in job.last_error
        assert s.get(Lead, lead_id).enriched_at is None
    assert flaky.calls == 1
