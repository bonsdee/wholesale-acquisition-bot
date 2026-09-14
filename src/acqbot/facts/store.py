"""Append-only fact store.

A fact is (lead, field, value, source, verified, confidence). Nothing is ever overwritten: a newer
fact for the same field supersedes the older one by setting the older row's `superseded_by`
(the only column the database allows to change). A seller's claim that a verified source later
contradicts stays in the chain — that revision is signal the closer needs (Section 10).

Source authority decides who supersedes whom. A lower-authority fact arriving after a verified one
is recorded but immediately marked superseded by the standing verified fact, so it never becomes
current.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acqbot.models import FactSource, VehicleFact

AUTHORITY: dict[FactSource, int] = {
    FactSource.SELLER: 0,
    FactSource.PHOTO: 1,
    FactSource.VIN: 2,
    FactSource.REGO: 2,
    FactSource.PPSR: 3,
    FactSource.INSPECTION: 4,
}


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def values_equal(a: Any, b: Any) -> bool:
    return _norm(a) == _norm(b)


def materially_different(field_key: str, old: Any, new: Any) -> bool:
    """Whether a superseding value contradicts the old one enough to matter for price."""
    if field_key == "odometer_km":
        try:
            o, n = int(old), int(new)
        except (TypeError, ValueError):
            return not values_equal(old, new)
        return abs(o - n) > max(2_000, 0.05 * max(o, n))
    if field_key in {"finance_owing", "write_off_status"} and isinstance(old, dict) and isinstance(new, dict):
        key = "owing" if field_key == "finance_owing" else "written_off"
        return bool(old.get(key)) != bool(new.get(key))
    return not values_equal(old, new)


@dataclass
class FactView:
    fact_id: uuid.UUID
    field: str
    value: Any
    source: FactSource
    verified: bool
    confidence: float
    recorded_at: datetime


@dataclass
class Contradiction:
    field: str
    claimed: Any
    actual: Any
    actual_source: FactSource
    claimed_fact_id: uuid.UUID
    actual_fact_id: uuid.UUID


@dataclass
class FactSheet:
    """What the conversation layer (and later the model) sees. Small and structured."""

    confirmed: dict[str, Any] = field(default_factory=dict)
    claimed: dict[str, Any] = field(default_factory=dict)
    contradicted: dict[str, dict[str, Any]] = field(default_factory=dict)
    facts: dict[str, FactView] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        fv = self.facts.get(key)
        return fv.value if fv else default

    def has(self, key: str, *, min_confidence: float = 0.0) -> bool:
        fv = self.facts.get(key)
        return fv is not None and (fv.verified or fv.confidence >= min_confidence)

    def verified_from(self, key: str, sources: tuple[str, ...]) -> bool:
        fv = self.facts.get(key)
        return fv is not None and fv.verified and fv.source.value in sources

    def as_dict(self) -> dict[str, Any]:
        return {"confirmed": self.confirmed, "claimed": self.claimed, "contradicted": self.contradicted}


@dataclass
class RecordResult:
    fact: VehicleFact
    created: bool
    superseded: VehicleFact | None
    contradiction: Contradiction | None


def _all_facts(session: Session, lead_id: uuid.UUID) -> list[VehicleFact]:
    stmt = (
        select(VehicleFact)
        .where(VehicleFact.lead_id == lead_id)
        .order_by(VehicleFact.recorded_at, VehicleFact.fact_id)
    )
    return list(session.scalars(stmt))


def current_fact(session: Session, lead_id: uuid.UUID, field_key: str) -> VehicleFact | None:
    stmt = (
        select(VehicleFact)
        .where(
            VehicleFact.lead_id == lead_id,
            VehicleFact.field == field_key,
            VehicleFact.superseded_by.is_(None),
        )
        .order_by(VehicleFact.recorded_at.desc())
    )
    rows = list(session.scalars(stmt))
    if not rows:
        return None
    # Defensive: if more than one un-superseded row exists, the most authoritative/confident wins.
    rows.sort(key=lambda f: (AUTHORITY[f.source], f.verified, f.confidence, f.recorded_at), reverse=True)
    return rows[0]


def record_fact(
    session: Session,
    lead_id: uuid.UUID,
    field_key: str,
    value: Any,
    *,
    source: FactSource,
    verified: bool = False,
    confidence: float = 1.0,
) -> RecordResult:
    """Append a fact. Returns what happened so callers can react to contradictions."""
    if verified:
        confidence = 1.0
    cur = current_fact(session, lead_id, field_key)

    if cur is None:
        fact = VehicleFact(
            lead_id=lead_id,
            field=field_key,
            value=value,
            source=source,
            verified=verified,
            confidence=confidence,
        )
        session.add(fact)
        session.flush()
        return RecordResult(fact=fact, created=True, superseded=None, contradiction=None)

    same_value = values_equal(cur.value, value)
    new_rank = (AUTHORITY[source], verified, confidence)
    cur_rank = (AUTHORITY[cur.source], cur.verified, cur.confidence)

    # Nothing new to say: identical value from a source that is no more authoritative.
    if same_value and new_rank <= cur_rank:
        return RecordResult(fact=cur, created=False, superseded=None, contradiction=None)

    fact = VehicleFact(
        lead_id=lead_id, field=field_key, value=value, source=source, verified=verified, confidence=confidence
    )

    if new_rank < cur_rank and not same_value:
        # A weaker fact after a stronger one: keep it in the chain, but the stronger fact stays current.
        fact.superseded_by = cur.fact_id
        session.add(fact)
        session.flush()
        return RecordResult(fact=fact, created=True, superseded=None, contradiction=None)

    session.add(fact)
    session.flush()
    cur.superseded_by = fact.fact_id
    session.flush()

    contradiction = None
    if not same_value and verified and not cur.verified and materially_different(field_key, cur.value, value):
        contradiction = Contradiction(
            field=field_key,
            claimed=cur.value,
            actual=value,
            actual_source=source,
            claimed_fact_id=cur.fact_id,
            actual_fact_id=fact.fact_id,
        )
    return RecordResult(fact=fact, created=True, superseded=cur, contradiction=contradiction)


def fact_sheet(session: Session, lead_id: uuid.UUID) -> FactSheet:
    facts = _all_facts(session, lead_id)
    by_id = {f.fact_id: f for f in facts}
    sheet = FactSheet()

    # Current facts: not superseded. One per field after the defensive sort.
    current: dict[str, VehicleFact] = {}
    for f in facts:
        if f.superseded_by is not None:
            continue
        prev = current.get(f.field)
        if prev is None or (AUTHORITY[f.source], f.verified, f.confidence, f.recorded_at) > (
            AUTHORITY[prev.source],
            prev.verified,
            prev.confidence,
            prev.recorded_at,
        ):
            current[f.field] = f

    for key, f in current.items():
        sheet.facts[key] = FactView(
            fact_id=f.fact_id,
            field=key,
            value=f.value,
            source=f.source,
            verified=f.verified,
            confidence=f.confidence,
            recorded_at=f.recorded_at,
        )
        (sheet.confirmed if f.verified else sheet.claimed)[key] = f.value

    # Contradictions: an unverified seller claim superseded by a verified, materially different fact.
    for f in facts:
        if f.superseded_by is None or f.verified or f.source != FactSource.SELLER:
            continue
        sup = by_id.get(f.superseded_by)
        if sup is None or not sup.verified:
            continue
        if materially_different(f.field, f.value, sup.value):
            sheet.contradicted[f.field] = {
                "claimed": f.value,
                "actual": sup.value,
                "source": sup.source.value,
                "claimed_at": f.recorded_at.isoformat(),
            }
            sheet.claimed.pop(f.field, None)
    return sheet


def fact_history(session: Session, lead_id: uuid.UUID, field_key: str) -> list[VehicleFact]:
    return [f for f in _all_facts(session, lead_id) if f.field == field_key]
