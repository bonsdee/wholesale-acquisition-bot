"""Minimal human console (Section 11 suggests Retool; this covers the loop until then).

Escalation queue, offer approval, transcript review, manual facts, thread linking. Guarded by the
X-Admin-Token header; disabled entirely when ACQBOT_ADMIN_TOKEN is empty.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from acqbot.config import get_settings
from acqbot.conversation.handoff import transcript
from acqbot.conversation.service import Conversation
from acqbot.conversation.transitions import transition
from acqbot.db import session_scope
from acqbot.facts.store import fact_sheet, record_fact
from acqbot.models import (
    Escalation,
    FactSource,
    HandoffPacket,
    LadderStep,
    Lead,
    LeadState,
    Offer,
    OfferOutcome,
    Thread,
)
from acqbot.transport.registry import get_transport
from acqbot.valuation.service import compute_valuation, latest_valuation

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(status_code=404, detail="admin console disabled (ACQBOT_ADMIN_TOKEN unset)")
    if not x_admin_token or not hmac.compare_digest(token, x_admin_token):
        raise HTTPException(status_code=401, detail="bad admin token")


Admin = Depends(require_admin)


def _esc(e: Escalation) -> dict[str, Any]:
    return {
        "escalation_id": str(e.escalation_id),
        "lead_id": str(e.lead_id),
        "reason": e.reason,
        "details": e.details,
        "at": e.at.isoformat(),
        "resolved_by": e.resolved_by,
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
    }


@router.get("/escalations", dependencies=[Admin])
def open_escalations(include_resolved: bool = False) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(Escalation).order_by(Escalation.at)
        if not include_resolved:
            q = q.where(Escalation.resolved_at.is_(None))
        return [_esc(e) for e in s.scalars(q)]


class Resolve(BaseModel):
    by: str = Field(min_length=1, max_length=64)
    resolution: str = Field(min_length=1, max_length=4000)
    return_to_automation: bool = False  # for HUMAN leads: hand the thread back to the bot


@router.post("/escalations/{escalation_id}/resolve", dependencies=[Admin])
def resolve_escalation(escalation_id: uuid.UUID, body: Resolve) -> dict[str, Any]:
    with session_scope() as s:
        e = s.get(Escalation, escalation_id)
        if e is None:
            raise HTTPException(404, "escalation not found")
        if e.resolved_at is not None:
            raise HTTPException(409, "already resolved")
        e.resolved_by, e.resolution, e.resolved_at = body.by, body.resolution, datetime.now(UTC)
        lead = s.get(Lead, e.lead_id)
        if body.return_to_automation and lead is not None and lead.state == LeadState.HUMAN:
            still_open = (
                s.query(Escalation)
                .filter(Escalation.lead_id == lead.lead_id, Escalation.resolved_at.is_(None))
                .count()
            )
            if still_open == 0:
                transition(s, lead, LeadState.ENGAGED, f"returned_to_automation_by:{body.by}")
        s.flush()
        return _esc(e)


@router.get("/leads/{lead_id}/transcript", dependencies=[Admin])
def lead_transcript(lead_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        if lead is None:
            raise HTTPException(404, "lead not found")
        return {
            "lead_id": str(lead_id),
            "state": lead.state.value,
            "messages": transcript(s, lead_id),
            "facts": fact_sheet(s, lead_id).as_dict(),
        }


class PresentOffer(BaseModel):
    step: LadderStep = LadderStep.OPENING
    by: str = Field(min_length=1, max_length=64)
    amount_aud: float | None = None  # only with step=human: an above-ladder amount decided by a person


@router.post("/leads/{lead_id}/present-offer", dependencies=[Admin])
def present_offer(lead_id: uuid.UUID, body: PresentOffer) -> dict[str, Any]:
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        if lead is None:
            raise HTTPException(404, "lead not found")
        thread = s.scalars(
            select(Thread).where(Thread.lead_id == lead_id).order_by(Thread.created_at.desc())
        ).first()
        if thread is None:
            raise HTTPException(409, "lead has no thread")
        conv = Conversation(s, get_transport(thread.channel))
        if lead.state == LeadState.HUMAN:
            # A human presenting an offer is also handing the negotiation back to the automation.
            transition(s, lead, LeadState.PRICED, f"human_presenting_offer:{body.by}")
        try:
            if body.step == LadderStep.HUMAN:
                if body.amount_aud is None:
                    raise HTTPException(422, "amount_aud is required for step=human")
                offer = conv.record_human_offer(lead, body.amount_aud, presented_by=f"human:{body.by}")
            else:
                offer = conv.present_offer(lead, body.step, presented_by=f"human:{body.by}")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "offer_id": str(offer.offer_id),
            "amount": float(offer.amount),
            "step": offer.ladder_step.value,
            "expires_at": offer.expires_at.isoformat(),
            "state": lead.state.value,
        }


class OfferOutcomeBody(BaseModel):
    outcome: OfferOutcome
    by: str = Field(min_length=1, max_length=64)


@router.post("/leads/{lead_id}/offer-outcome", dependencies=[Admin])
def offer_outcome(lead_id: uuid.UUID, body: OfferOutcomeBody) -> dict[str, Any]:
    with session_scope() as s:
        offer = s.scalars(
            select(Offer).where(Offer.lead_id == lead_id).order_by(Offer.presented_at.desc())
        ).first()
        if offer is None:
            raise HTTPException(404, "no offer")
        if offer.outcome is not None:
            raise HTTPException(409, f"offer already {offer.outcome.value}")
        offer.outcome, offer.outcome_at = body.outcome, datetime.now(UTC)
        lead = s.get(Lead, lead_id)
        if body.outcome == OfferOutcome.ACCEPTED:
            transition(s, lead, LeadState.ACCEPTED, f"accepted_recorded_by:{body.by}")
        elif body.outcome == OfferOutcome.REJECTED:
            transition(s, lead, LeadState.REJECTED, f"rejected_recorded_by:{body.by}")
        return {"offer_id": str(offer.offer_id), "outcome": offer.outcome.value, "state": lead.state.value}


class ManualFact(BaseModel):
    field: str = Field(min_length=1, max_length=64)
    value: Any
    source: FactSource = FactSource.INSPECTION
    verified: bool = True
    by: str = Field(min_length=1, max_length=64)


@router.post("/leads/{lead_id}/facts", dependencies=[Admin])
def add_fact(lead_id: uuid.UUID, body: ManualFact) -> dict[str, Any]:
    with session_scope() as s:
        if s.get(Lead, lead_id) is None:
            raise HTTPException(404, "lead not found")
        rr = record_fact(s, lead_id, body.field, body.value, source=body.source, verified=body.verified)
        from acqbot.queue.jobs import enqueue

        lead = s.get(Lead, lead_id)
        if rr.created and lead.state in {LeadState.VERIFICATION, LeadState.PRICED, LeadState.DISCOVERY}:
            enqueue(
                s, "value_lead", {"lead_id": str(lead_id), "then": "on_priced"}, dedupe_key=f"value:{lead_id}"
            )
            for e in s.query(Escalation).filter(
                Escalation.lead_id == lead_id,
                Escalation.reason == "verification_review",
                Escalation.resolved_at.is_(None),
            ):
                e.resolved_by, e.resolution, e.resolved_at = (
                    body.by,
                    f"recorded {body.field} from {body.source.value}",
                    datetime.now(UTC),
                )
        return {
            "fact_id": str(rr.fact.fact_id),
            "created": rr.created,
            "contradiction": rr.contradiction.__dict__ if rr.contradiction else None,
            "facts": fact_sheet(s, lead_id).as_dict(),
        }


@router.post("/leads/{lead_id}/value", dependencies=[Admin])
def revalue(lead_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as s:
        if s.get(Lead, lead_id) is None:
            raise HTTPException(404, "lead not found")
        out = compute_valuation(s, lead_id)
        return {
            "basis": out.basis.value,
            "wholesale_max": out.result.wholesale_max,
            "band": [out.result.band_low, out.result.band_high],
            "ladder": out.result.ladder,
            "warnings": out.result.warnings,
        }


@router.get("/handoffs", dependencies=[Admin])
def handoffs(include_claimed: bool = False) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(HandoffPacket).order_by(HandoffPacket.created_at)
        if not include_claimed:
            q = q.where(HandoffPacket.claimed_at.is_(None))
        return [
            {
                "packet_id": str(p.packet_id),
                "lead_id": str(p.lead_id),
                "created_at": p.created_at.isoformat(),
                "sla_expires_at": p.sla_expires_at.isoformat(),
                "claimed_by": p.claimed_by,
                "packet": p.packet,
            }
            for p in s.scalars(q)
        ]


class Claim(BaseModel):
    by: str = Field(min_length=1, max_length=64)


@router.post("/handoffs/{packet_id}/claim", dependencies=[Admin])
def claim_handoff(packet_id: uuid.UUID, body: Claim) -> dict[str, Any]:
    with session_scope() as s:
        p = s.get(HandoffPacket, packet_id)
        if p is None:
            raise HTTPException(404, "packet not found")
        if p.claimed_at is not None:
            raise HTTPException(409, f"already claimed by {p.claimed_by}")
        p.claimed_by, p.claimed_at = body.by, datetime.now(UTC)
        for e in s.query(Escalation).filter(
            Escalation.lead_id == p.lead_id, Escalation.reason == "handoff", Escalation.resolved_at.is_(None)
        ):
            e.resolved_by, e.resolution, e.resolved_at = body.by, "claimed", datetime.now(UTC)
        return {"packet_id": str(p.packet_id), "claimed_by": p.claimed_by}


@router.get("/threads/unlinked", dependencies=[Admin])
def unlinked_threads() -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.scalars(select(Thread).where(Thread.lead_id.is_(None)).order_by(Thread.created_at))
        return [
            {
                "thread_id": str(t.thread_id),
                "channel": t.channel.value,
                "external_id": t.external_id,
                "referral_ref": t.referral_ref,
                "created_at": t.created_at.isoformat(),
            }
            for t in rows
        ]


class LinkThread(BaseModel):
    lead_id: uuid.UUID


@router.post("/threads/{thread_id}/link", dependencies=[Admin])
def link_thread(thread_id: uuid.UUID, body: LinkThread) -> dict[str, Any]:
    from acqbot.queue.jobs import enqueue

    with session_scope() as s:
        t = s.get(Thread, thread_id)
        if t is None or s.get(Lead, body.lead_id) is None:
            raise HTTPException(404, "thread or lead not found")
        if t.lead_id is not None:
            raise HTTPException(409, "thread already linked")
        t.lead_id = body.lead_id
        enqueue(s, "maybe_send_opening", {"lead_id": str(body.lead_id)}, dedupe_key=f"opening:{body.lead_id}")
        return {"thread_id": str(t.thread_id), "lead_id": str(t.lead_id)}


@router.get("/leads/{lead_id}/valuation", dependencies=[Admin])
def lead_valuation(lead_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as s:
        v = latest_valuation(s, lead_id)
        if v is None:
            raise HTTPException(404, "no valuation")
        return {
            "valuation_id": str(v.valuation_id),
            "basis": v.basis.value,
            "band": [float(v.band_low), float(v.band_high)],
            "wholesale_max": float(v.wholesale_max),
            "ladder": v.ladder,
            "recon_lines": v.recon_lines,
            "inputs_snapshot": v.inputs_snapshot,
            "computed_at": v.computed_at.isoformat(),
        }
