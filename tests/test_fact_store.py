from acqbot.facts.store import fact_history, fact_sheet, record_fact
from acqbot.ingestion.service import ingest_lead
from acqbot.models import FactSource
from acqbot.simulator import make_lead


def _lead(session, seed=30):
    payload = make_lead("clean", seed=seed)
    lead_id = ingest_lead(session, payload).lead_id
    session.commit()
    return lead_id, payload


def test_verified_fact_supersedes_claim(session):
    lead_id, payload = _lead(session)
    claimed_km = payload["vehicle_claimed"]["odometer_km"]

    r = record_fact(
        session, lead_id, "odometer_km", claimed_km + 30_000, source=FactSource.PHOTO, verified=True
    )
    session.commit()

    assert r.created and r.superseded is not None
    assert r.contradiction is not None and r.contradiction.claimed == claimed_km
    sheet = fact_sheet(session, lead_id)
    assert sheet.confirmed["odometer_km"] == claimed_km + 30_000
    assert "odometer_km" not in sheet.claimed
    assert sheet.contradicted["odometer_km"]["claimed"] == claimed_km
    assert sheet.contradicted["odometer_km"]["source"] == "photo"

    history = fact_history(session, lead_id, "odometer_km")
    assert len(history) == 2 and history[0].superseded_by == history[1].fact_id


def test_small_difference_is_not_a_contradiction(session):
    lead_id, payload = _lead(session, seed=31)
    claimed_km = payload["vehicle_claimed"]["odometer_km"]
    r = record_fact(session, lead_id, "odometer_km", claimed_km + 500, source=FactSource.PHOTO, verified=True)
    assert r.contradiction is None
    assert fact_sheet(session, lead_id).contradicted == {}


def test_weaker_fact_after_verified_does_not_become_current(session):
    lead_id, _ = _lead(session, seed=32)
    record_fact(session, lead_id, "keys_count", 2, source=FactSource.INSPECTION, verified=True)
    r = record_fact(session, lead_id, "keys_count", 1, source=FactSource.SELLER, confidence=0.9)
    session.commit()
    assert r.created and r.fact.superseded_by is not None
    assert fact_sheet(session, lead_id).confirmed["keys_count"] == 2


def test_identical_restatement_is_a_noop(session):
    lead_id, payload = _lead(session, seed=33)
    make = payload["vehicle_claimed"]["make"]
    r = record_fact(session, lead_id, "make", make.lower(), source=FactSource.SELLER, confidence=0.6)
    assert not r.created
    assert len(fact_history(session, lead_id, "make")) == 1


def test_seller_confirmation_raises_confidence(session):
    lead_id, payload = _lead(session, seed=34)
    km = payload["vehicle_claimed"]["odometer_km"]
    r = record_fact(session, lead_id, "odometer_km", km, source=FactSource.SELLER, confidence=0.9)
    assert r.created and r.superseded is not None and r.contradiction is None
    sheet = fact_sheet(session, lead_id)
    assert sheet.facts["odometer_km"].confidence == 0.9
    assert sheet.has("odometer_km", min_confidence=0.9)
