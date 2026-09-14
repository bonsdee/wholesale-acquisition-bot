"""Conversation state machine — Section 5.

Deal stage is computed from collected facts by deterministic code. The conversation layer (and
later the model) is told the stage and what remains outstanding; it never chooses either.

Two kinds of state:
  computed  NEW, CONTACTED, DISCOVERY, VERIFICATION, PRICED, OFFER_MADE, NEGOTIATING — derived
            here from facts, messages, valuations and offers every time a message arrives.
  sticky    ACCEPTED, HANDOFF, REJECTED, ARCHIVED, STALLED, ESCALATED, HUMAN, TERMINATED —
            set by events (acceptance, escalation, PPSR gate, nudge exhaustion); never recomputed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from acqbot.facts.fields import DISCOVERY_REQUIRED, FieldSpec
from acqbot.facts.store import FactSheet
from acqbot.models import LeadState

STICKY_STATES = frozenset(
    {
        LeadState.ACCEPTED,
        LeadState.HANDOFF,
        LeadState.REJECTED,
        LeadState.ARCHIVED,
        LeadState.STALLED,
        LeadState.ESCALATED,
        LeadState.HUMAN,
        LeadState.TERMINATED,
    }
)

# Stages at or beyond which a dollar figure may appear in an outbound message.
PRICE_VISIBLE_STATES = frozenset(
    {LeadState.PRICED, LeadState.OFFER_MADE, LeadState.NEGOTIATING, LeadState.ACCEPTED}
)


@dataclass
class Signals:
    """Everything compute_stage needs that is not in the fact sheet."""

    outbound_count: int = 0
    inbound_after_first_outbound: int = 0
    has_verified_valuation: bool = False
    offers_presented: int = 0
    inbound_after_last_offer: int = 0
    acknowledged_contradictions: set[str] = field(default_factory=set)
    min_photos: int = 6


@dataclass
class StageView:
    stage: LeadState
    outstanding: list[str] = field(default_factory=list)  # DISCOVERY fields still needed
    verification_outstanding: list[str] = field(default_factory=list)
    pending_contradictions: list[str] = field(default_factory=list)
    next_field: FieldSpec | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "outstanding": self.outstanding,
            "verification_outstanding": self.verification_outstanding,
            "pending_contradictions": self.pending_contradictions,
            "next_field": self.next_field.key if self.next_field else None,
            "note": self.note,
        }


def field_satisfied(spec: FieldSpec, sheet: FactSheet, *, min_photos: int) -> bool:
    fv = sheet.facts.get(spec.key)
    if fv is None:
        return False
    if spec.key == "photos":
        return isinstance(fv.value, list) and len(fv.value) >= min_photos
    if spec.key == "rego":
        # A VIN satisfies the identifier requirement just as well as a plate.
        return True
    return fv.verified or fv.confidence >= spec.min_confidence


def discovery_outstanding(sheet: FactSheet, *, min_photos: int) -> list[FieldSpec]:
    out: list[FieldSpec] = []
    for spec in DISCOVERY_REQUIRED:
        if spec.key == "rego" and (sheet.facts.get("vin") or sheet.facts.get("rego")):
            continue
        # PPSR/rego-derived facts satisfy their own DISCOVERY requirement when verified; photos still need the count.
        if (
            spec.key != "photos"
            and spec.verified_sources
            and sheet.verified_from(spec.key, spec.verified_sources)
        ):
            continue
        if not field_satisfied(spec, sheet, min_photos=min_photos):
            out.append(spec)
    return out


def verification_outstanding(sheet: FactSheet, *, min_photos: int) -> list[FieldSpec]:
    out: list[FieldSpec] = []
    for spec in DISCOVERY_REQUIRED:
        if not spec.verified_sources:
            continue
        if spec.key == "photos":
            fv = sheet.facts.get("photos")
            if fv is None or not fv.verified or len(fv.value or []) < min_photos:
                out.append(spec)
            continue
        if not sheet.verified_from(spec.key, spec.verified_sources):
            out.append(spec)
    return out


def compute_stage(current: LeadState, sheet: FactSheet, sig: Signals) -> StageView:
    if current in STICKY_STATES:
        return StageView(stage=current, note="sticky")

    if sig.outbound_count == 0:
        return StageView(
            stage=LeadState.NEW,
            outstanding=[s.key for s in discovery_outstanding(sheet, min_photos=sig.min_photos)],
        )

    if sig.inbound_after_first_outbound == 0:
        return StageView(
            stage=LeadState.CONTACTED,
            outstanding=[s.key for s in discovery_outstanding(sheet, min_photos=sig.min_photos)],
        )

    pending_contra = [f for f in sheet.contradicted if f not in sig.acknowledged_contradictions]
    outstanding = discovery_outstanding(sheet, min_photos=sig.min_photos)

    if pending_contra:
        return StageView(
            stage=LeadState.DISCOVERY,
            outstanding=[s.key for s in outstanding],
            pending_contradictions=pending_contra,
            note="re-entered DISCOVERY: verified data contradicts a claim",
        )
    if outstanding:
        return StageView(
            stage=LeadState.DISCOVERY, outstanding=[s.key for s in outstanding], next_field=outstanding[0]
        )

    v_out = verification_outstanding(sheet, min_photos=sig.min_photos)
    if v_out or not sig.has_verified_valuation:
        return StageView(
            stage=LeadState.VERIFICATION,
            verification_outstanding=[s.key for s in v_out],
            note="awaiting checks" if v_out else "awaiting valuation",
        )

    if sig.offers_presented == 0:
        return StageView(stage=LeadState.PRICED, note="valuation released; offer not yet presented")
    if sig.inbound_after_last_offer == 0:
        return StageView(stage=LeadState.OFFER_MADE)
    return StageView(stage=LeadState.NEGOTIATING)
