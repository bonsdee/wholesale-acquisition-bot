"""Conversation orchestrator — Sections 5, 7 and 8, without a language model (Phase 3).

Per inbound message (Figure 4, deterministic parts):
    [1] persist to the append-only thread store
    [2] extract structured facts and screens (rule-based here; a small model in Phase 4)
    [3] recompute the state machine position from the fact store
    [4]–[5] pick the next scripted message for the stage
    [6] validation gate — even templates go through it
    [7] send via the transport and log the outbound with its template version

Escalation triggers (5.2) route to a human with the pending response discarded. The one exception
is the direct "are you a bot?" question, which gets the plain answer Section 8 requires and is then
handed over.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from acqbot.config import Settings, get_settings
from acqbot.conversation import templates as T
from acqbot.conversation.extract import Extraction, extract
from acqbot.conversation.gate import GateContext, validate
from acqbot.conversation.handoff import write_packet
from acqbot.conversation.state import PRICE_VISIBLE_STATES, Signals, StageView, compute_stage
from acqbot.conversation.transitions import create_task, escalate, transition
from acqbot.enrichment import catalog
from acqbot.facts.fields import STATED_CONFIDENCE, spec_for
from acqbot.facts.store import FactSheet, fact_sheet, record_fact
from acqbot.ingestion.service import request_reenrichment
from acqbot.models import (
    Direction,
    Escalation,
    FactSource,
    LadderStep,
    Lead,
    LeadState,
    Message,
    Offer,
    OfferOutcome,
    Thread,
    Valuation,
    ValuationBasis,
)
from acqbot.queue.jobs import enqueue
from acqbot.transport.protocol import InboundMessage, ThreadInfo, Transport, TransportError
from acqbot.valuation.service import latest_valuation

log = logging.getLogger("acqbot.conversation")

HARD_ESCALATION_FLAGS = ("legal", "deceased_estate", "distress", "minor_or_no_authority", "hostile")
SILENT_STATES = {
    LeadState.TERMINATED,
    LeadState.ARCHIVED,
    LeadState.REJECTED,
    LeadState.HANDOFF,
    LeadState.ACCEPTED,
}
LADDER_ORDER = [
    LadderStep.OPENING,
    LadderStep.STEP_1,
    LadderStep.STEP_2,
]  # floor is not reachable by automation


@dataclass
class HandleResult:
    action: str
    lead_id: uuid.UUID | None = None
    stage: str | None = None
    sent: str | None = None
    facts_recorded: list[str] = field(default_factory=list)
    escalated: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def agent_for(lead: Lead, settings: Settings) -> str:
    names = settings.agent_names or ["Alex"]
    idx = int(hashlib.sha256(str(lead.lead_id).encode()).hexdigest(), 16) % len(names)
    return names[idx]


class Conversation:
    def __init__(self, session: Session, transport: Transport, settings: Settings | None = None) -> None:
        self.s = session
        self.t = transport
        self.cfg = settings or get_settings()
        self._last_inbound_text: str | None = None

    # ------------------------------------------------------------------ threads

    def get_or_create_thread(self, msg: InboundMessage) -> Thread:
        thread = self.s.scalars(
            select(Thread).where(Thread.channel == msg.channel, Thread.external_id == msg.external_id)
        ).first()
        if thread is None:
            thread = Thread(channel=msg.channel, external_id=msg.external_id, referral_ref=msg.referral_ref)
            self.s.add(thread)
            self.s.flush()
        elif msg.referral_ref and not thread.referral_ref:
            thread.referral_ref = msg.referral_ref
        return thread

    def link_thread(self, thread: Thread, msg: InboundMessage) -> Lead | None:
        """Link by m.me referral (lead_id) first, then by the seller's platform id."""
        if thread.lead_id:
            return self.s.get(Lead, thread.lead_id)
        lead: Lead | None = None
        ref = msg.referral_ref or thread.referral_ref
        if ref:
            try:
                lead = self.s.get(Lead, uuid.UUID(ref))
            except ValueError:
                lead = None
        if lead is None:
            lead = self.s.scalars(
                select(Lead)
                .where(Lead.seller_platform_id == msg.external_id)
                .order_by(Lead.created_at.desc())
            ).first()
        if lead is not None:
            thread.lead_id = lead.lead_id
            self.s.flush()
            enqueue(
                self.s,
                "maybe_send_opening",
                {"lead_id": str(lead.lead_id)},
                dedupe_key=f"opening:{lead.lead_id}",
            )
        return lead

    def thread_for_lead(self, lead: Lead) -> Thread | None:
        return self.s.scalars(
            select(Thread)
            .where(Thread.lead_id == lead.lead_id, Thread.channel == self.t.channel)
            .order_by(Thread.created_at.desc())
        ).first()

    # ------------------------------------------------------------------ signals

    def _signals(self, lead: Lead, sheet: FactSheet) -> Signals:
        first_out = self.s.scalar(
            select(func.min(Message.sent_at)).where(
                Message.lead_id == lead.lead_id, Message.direction == Direction.OUTBOUND
            )
        )
        out_count = (
            self.s.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.lead_id == lead.lead_id, Message.direction == Direction.OUTBOUND)
            )
            or 0
        )
        in_after = 0
        if first_out:
            in_after = (
                self.s.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(
                        Message.lead_id == lead.lead_id,
                        Message.direction == Direction.INBOUND,
                        Message.sent_at > first_out,
                    )
                )
                or 0
            )
        offers = list(
            self.s.scalars(select(Offer).where(Offer.lead_id == lead.lead_id).order_by(Offer.presented_at))
        )
        in_after_offer = 0
        if offers:
            in_after_offer = (
                self.s.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(
                        Message.lead_id == lead.lead_id,
                        Message.direction == Direction.INBOUND,
                        Message.sent_at > offers[-1].presented_at,
                    )
                )
                or 0
            )
        acked = set(sheet.get("contradictions_acknowledged") or [])
        return Signals(
            outbound_count=out_count,
            inbound_after_first_outbound=in_after,
            has_verified_valuation=latest_valuation(self.s, lead.lead_id, basis=ValuationBasis.VERIFIED)
            is not None,
            offers_presented=len(offers),
            inbound_after_last_offer=in_after_offer,
            acknowledged_contradictions=acked,
            min_photos=self.cfg.min_photos,
        )

    def _last_outbound(self, lead: Lead) -> Message | None:
        return self.s.scalars(
            select(Message)
            .where(Message.lead_id == lead.lead_id, Message.direction == Direction.OUTBOUND)
            .order_by(Message.sent_at.desc(), Message.msg_id.desc())
        ).first()

    def _pending_field(self, lead: Lead) -> str | None:
        last = self._last_outbound(lead)
        if last and last.validation_notes:
            return last.validation_notes.get("asked_field")
        return None

    def _turns_without_progress(self, lead: Lead) -> int:
        """Consecutive inbound messages since the last recorded fact, state change or offer."""
        from acqbot.models import StateLog, VehicleFact

        last_fact = self.s.scalar(
            select(func.max(VehicleFact.recorded_at)).where(VehicleFact.lead_id == lead.lead_id)
        )
        last_state = self.s.scalar(select(func.max(StateLog.at)).where(StateLog.lead_id == lead.lead_id))
        last_offer = self.s.scalar(select(func.max(Offer.presented_at)).where(Offer.lead_id == lead.lead_id))
        marks = [m for m in (last_fact, last_state, last_offer) if m is not None]
        since = max(marks) if marks else None
        q = (
            select(func.count())
            .select_from(Message)
            .where(Message.lead_id == lead.lead_id, Message.direction == Direction.INBOUND)
        )
        if since:
            q = q.where(Message.sent_at > since)
        return self.s.scalar(q) or 0

    def current_offer(self, lead: Lead) -> Offer | None:
        return self.s.scalars(
            select(Offer).where(Offer.lead_id == lead.lead_id).order_by(Offer.presented_at.desc())
        ).first()

    def identity(self, lead: Lead) -> T.Identity:
        return T.Identity(
            dealership=self.cfg.dealership_name,
            lmct=self.cfg.dealership_lmct,
            agent=agent_for(lead, self.cfg),
        )

    # ------------------------------------------------------------------ outbound

    def _send(
        self,
        lead: Lead,
        thread: Thread,
        body: str,
        template_id: str,
        *,
        asked_field: str | None = None,
        stage: LeadState | None = None,
        ladder: dict[str, float] | None = None,
        current_offer: float | None = None,
        allowed_years: set[int] | None = None,
        **vars: Any,
    ) -> Message | None:
        sheet = fact_sheet(self.s, lead.lead_id)
        ctx = GateContext(
            stage=stage or lead.state,
            ladder=ladder,
            current_offer=current_offer,
            vehicle_year=int(sheet.get("year")) if sheet.get("year") else None,
            allowed_years=allowed_years or set(),
            max_length=self.t.max_body_length,
        )
        gate = validate(body, ctx)
        if not gate.ok:
            log.error("gate rejected template %s for lead %s: %s", template_id, lead.lead_id, gate.violations)
            escalate(
                self.s, lead, "template_failed_gate", {"template": template_id, "violations": gate.violations}
            )
            return None
        info = ThreadInfo(external_id=thread.external_id, last_inbound_at=thread.last_inbound_at)
        if not self.t.send_window_open(info):
            create_task(
                self.s, lead, "send_window_closed", {"template": template_id, "channel": thread.channel.value}
            )
            return None
        try:
            receipt = self.t.send(info, body)
        except TransportError as exc:
            if exc.retryable:
                raise
            escalate(self.s, lead, "transport_rejected", {"error": str(exc)})
            return None
        msg = Message(
            thread_id=thread.thread_id,
            lead_id=lead.lead_id,
            direction=Direction.OUTBOUND,
            body=body,
            sent_at=datetime.now(UTC),
            external_msg_id=receipt.external_msg_id,
            model_version=T.TEMPLATE_VERSION,
            prompt_hash=T.template_hash(template_id, **vars),
            validated=True,
            validation_notes={"template": template_id, "asked_field": asked_field, "gate": gate.as_dict()},
        )
        self.s.add(msg)
        thread.last_outbound_at = msg.sent_at
        self.s.flush()
        return msg

    def send_opening(self, lead: Lead, thread: Thread) -> Message | None:
        """First outbound: disclosure + a specific question. Requires enrichment to be complete."""
        if lead.state != LeadState.NEW or lead.enriched_at is None:
            return None
        sheet = fact_sheet(self.s, lead.lead_id)
        view = compute_stage(lead.state, sheet, self._signals(lead, sheet))
        entry = catalog.find(sheet.get("make", ""), sheet.get("model", ""))
        first_spec = spec_for(view.outstanding[0]) if view.outstanding else None
        first_ask = first_spec.ask if first_spec else "Is it still available?"
        body = T.opening(
            self.identity(lead),
            lead.upstream_payload.get("seller", {}).get("display_name", ""),
            sheet.get,
            first_ask,
            entry.variants if entry else (),
        )
        asked = (
            "variant"
            if (not sheet.get("variant") and entry and len(entry.variants) >= 2)
            else (first_spec.key if first_spec else None)
        )
        msg = self._send(lead, thread, body, "opening", asked_field=asked, stage=LeadState.NEW)
        if msg is not None:
            transition(self.s, lead, LeadState.CONTACTED, "opening_sent", {"channel": thread.channel.value})
        return msg

    # ------------------------------------------------------------------ inbound

    def handle_inbound(self, msg: InboundMessage) -> HandleResult:
        thread = self.get_or_create_thread(msg)
        lead = self.link_thread(thread, msg)
        inbound = Message(
            thread_id=thread.thread_id,
            lead_id=lead.lead_id if lead else None,
            direction=Direction.INBOUND,
            body=msg.body,
            sent_at=datetime.now(UTC),
            external_msg_id=msg.external_msg_id,
            attachments=msg.attachments or None,
        )
        self.s.add(inbound)
        thread.last_inbound_at = inbound.sent_at
        thread.window_expires_at = inbound.sent_at + timedelta(hours=24)
        self.s.flush()

        if lead is None:
            return HandleResult(action="unlinked", details={"thread_id": str(thread.thread_id)})
        if lead.state in SILENT_STATES:
            return HandleResult(action="ignored", lead_id=lead.lead_id, stage=lead.state.value)
        if lead.state == LeadState.HUMAN:
            return HandleResult(action="human_owned", lead_id=lead.lead_id, stage=lead.state.value)
        if lead.state == LeadState.STALLED:
            transition(self.s, lead, LeadState.ENGAGED, "seller_replied_after_stall")

        self._last_inbound_text = msg.body
        sheet = fact_sheet(self.s, lead.lead_id)
        stage_before = compute_stage(lead.state, sheet, self._signals(lead, sheet))
        pending = self._pending_field(lead)
        ex = extract(
            msg.body,
            msg.attachments,
            pending_field=pending,
            stage_priced=stage_before.stage in PRICE_VISIBLE_STATES,
        )

        # --- screens (5.2, 8) ---
        if "stop" in ex.flags:
            sent = self._send(lead, thread, T.stop_ack(), "stop_ack", stage=lead.state)
            transition(self.s, lead, LeadState.ARCHIVED, "seller_opt_out")
            return HandleResult(
                action="opt_out",
                lead_id=lead.lead_id,
                stage=lead.state.value,
                sent=sent.body if sent else None,
            )
        if "human_question" in ex.flags:
            sent = self._send(
                lead, thread, T.human_answer(self.identity(lead)), "human_answer", stage=lead.state
            )
            escalate(self.s, lead, "human_requested", {"message": msg.body[:500]})
            return HandleResult(
                action="escalated",
                lead_id=lead.lead_id,
                stage=lead.state.value,
                escalated="human_requested",
                sent=sent.body if sent else None,
            )
        for flag in HARD_ESCALATION_FLAGS:
            if flag in ex.flags:
                escalate(self.s, lead, flag, {"message": msg.body[:500]})
                return HandleResult(
                    action="escalated", lead_id=lead.lead_id, stage=lead.state.value, escalated=flag
                )

        # --- record what the seller told us ---
        recorded = self._record_extraction(lead, sheet, ex, pending)
        if lead.state == LeadState.NEW:
            # Seller wrote first (m.me link). Answer with the opening once enrichment is done.
            if lead.enriched_at is not None:
                sent = self.send_opening(lead, thread)
                return HandleResult(
                    action="opening",
                    lead_id=lead.lead_id,
                    stage=lead.state.value,
                    sent=sent.body if sent else None,
                    facts_recorded=recorded,
                )
            enqueue(
                self.s,
                "maybe_send_opening",
                {"lead_id": str(lead.lead_id)},
                dedupe_key=f"opening:{lead.lead_id}",
            )
            return HandleResult(
                action="awaiting_enrichment",
                lead_id=lead.lead_id,
                stage=lead.state.value,
                facts_recorded=recorded,
            )

        # --- offer intents ---
        if stage_before.stage in {LeadState.OFFER_MADE, LeadState.NEGOTIATING}:
            handled = self._handle_offer_response(lead, thread, ex, msg)
            if handled is not None:
                handled.facts_recorded = recorded
                return handled

        # --- recompute stage [3] ---
        if lead.state == LeadState.CONTACTED:
            transition(self.s, lead, LeadState.ENGAGED, "seller_replied")
        sheet = fact_sheet(self.s, lead.lead_id)
        view = compute_stage(lead.state, sheet, self._signals(lead, sheet))
        if view.stage != lead.state and view.stage not in {LeadState.NEW, LeadState.CONTACTED}:
            transition(
                self.s,
                lead,
                view.stage,
                "facts_updated" if recorded else "seller_replied",
                {"outstanding": view.outstanding},
            )

        # --- no progression (5.2) ---
        if not recorded and not ex.parsed_pending and view.stage == stage_before.stage:
            turns = self._turns_without_progress(lead)
            if turns >= self.cfg.no_progress_turns:
                escalate(self.s, lead, "no_progression", {"turns": turns, "stage": view.stage.value})
                return HandleResult(
                    action="escalated",
                    lead_id=lead.lead_id,
                    stage=lead.state.value,
                    escalated="no_progression",
                    facts_recorded=recorded,
                )

        # --- respond [4]-[7] ---
        sent = self._respond(lead, thread, view, ex, pending, sheet, bool(recorded))
        self._after_stage(lead, view)
        return HandleResult(
            action="replied" if sent else "no_reply",
            lead_id=lead.lead_id,
            stage=lead.state.value,
            sent=sent.body if sent else None,
            facts_recorded=recorded,
            details=view.as_dict(),
        )

    # ------------------------------------------------------------------ pieces

    def _record_extraction(
        self, lead: Lead, sheet: FactSheet, ex: Extraction, pending: str | None
    ) -> list[str]:
        recorded: list[str] = []
        facts = dict(ex.facts)

        # Variant question from the opening: match the reply against the catalog's variant names.
        if pending == "variant" and "variant" not in facts:
            entry = catalog.find(sheet.get("make", ""), sheet.get("model", ""))
            for v in entry.variants if entry else ():
                if v.lower() in (self._last_inbound_text or "").lower():
                    facts["variant"] = v
                    ex.parsed_pending = True
                    break

        for key, value in facts.items():
            rr = record_fact(
                self.s, lead.lead_id, key, value, source=FactSource.SELLER, confidence=STATED_CONFIDENCE
            )
            if rr.created:
                recorded.append(key)
            if key in {"rego", "vin"} and rr.created:
                request_reenrichment(self.s, lead, f"{key}_from_conversation")
        if ex.photo_urls:
            have = list(sheet.get("photos") or [])
            merged = have + [u for u in ex.photo_urls if u not in have]
            rr = record_fact(self.s, lead.lead_id, "photos", merged, source=FactSource.PHOTO, verified=True)
            if rr.created:
                recorded.append("photos")
        if ex.phone:
            rr = record_fact(
                self.s,
                lead.lead_id,
                "seller_phone",
                ex.phone,
                source=FactSource.SELLER,
                confidence=STATED_CONFIDENCE,
            )
            if rr.created:
                recorded.append("seller_phone")
        # Any reply to "which is right?" acknowledges the contradiction; the verified fact stands regardless.
        if pending and pending.startswith("contradiction:"):
            fld = pending.split(":", 1)[1]
            acked = list(sheet.get("contradictions_acknowledged") or [])
            if fld not in acked:
                acked.append(fld)
                record_fact(
                    self.s,
                    lead.lead_id,
                    "contradictions_acknowledged",
                    acked,
                    source=FactSource.SELLER,
                    confidence=1.0,
                )
                recorded.append("contradictions_acknowledged")
                ex.parsed_pending = True
        return recorded

    def _respond(
        self,
        lead: Lead,
        thread: Thread,
        view: StageView,
        ex: Extraction,
        pending: str | None,
        sheet: FactSheet,
        changed: bool,
    ) -> Message | None:
        preface = "Thanks. " if changed else ""
        if view.pending_contradictions:
            fld = view.pending_contradictions[0]
            c = sheet.contradicted[fld]
            years = {int(c["claimed"]), int(c["actual"])} if fld == "year" else set()
            return self._send(
                lead,
                thread,
                T.contradiction(fld, c["claimed"], c["actual"], c["source"]),
                "contradiction",
                asked_field=f"contradiction:{fld}",
                stage=view.stage,
                allowed_years=years,
                field=fld,
            )
        if view.stage == LeadState.DISCOVERY and view.next_field is not None:
            spec = view.next_field
            if pending == spec.key and not ex.parsed_pending and not changed:
                return self._send(
                    lead,
                    thread,
                    T.clarify(spec),
                    "clarify",
                    asked_field=spec.key,
                    stage=view.stage,
                    field=spec.key,
                )
            if spec.key == "photos":
                have = len(sheet.get("photos") or [])
                if 0 < have < self.cfg.min_photos:
                    return self._send(
                        lead,
                        thread,
                        T.photos_partial(have, self.cfg.min_photos),
                        "photos_partial",
                        asked_field="photos",
                        stage=view.stage,
                        have=have,
                    )
            return self._send(
                lead,
                thread,
                T.ask_field(spec, preface=preface),
                f"ask:{spec.key}",
                asked_field=spec.key,
                stage=view.stage,
                field=spec.key,
            )
        if view.stage == LeadState.VERIFICATION:
            last = self._last_outbound(lead)
            if (
                last
                and last.validation_notes
                and last.validation_notes.get("template") == "verification_wait"
            ):
                return None
            return self._send(
                lead, thread, T.verification_wait(self.identity(lead)), "verification_wait", stage=view.stage
            )
        if view.stage == LeadState.PRICED:
            return None  # on_priced handles presentation (human or automated)
        return None

    def _after_stage(self, lead: Lead, view: StageView) -> None:
        if view.stage == LeadState.VERIFICATION and not view.verification_outstanding:
            enqueue(
                self.s,
                "value_lead",
                {"lead_id": str(lead.lead_id), "then": "on_priced"},
                dedupe_key=f"value:{lead.lead_id}",
            )

    # ------------------------------------------------------------------ offers

    def on_priced(self, lead: Lead) -> HandleResult:
        """Called once a verified valuation exists. Human presents (default) or automation does."""
        thread = self.thread_for_lead(lead)
        sheet = fact_sheet(self.s, lead.lead_id)
        view = compute_stage(lead.state, sheet, self._signals(lead, sheet))
        if view.stage != LeadState.PRICED:
            return HandleResult(
                action="not_priced", lead_id=lead.lead_id, stage=view.stage.value, details=view.as_dict()
            )
        transition(self.s, lead, LeadState.PRICED, "valuation_released")
        if self.cfg.auto_present_offer:
            offer = self.present_offer(lead, LadderStep.OPENING, presented_by="system")
            return HandleResult(
                action="offer_presented",
                lead_id=lead.lead_id,
                stage=lead.state.value,
                details={"amount": float(offer.amount)},
            )
        sent = None
        if thread is not None:
            sent = self._send(
                lead,
                thread,
                T.priced_pending_human(self.identity(lead)),
                "priced_pending_human",
                stage=LeadState.PRICED,
            )
        val = latest_valuation(self.s, lead.lead_id, basis=ValuationBasis.VERIFIED)
        create_task(
            self.s,
            lead,
            "offer_presentation",
            {
                "ladder": val.ladder if val else None,
                "band": [float(val.band_low), float(val.band_high)] if val else None,
            },
        )
        return HandleResult(
            action="awaiting_human_offer",
            lead_id=lead.lead_id,
            stage=lead.state.value,
            sent=sent.body if sent else None,
        )

    def present_offer(self, lead: Lead, step: LadderStep, *, presented_by: str) -> Offer:
        """Record and send an offer at an authorised ladder step. Floor is a human decision."""
        if lead.state not in {LeadState.PRICED, LeadState.OFFER_MADE, LeadState.NEGOTIATING}:
            raise ValueError(f"cannot present an offer in state {lead.state.value}")
        val = latest_valuation(self.s, lead.lead_id, basis=ValuationBasis.VERIFIED)
        if val is None:
            raise ValueError("no verified valuation to present from")
        if step == LadderStep.FLOOR and not presented_by.startswith("human"):
            raise ValueError("floor is not reachable by automation")
        if step == LadderStep.HUMAN:
            raise ValueError("use record_human_offer for above-floor amounts")
        amount = float(val.ladder[step.value])
        prev = self.current_offer(lead)
        if prev is not None and prev.outcome is None:
            prev.outcome = OfferOutcome.SUPERSEDED
            prev.outcome_at = datetime.now(UTC)
        expires = datetime.now(UTC) + timedelta(hours=self.cfg.offer_expiry_hours)
        offer = Offer(
            lead_id=lead.lead_id,
            valuation_id=val.valuation_id,
            amount=amount,
            ladder_step=step,
            presented_by=presented_by,
            presented_at=datetime.now(UTC),
            expires_at=expires,
        )
        self.s.add(offer)
        self.s.flush()
        transition(
            self.s,
            lead,
            LeadState.OFFER_MADE,
            f"offer_presented:{step.value}",
            {"amount": amount, "by": presented_by},
        )
        thread = self.thread_for_lead(lead)
        if thread is not None:
            sheet = fact_sheet(self.s, lead.lead_id)
            if prev is None:
                body, tid = (
                    T.offer(amount, expires, sheet.get, self.identity(lead), self.cfg.timezone),
                    "offer",
                )
            else:
                body, tid = T.concession(amount, expires, self.cfg.timezone), "concession"
            self._send(
                lead,
                thread,
                body,
                tid,
                stage=LeadState.OFFER_MADE,
                ladder=val.ladder,
                current_offer=amount,
                amount=amount,
            )
        enqueue(
            self.s,
            "expire_offer",
            {"offer_id": str(offer.offer_id)},
            run_at=expires,
            dedupe_key=f"expire:{offer.offer_id}",
        )
        self._resolve_offer_tasks(lead, presented_by, offer)
        return offer

    def _resolve_offer_tasks(self, lead: Lead, by: str, offer: Offer) -> None:
        for e in self.s.query(Escalation).filter(
            Escalation.lead_id == lead.lead_id,
            Escalation.reason.in_(["offer_presentation", "offer_response_needed"]),
            Escalation.resolved_at.is_(None),
        ):
            e.resolved_by, e.resolution, e.resolved_at = (
                by,
                f"offer presented: {offer.ladder_step.value} ${float(offer.amount):,.0f}",
                datetime.now(UTC),
            )
        self.s.flush()

    def _handle_offer_response(
        self, lead: Lead, thread: Thread, ex: Extraction, msg: InboundMessage
    ) -> HandleResult | None:
        offer = self.current_offer(lead)
        if offer is None:
            return None
        val = self.s.get(Valuation, offer.valuation_id)
        ladder = val.ladder if val else None
        if "accept" in ex.intents or (
            ex.counter_price is not None and ex.counter_price <= float(offer.amount)
        ):
            offer.outcome, offer.outcome_at = OfferOutcome.ACCEPTED, datetime.now(UTC)
            transition(self.s, lead, LeadState.ACCEPTED, "seller_accepted", {"amount": float(offer.amount)})
            sent = self._send(
                lead,
                thread,
                T.accepted(
                    self.identity(lead), lead.upstream_payload.get("seller", {}).get("display_name", "")
                ),
                "accepted",
                stage=LeadState.ACCEPTED,
                ladder=ladder,
                current_offer=float(offer.amount),
            )
            packet = write_packet(self.s, lead, offer, val, sla_hours=self.cfg.human_sla_hours)
            transition(
                self.s,
                lead,
                LeadState.HANDOFF,
                "handoff_packet_written",
                {"packet_id": str(packet.packet_id)},
            )
            create_task(
                self.s,
                lead,
                "handoff",
                {"packet_id": str(packet.packet_id), "sla_expires_at": packet.sla_expires_at.isoformat()},
            )
            return HandleResult(
                action="accepted",
                lead_id=lead.lead_id,
                stage=lead.state.value,
                sent=sent.body if sent else None,
                details={"amount": float(offer.amount)},
            )

        if "reject" in ex.intents and ex.counter_price is None and _is_final_rejection(msg.body):
            offer.outcome, offer.outcome_at = OfferOutcome.REJECTED, datetime.now(UTC)
            transition(self.s, lead, LeadState.REJECTED, "seller_rejected")
            sent = self._send(
                lead,
                thread,
                T.rejected_close(offer.expires_at, self.cfg.timezone),
                "rejected_close",
                stage=LeadState.REJECTED,
                ladder=ladder,
                current_offer=float(offer.amount),
            )
            transition(self.s, lead, LeadState.ARCHIVED, "rejected_archived")
            return HandleResult(
                action="rejected",
                lead_id=lead.lead_id,
                stage=lead.state.value,
                sent=sent.body if sent else None,
            )

        if "counter" in ex.intents or "reject" in ex.intents:
            transition(self.s, lead, LeadState.NEGOTIATING, "seller_countered", {"counter": ex.counter_price})
            if not self.cfg.auto_present_offer:
                escalate(
                    self.s,
                    lead,
                    "offer_response_needed",
                    {"counter": ex.counter_price, "current": float(offer.amount), "message": msg.body[:300]},
                )
                return HandleResult(
                    action="escalated",
                    lead_id=lead.lead_id,
                    stage=lead.state.value,
                    escalated="offer_response_needed",
                )
            idx = (
                LADDER_ORDER.index(offer.ladder_step)
                if offer.ladder_step in LADDER_ORDER
                else len(LADDER_ORDER)
            )
            if idx + 1 < len(LADDER_ORDER):
                nxt = LADDER_ORDER[idx + 1]
                new = self.present_offer(lead, nxt, presented_by="system")
                return HandleResult(
                    action="concession",
                    lead_id=lead.lead_id,
                    stage=lead.state.value,
                    details={"amount": float(new.amount), "step": nxt.value},
                )
            sent = self._send(
                lead,
                thread,
                T.at_ceiling(float(offer.amount), self.identity(lead)),
                "at_ceiling",
                stage=LeadState.NEGOTIATING,
                ladder=ladder,
                current_offer=float(offer.amount),
            )
            escalate(
                self.s,
                lead,
                "above_authorised_ladder",
                {
                    "counter": ex.counter_price,
                    "last_step": offer.ladder_step.value,
                    "amount": float(offer.amount),
                },
            )
            return HandleResult(
                action="escalated",
                lead_id=lead.lead_id,
                stage=lead.state.value,
                escalated="above_authorised_ladder",
                sent=sent.body if sent else None,
            )

        if "price_question" in ex.intents:
            sheet = fact_sheet(self.s, lead.lead_id)
            body = T.offer(
                float(offer.amount), offer.expires_at, sheet.get, self.identity(lead), self.cfg.timezone
            )
            sent = self._send(
                lead,
                thread,
                body,
                "offer_restate",
                stage=lead.state,
                ladder=ladder,
                current_offer=float(offer.amount),
                amount=float(offer.amount),
            )
            return HandleResult(
                action="restated",
                lead_id=lead.lead_id,
                stage=lead.state.value,
                sent=sent.body if sent else None,
            )
        return None

    def record_human_offer(
        self, lead: Lead, amount: float, *, presented_by: str, valuation: Valuation | None = None
    ) -> Offer:
        """An above-ladder amount decided by a human. Sent as a concession message."""
        val = valuation or latest_valuation(self.s, lead.lead_id, basis=ValuationBasis.VERIFIED)
        if val is None:
            raise ValueError("no verified valuation")
        prev = self.current_offer(lead)
        if prev is not None and prev.outcome is None:
            prev.outcome, prev.outcome_at = OfferOutcome.SUPERSEDED, datetime.now(UTC)
        expires = datetime.now(UTC) + timedelta(hours=self.cfg.offer_expiry_hours)
        offer = Offer(
            lead_id=lead.lead_id,
            valuation_id=val.valuation_id,
            amount=amount,
            ladder_step=LadderStep.HUMAN,
            presented_by=presented_by,
            presented_at=datetime.now(UTC),
            expires_at=expires,
        )
        self.s.add(offer)
        self.s.flush()
        transition(
            self.s,
            lead,
            LeadState.OFFER_MADE,
            "offer_presented:human",
            {"amount": amount, "by": presented_by},
        )
        thread = self.thread_for_lead(lead)
        if thread is not None:
            self._send(
                lead,
                thread,
                T.concession(amount, expires, self.cfg.timezone),
                "concession",
                stage=LeadState.OFFER_MADE,
                ladder=val.ladder,
                current_offer=amount,
                amount=amount,
            )
        enqueue(
            self.s,
            "expire_offer",
            {"offer_id": str(offer.offer_id)},
            run_at=expires,
            dedupe_key=f"expire:{offer.offer_id}",
        )
        self._resolve_offer_tasks(lead, presented_by, offer)
        return offer


def _is_final_rejection(text: str) -> bool:
    import re

    return bool(
        re.search(
            r"\b(not interested|no thanks|i'?ll pass|pass on|keep it|forget it|not selling|no longer selling|sold it|already sold)\b",
            text,
            re.I,
        )
    )


# ---------------------------------------------------------------------- job handlers


def _conversation_for(session: Session, lead: Lead) -> tuple[Conversation, Thread] | None:
    from acqbot.transport.registry import get_transport

    thread = session.scalars(
        select(Thread).where(Thread.lead_id == lead.lead_id).order_by(Thread.created_at.desc())
    ).first()
    if thread is None:
        return None
    return Conversation(session, get_transport(thread.channel)), thread


def register_handlers() -> None:
    from acqbot.queue.worker import job_handler

    @job_handler("maybe_send_opening")
    def _maybe_send_opening(session: Session, payload: dict[str, Any]) -> None:
        lead = session.get(Lead, uuid.UUID(payload["lead_id"]))
        if lead is None or lead.state != LeadState.NEW or lead.enriched_at is None:
            return
        pair = _conversation_for(session, lead)
        if pair is None:
            return
        conv, thread = pair
        conv.send_opening(lead, thread)

    @job_handler("on_priced")
    def _on_priced(session: Session, payload: dict[str, Any]) -> None:
        lead = session.get(Lead, uuid.UUID(payload["lead_id"]))
        if lead is None:
            return
        pair = _conversation_for(session, lead)
        if pair is None:
            return
        pair[0].on_priced(lead)

    @job_handler("expire_offer")
    def _expire_offer(session: Session, payload: dict[str, Any]) -> None:
        offer = session.get(Offer, uuid.UUID(payload["offer_id"]))
        if offer is None or offer.outcome is not None:
            return
        if offer.expires_at > datetime.now(UTC):
            return
        offer.outcome, offer.outcome_at = OfferOutcome.EXPIRED, datetime.now(UTC)
        lead = session.get(Lead, offer.lead_id)
        if lead is not None and lead.state in {LeadState.OFFER_MADE, LeadState.NEGOTIATING}:
            create_task(
                session,
                lead,
                "offer_expired",
                {"offer_id": str(offer.offer_id), "amount": float(offer.amount)},
            )


register_handlers()
