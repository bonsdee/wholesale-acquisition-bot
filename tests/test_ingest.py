import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from acqbot.facts.store import fact_sheet
from acqbot.ingestion.service import ingest_lead
from acqbot.models import Job, JobStatus, Lead, LeadDuplicate, LeadState, StateLog
from acqbot.simulator import make_lead


def test_accepted_lead_records_claims_and_enqueues(session):
    payload = make_lead("clean", seed=10)
    result = ingest_lead(session, payload)
    session.commit()

    assert result.status == "accepted"
    lead = session.get(Lead, result.lead_id)
    assert lead.state == LeadState.NEW
    assert lead.fingerprint and lead.odometer_km == payload["vehicle_claimed"]["odometer_km"]

    sheet = fact_sheet(session, lead.lead_id)
    assert sheet.claimed["make"] == payload["vehicle_claimed"]["make"]
    assert sheet.claimed["odometer_km"] == payload["vehicle_claimed"]["odometer_km"]
    assert not sheet.confirmed  # nothing is verified at ingestion
    assert all(not fv.verified and fv.confidence == 0.6 for fv in sheet.facts.values())

    log = session.scalars(select(StateLog).where(StateLog.lead_id == lead.lead_id)).all()
    assert [(h.from_state, h.to_state, h.trigger) for h in log] == [(None, LeadState.NEW, "lead_ingested")]

    jobs = session.scalars(select(Job).where(Job.kind == "enrich_lead")).all()
    assert len(jobs) == 1 and jobs[0].status == JobStatus.QUEUED
    assert jobs[0].payload == {"lead_id": str(lead.lead_id)}


def test_redelivery_of_same_lead_id_is_idempotent(session):
    payload = make_lead("clean", seed=11)
    first = ingest_lead(session, payload)
    session.commit()
    second = ingest_lead(session, payload)
    session.commit()
    assert second.status == "idempotent" and second.lead_id == first.lead_id
    assert session.query(Lead).count() == 1
    assert session.query(Job).count() == 1


def test_relisting_by_same_seller_is_a_duplicate(session):
    payload = make_lead("clean", seed=12)
    first = ingest_lead(session, payload)
    session.commit()

    relist = make_lead("clean", seed=12)  # same seller + vehicle, new lead id
    relist["lead_id"] = str(uuid.uuid4())
    relist["vehicle_claimed"]["odometer_km"] += 1_500
    relist["vehicle_claimed"]["model"] = relist["vehicle_claimed"]["model"].upper() + " "
    result = ingest_lead(session, relist)
    session.commit()

    assert result.status == "duplicate" and result.duplicate_of == first.lead_id
    assert session.query(Lead).count() == 1
    dups = session.query(LeadDuplicate).all()
    assert len(dups) == 1 and dups[0].duplicate_of == first.lead_id


def test_same_seller_different_vehicle_is_not_a_duplicate(session):
    payload = make_lead("clean", seed=13)
    ingest_lead(session, payload)
    session.commit()
    other = make_lead("clean", seed=13)
    other["lead_id"] = str(uuid.uuid4())
    other["vehicle_claimed"]["year"] -= 1
    assert ingest_lead(session, other).status == "accepted"
    session.commit()
    assert session.query(Lead).count() == 2


def test_far_apart_odometer_is_not_a_duplicate(session):
    payload = make_lead("clean", seed=14)
    ingest_lead(session, payload)
    session.commit()
    other = make_lead("clean", seed=14)
    other["lead_id"] = str(uuid.uuid4())
    other["vehicle_claimed"]["odometer_km"] = payload["vehicle_claimed"]["odometer_km"] * 2 + 50_000
    assert ingest_lead(session, other).status == "accepted"


def test_dedupe_window_expires(session):
    payload = make_lead("clean", seed=15)
    first = ingest_lead(session, payload)
    session.commit()
    # Backdate the original past the 90-day window.
    old = datetime.now(UTC) - timedelta(days=91)
    session.execute(
        text("UPDATE leads SET created_at = :old WHERE lead_id = :id"), {"old": old, "id": first.lead_id}
    )
    session.commit()

    relist = make_lead("clean", seed=15)
    relist["lead_id"] = str(uuid.uuid4())
    assert ingest_lead(session, relist).status == "accepted"
