"""Lead ingestion: validate → dedupe → persist → record claimed facts → enqueue enrichment.

No outbound contact happens here (Phase 1 of the build sequence). The upstream lead_id is our
primary key, so a re-delivered webhook is idempotent rather than a duplicate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acqbot.config import get_settings
from acqbot.contracts import LeadV1, parse_lead
from acqbot.conversation.transitions import transition
from acqbot.facts.fields import LISTING_CONFIDENCE
from acqbot.facts.store import record_fact
from acqbot.ingestion.fingerprint import odometer_matches, vehicle_fingerprint
from acqbot.models import FactSource, Lead, LeadDuplicate, LeadState, StateLog
from acqbot.queue.jobs import enqueue


@dataclass
class IngestResult:
    status: str  # accepted | duplicate | idempotent
    lead_id: uuid.UUID
    duplicate_of: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"status": self.status, "lead_id": str(self.lead_id)}
        if self.duplicate_of:
            d["duplicate_of"] = str(self.duplicate_of)
        return d


def find_duplicate(
    session: Session, fingerprint: str, seller_platform_id: str, odometer_km: int, window_days: int
) -> Lead | None:
    since = datetime.now(UTC) - timedelta(days=window_days)
    stmt = (
        select(Lead)
        .where(
            Lead.fingerprint == fingerprint,
            Lead.seller_platform_id == seller_platform_id,
            Lead.created_at >= since,
        )
        .order_by(Lead.created_at.desc())
    )
    for candidate in session.scalars(stmt):
        if odometer_matches(candidate.odometer_km, odometer_km):
            return candidate
    return None


def ingest_lead(session: Session, payload: dict[str, Any]) -> IngestResult:
    settings = get_settings()
    lead: LeadV1 = parse_lead(payload)  # raises LeadContractError

    existing = session.get(Lead, lead.lead_id)
    if existing is not None:
        return IngestResult(status="idempotent", lead_id=existing.lead_id)

    vc = lead.vehicle_claimed
    fp = vehicle_fingerprint(vc.make, vc.model, vc.year)
    dup = find_duplicate(session, fp, lead.seller.platform_id, vc.odometer_km, settings.dedupe_window_days)
    if dup is not None:
        session.add(LeadDuplicate(duplicate_of=dup.lead_id, upstream_payload=payload))
        session.flush()
        return IngestResult(status="duplicate", lead_id=lead.lead_id, duplicate_of=dup.lead_id)

    row = Lead(
        lead_id=lead.lead_id,
        source=lead.source,
        state=LeadState.NEW,
        schema_version=lead.schema_version,
        fingerprint=fp,
        seller_platform_id=lead.seller.platform_id,
        odometer_km=vc.odometer_km,
        listing_url=lead.listing_url,
        qualified_at=lead.qualified_at,
        upstream_payload=payload,
    )
    session.add(row)
    session.flush()
    session.add(
        StateLog(lead_id=row.lead_id, from_state=None, to_state=LeadState.NEW, trigger="lead_ingested")
    )

    # Everything under vehicle_claimed is a seller assertion taken from the listing.
    claimed: dict[str, Any] = {
        "make": vc.make,
        "model": vc.model,
        "variant": vc.variant,
        "year": vc.year,
        "odometer_km": vc.odometer_km,
        "rego": vc.rego,
        "vin": vc.vin,
        "transmission": vc.transmission,
        "fuel": vc.fuel,
        "asking_price_aud": vc.asking_price_aud,
        "listing_images": vc.images,
    }
    for key, value in claimed.items():
        if value is None or value == [] or value == "":
            continue
        record_fact(session, row.lead_id, key, value, source=FactSource.SELLER, confidence=LISTING_CONFIDENCE)

    enqueue(session, "enrich_lead", {"lead_id": str(row.lead_id)}, dedupe_key=f"enrich:{row.lead_id}")
    session.flush()
    return IngestResult(status="accepted", lead_id=row.lead_id)


def request_reenrichment(session: Session, lead: Lead, reason: str) -> None:
    """Re-run enrichment (e.g. rego learned in DISCOVERY). Idempotent while a job is active."""
    enqueue(
        session,
        "enrich_lead",
        {"lead_id": str(lead.lead_id), "reason": reason},
        dedupe_key=f"enrich:{lead.lead_id}",
    )


__all__ = ["IngestResult", "ingest_lead", "find_duplicate", "request_reenrichment", "transition"]
