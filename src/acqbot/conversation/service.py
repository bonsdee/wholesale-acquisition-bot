"""Conversation orchestrator — Sections 5, 7 and 8.

Per inbound message (Figure 4):
    [1] persist to the append-only thread store
    [2] extract structured facts and screens — the extraction model with the regex screens as a
        floor (Phase 4), or the regex parsers alone when no model is configured (Phase 3)
    [3] recompute the state machine position from the fact store
    [4] the planner decides what the next message does (a Directive carrying scripted wording)
    [5] the conversation model writes it — discovery messages only in Phase 4 — or the wording is used as-is
    [6] validation gate — every outbound, scripted or generated; one rewrite, then the script
    [7] send via the transport and log the outbound with its model or template version

Escalation triggers (5.2) route to a human with the pending response discarded. The one exception
is the direct "are you a bot?" question, which gets the plain answer Section 8 requires and is then
handed over.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from acqbot.config import Settings, get_settings
from acqbot.conversation import compose as C
from acqbot.conversation import templates as T
from acqbot.conversation.compose import (
    Composer,
    Directive,
    ask_field_instruction,
    clarify_instruction,
    contradiction_instruction,
    photos_partial_instruction,
    verification_wait_instruction,
)
from acqbot.conversation.extract import PUSHBACK, REJECT, Extraction, extract
from acqbot.conversation.extract_model import ExtractionContext, ModelExtractor
from acqbot.conversation.gate import GateContext, validate
from acqbot.conversation.handoff import write_packet
from acqbot.conversation.history import as_turns
from acqbot.conversation.state import PRICE_VISIBLE_STATES, Signals, StageView, compute_stage
from acqbot.conversation.transitions import create_task, escalate, transition
from acqbot.enrichment import catalog
from acqbot.facts.fields import SELLER_PHONE, STATED_CONFIDENCE, spec_for
from acqbot.facts.store import FactSheet, fact_sheet, record_fact
from acqbot.ingestion.service import request_reenrichment
from acqbot.llm.client import ModelClient
from acqbot.llm.registry import get_model_client
from acqbot.models import (
    Channel,
    Direction,
    Escalation,
    FactSource,
    HandoffPacket,
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
# Fact-store rows that are conversation bookkeeping, not vehicle facts: never count as progress.
BOOKKEEPING_FIELDS = ("seller_notes", "deferred_fields")
# States where the ball is in the seller's court, so silence is worth chasing. Everything else is
# either ours to act on (VERIFICATION, PRICED), a person's (HUMAN), or over.
WAITING_STATES = {
    LeadState.CONTACTED,
    LeadState.ENGAGED,
    LeadState.DISCOVERY,
    LeadState.OFFER_MADE,
    LeadState.NEGOTIATING,
}


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
    def __init__(
        self,
        session: Session,
        transport: Transport,
        settings: Settings | None = None,
        model: ModelClient | None = None,
    ) -> None:
        self.s = session
        self.t = transport
        self.cfg = settings or get_settings()
        self.model = model if model is not None else get_model_client(self.cfg)
        self._last_inbound_text: str | None = None
        self._last_escalation: str | None = None

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

    # ------------------------------------------------------------------ nudges (Phase 6)

    def nudge_schedule(self) -> list[int]:
        """Hours of silence before each nudge, measured from our last message.

        The first sits inside Messenger's 24-hour window — 4.2 says design the cadence *around* that
        window, and a nudge at hour 20 is the last one we may legally send on-platform. The rest are
        spaced for SMS, where there is no window but there are manners."""
        hours = [self.cfg.nudge_before_window_closes_hours, *self.cfg.sms_nudge_hours]
        return hours[: max(0, self.cfg.max_nudges)]

    def _nudges_sent(self, lead: Lead) -> int:
        return (
            self.s.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.lead_id == lead.lead_id,
                    Message.direction == Direction.OUTBOUND,
                    Message.validation_notes["template"].astext.startswith("nudge"),
                )
            )
            or 0
        )

    def nudge(self, lead: Lead, thread: Thread) -> HandleResult:
        """Chase a quiet seller, or give up on one. Called by the `nudge` job.

        The job's own timing is not trusted: the clock is recomputed from the last message every
        time, so an early firing reschedules itself and a late one still does the right thing."""
        if lead.state not in WAITING_STATES:
            return HandleResult(action="nudge_skipped", lead_id=lead.lead_id, stage=lead.state.value)
        last_out = self._last_outbound(lead)
        if last_out is None:
            return HandleResult(action="nudge_skipped", lead_id=lead.lead_id)
        if thread.last_inbound_at and thread.last_inbound_at > last_out.sent_at:
            return HandleResult(action="nudge_not_needed", lead_id=lead.lead_id)  # they replied

        schedule = self.nudge_schedule()
        sent = self._nudges_sent(lead)
        last_at = last_out.sent_at if last_out.sent_at.tzinfo else last_out.sent_at.replace(tzinfo=UTC)
        if sent >= len(schedule):
            return self._stall(lead, thread)
        due_at = last_at + timedelta(hours=schedule[sent])
        if due_at > datetime.now(UTC):
            self._schedule_nudge(lead, due_at)  # fired early; come back when it is actually due
            return HandleResult(action="nudge_deferred", lead_id=lead.lead_id)

        asked = self._pending_field(lead)
        spec = spec_for(asked) if asked else None
        if lead.state in {LeadState.OFFER_MADE, LeadState.NEGOTIATING}:
            body, template = T.nudge_offer(self.identity(lead)), "nudge_offer"
        else:
            label = spec.label.lower() if spec else None
            body, template = T.nudge_discovery(self.identity(lead), label), "nudge_discovery"
        msg = self._send(lead, thread, body, template, asked_field=asked, stage=lead.state)
        if msg is None:
            # No open window and nowhere to migrate to: there is no way to reach this seller, and
            # queueing three more undeliverable nudges helps nobody.
            return self._stall(lead, thread)
        self._schedule_nudge(
            lead, datetime.now(UTC) + timedelta(hours=schedule[min(sent + 1, len(schedule) - 1)])
        )
        return HandleResult(action="nudged", lead_id=lead.lead_id, stage=lead.state.value, sent=msg.body)

    def _stall(self, lead: Lead, thread: Thread) -> HandleResult:
        """Out of nudges. Say so once, mark it STALLED, and leave archiving to a person — the
        re-engagement policy is still an open question (A.2) and is not ours to invent."""
        sent = None
        if self._nudges_sent(lead):
            sent = self._send(
                lead, thread, T.stalled_close(self.identity(lead)), "stalled_close", stage=lead.state
            )
        transition(self.s, lead, LeadState.STALLED, "no_reply_after_nudges")
        create_task(self.s, lead, "stalled_no_reply", {"nudges": self._nudges_sent(lead)})
        return HandleResult(
            action="stalled",
            lead_id=lead.lead_id,
            stage=lead.state.value,
            sent=sent.body if sent else None,
        )

    def _schedule_nudge(self, lead: Lead, run_at: datetime) -> None:
        enqueue(
            self.s,
            "nudge",
            {"lead_id": str(lead.lead_id)},
            run_at=run_at,
            dedupe_key=f"nudge:{lead.lead_id}:{run_at.isoformat(timespec='minutes')}",
        )

    def _migrate_to_sms(self, lead: Lead, thread: Thread) -> tuple[Transport, Thread] | None:
        """4.2 Stage 3 — carry the conversation off Messenger when its window shuts.

        Returns the SMS transport and thread, or None when there is nothing to move to: no number on
        file, Twilio not configured, or we are already on SMS. The orchestrator above this line never
        learns which channel carried the message (4.3) — it just gets a send that worked."""
        from acqbot.transport.registry import get_transport

        if thread.channel == Channel.SMS or not self.cfg.sms_migration:
            return None
        phone = fact_sheet(self.s, lead.lead_id).get("seller_phone")
        if not phone:
            return None
        try:
            transport = get_transport(Channel.SMS)
        except RuntimeError as exc:  # Twilio not configured — a person has to pick this up
            log.warning("cannot migrate lead %s to SMS: %s", lead.lead_id, exc)
            return None
        sms = self.s.scalars(
            select(Thread)
            .where(Thread.lead_id == lead.lead_id, Thread.channel == Channel.SMS)
            .order_by(Thread.created_at.desc())
        ).first()
        if sms is None:
            sms = Thread(channel=Channel.SMS, external_id=str(phone), lead_id=lead.lead_id)
            self.s.add(sms)
            self.s.flush()
            # No state transition: the deal did not move, the wire did. The new thread row and the
            # thread_id on every message are the record of where the conversation changed channel.
            log.info("lead %s moved from %s to SMS", lead.lead_id, thread.channel.value)
        return transport, sms

    def _ever_asked(self, lead: Lead, field_key: str) -> bool:
        """Whether any outbound has already asked for this field. Used for the fields we ask once
        and then let go — pressing a seller twice for their phone number is how you lose them."""
        return bool(
            self.s.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.lead_id == lead.lead_id,
                    Message.direction == Direction.OUTBOUND,
                    Message.validation_notes["asked_field"].astext == field_key,
                )
            )
        )

    def _turns_without_progress(self, lead: Lead) -> int:
        """Consecutive inbound messages since the last recorded fact, state change or offer."""
        from acqbot.models import StateLog, VehicleFact

        last_fact = self.s.scalar(
            select(func.max(VehicleFact.recorded_at)).where(
                VehicleFact.lead_id == lead.lead_id, VehicleFact.field.notin_(BOOKKEEPING_FIELDS)
            )
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

    def _gate_ctx(
        self,
        sheet: FactSheet,
        stage: LeadState,
        *,
        ladder: dict[str, float] | None = None,
        current_offer: float | None = None,
        allowed_years: set[int] | None = None,
        allowed_odometers: set[int] | None = None,
        sole_figure: float | None = None,
        must_include: list[str] | None = None,
    ) -> GateContext:
        def _int(v: Any) -> int | None:
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        # The car's own name, tokenised: "Landcruiser Prado GXL" contributes GXL, so naming the
        # vehicle is never mistaken for shouting.
        caps: set[str] = set()
        for part in (sheet.get("make"), sheet.get("model"), sheet.get("variant")):
            if isinstance(part, str):
                caps |= {tok for tok in re.split(r"[^A-Za-z0-9]+", part) if tok}
        return GateContext(
            stage=stage,
            ladder=ladder,
            current_offer=current_offer,
            vehicle_year=_int(sheet.get("year")),
            allowed_years=allowed_years or set(),
            max_length=self.t.max_body_length,
            vehicle_make=sheet.get("make"),
            vehicle_odometer_km=_int(sheet.get("odometer_km")),
            allowed_odometers=allowed_odometers or set(),
            allowed_caps=caps,
            sole_figure=sole_figure,
            must_include=list(must_include or []),
        )

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
        allowed_odometers: set[int] | None = None,
        sole_figure: float | None = None,
        must_include: list[str] | None = None,
        model_version: str | None = None,
        prompt_hash: str | None = None,
        generator: str = "template",
        notes: dict[str, Any] | None = None,
        **vars: Any,
    ) -> Message | None:
        sheet = fact_sheet(self.s, lead.lead_id)
        ctx = self._gate_ctx(
            sheet,
            stage or lead.state,
            ladder=ladder,
            current_offer=current_offer,
            allowed_years=allowed_years,
            allowed_odometers=allowed_odometers,
            sole_figure=sole_figure,
            must_include=must_include,
        )
        gate = validate(body, ctx)
        if not gate.ok:
            log.error("gate rejected template %s for lead %s: %s", template_id, lead.lead_id, gate.violations)
            escalate(
                self.s, lead, "template_failed_gate", {"template": template_id, "violations": gate.violations}
            )
            return None
        info = ThreadInfo(external_id=thread.external_id, last_inbound_at=thread.last_inbound_at)
        transport = self.t
        if not transport.send_window_open(info):
            moved = self._migrate_to_sms(lead, thread)
            if moved is None:
                create_task(
                    self.s,
                    lead,
                    "send_window_closed",
                    {"template": template_id, "channel": thread.channel.value},
                )
                return None
            transport, thread = moved
            info = ThreadInfo(external_id=thread.external_id, last_inbound_at=thread.last_inbound_at)
        try:
            receipt = transport.send(info, body)
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
            model_version=model_version or T.TEMPLATE_VERSION,
            prompt_hash=prompt_hash or T.template_hash(template_id, **vars),
            validated=True,
            validation_notes={
                "template": template_id,
                "asked_field": asked_field,
                "gate": gate.as_dict(),
                "generator": generator,
                **(notes or {}),
            },
        )
        self.s.add(msg)
        thread.last_outbound_at = msg.sent_at
        self.s.flush()
        if (stage or lead.state) in WAITING_STATES and not template_id.startswith(("nudge", "stalled")):
            self._schedule_nudge(
                lead, msg.sent_at + timedelta(hours=self.cfg.nudge_before_window_closes_hours)
            )
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
        # [1] persist — idempotently, so a retried job (a send that failed on the way out) or a
        # redelivered webhook does not record the seller's message twice.
        inbound = None
        if msg.external_msg_id:
            inbound = self.s.scalars(
                select(Message).where(
                    Message.thread_id == thread.thread_id,
                    Message.direction == Direction.INBOUND,
                    Message.external_msg_id == msg.external_msg_id,
                )
            ).first()
        if inbound is None:
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
        self._last_escalation = None
        sheet = fact_sheet(self.s, lead.lead_id)
        stage_before = compute_stage(lead.state, sheet, self._signals(lead, sheet))
        pending = self._pending_field(lead)
        ex = self._extract(
            lead,
            thread,
            msg,
            sheet,
            pending,
            stage_before.stage in PRICE_VISIBLE_STATES,
            exclude_msg_id=inbound.msg_id,
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
        sent = self._respond(lead, thread, view, ex, pending, sheet, recorded)
        if self._last_escalation:
            return HandleResult(
                action="escalated",
                lead_id=lead.lead_id,
                stage=lead.state.value,
                escalated=self._last_escalation,
                facts_recorded=recorded,
            )
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

    def _extract(
        self,
        lead: Lead,
        thread: Thread,
        msg: InboundMessage,
        sheet: FactSheet,
        pending: str | None,
        stage_priced: bool,
        exclude_msg_id: uuid.UUID | None = None,
    ) -> Extraction:
        """Stage 2: the extraction model over the regex floor, or the regex parsers alone."""
        if self.model is None:
            return extract(msg.body, msg.attachments, pending_field=pending, stage_priced=stage_priced)
        last = self._last_outbound(lead)
        recent = list(
            self.s.scalars(
                select(Message)
                .where(Message.lead_id == lead.lead_id, Message.msg_id != exclude_msg_id)
                .order_by(Message.sent_at.desc(), Message.msg_id.desc())
                .limit(4)
            )
        )[::-1]
        ctx = ExtractionContext(
            lead_id=lead.lead_id,
            thread_id=thread.thread_id,
            vehicle=T._vehicle(sheet.get),
            pending_field=pending,
            last_question=last.body if last else None,
            recent_turns=as_turns(recent),
            stage_priced=stage_priced,
        )
        return ModelExtractor(self.s, self.model, self.cfg).extract(msg.body, msg.attachments, ctx)

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
        # Things the model noticed that are not fields (Figure 5: they ride along in the packet's flags).
        # Neither notes nor deferrals count as progress — they must not reset the no-progression counter.
        if ex.notes:
            have = list(sheet.get("seller_notes") or [])
            merged = have + [n for n in ex.notes if n not in have]
            if merged != have:
                record_fact(
                    self.s,
                    lead.lead_id,
                    "seller_notes",
                    merged[:20],
                    source=FactSource.SELLER,
                    confidence=1.0,
                )
        if "defer" in ex.intents and pending and spec_for(pending) and not ex.parsed_pending:
            deferred = list(sheet.get("deferred_fields") or [])
            if pending not in deferred:
                record_fact(
                    self.s,
                    lead.lead_id,
                    "deferred_fields",
                    deferred + [pending],
                    source=FactSource.SELLER,
                    confidence=1.0,
                )
        return recorded

    def _respond(
        self,
        lead: Lead,
        thread: Thread,
        view: StageView,
        ex: Extraction,
        pending: str | None,
        sheet: FactSheet,
        recorded: list[str],
    ) -> Message | None:
        directive = self._plan(lead, view, ex, pending, sheet, recorded)
        if directive is None:
            return None
        return self._deliver(lead, thread, directive, view, sheet)

    def _plan(
        self,
        lead: Lead,
        view: StageView,
        ex: Extraction,
        pending: str | None,
        sheet: FactSheet,
        recorded: list[str],
    ) -> Directive | None:
        """Stage 4: decide what the next message does. Deterministic; the model only words it."""
        changed = bool(recorded)
        agent = self.identity(lead).agent
        if view.pending_contradictions:
            fld = view.pending_contradictions[0]
            c = sheet.contradicted[fld]
            years = {int(c["claimed"]), int(c["actual"])} if fld == "year" else set()
            odos = {int(c["claimed"]), int(c["actual"])} if fld == "odometer_km" else set()
            return Directive(
                kind="contradiction",
                template_id="contradiction",
                body=T.contradiction(fld, c["claimed"], c["actual"], c["source"]),
                stage=view.stage,
                asked_field=f"contradiction:{fld}",
                allowed_years=years,
                allowed_odometers=odos,
                instruction=contradiction_instruction(fld, c["claimed"], c["actual"], c["source"]),
                template_vars={"field": fld},
            )
        if view.stage == LeadState.DISCOVERY and view.next_field is not None:
            # Fields the seller said they'd come back on go to the end of the queue while others remain.
            deferred_keys = [k for k in (sheet.get("deferred_fields") or []) if k in view.outstanding]
            order = [k for k in view.outstanding if k not in deferred_keys] + deferred_keys
            spec = spec_for(order[0]) or view.next_field
            deferred_now = "defer" in ex.intents and pending == view.next_field.key and not ex.parsed_pending
            deferred_label = None
            if deferred_now:
                dspec = spec_for(pending)
                deferred_label = dspec.label.lower() if dspec else pending
            if pending == spec.key and not ex.parsed_pending and not changed and not deferred_now:
                return Directive(
                    kind="clarify",
                    template_id="clarify",
                    body=T.clarify(spec),
                    stage=view.stage,
                    asked_field=spec.key,
                    instruction=clarify_instruction(
                        spec,
                        low_confidence=ex.low_confidence.get(spec.key),
                        seller_question=ex.seller_question,
                        agent=agent,
                    ),
                    template_vars={"field": spec.key},
                )
            # 4.2 Stage 2 — the mobile number, asked once, just before the photos. The spec calls
            # this an early objective; asked on turn two, before a single question about the car, it
            # reads as a data grab, and the framing the spec relies on ("a step toward being paid")
            # only becomes true this late. Declined once, it is never asked again and never blocks.
            if (
                spec.key == "photos"
                and not sheet.get("seller_phone")
                and pending != SELLER_PHONE.key
                and not self._ever_asked(lead, SELLER_PHONE.key)
            ):
                return Directive(
                    kind="ask_field",
                    template_id=f"ask:{SELLER_PHONE.key}",
                    body=T.ask_field(SELLER_PHONE, preface="Thanks. " if changed else ""),
                    stage=view.stage,
                    asked_field=SELLER_PHONE.key,
                    instruction=ask_field_instruction(
                        SELLER_PHONE,
                        recorded=[k for k in recorded if k not in BOOKKEEPING_FIELDS],
                        seller_question=ex.seller_question,
                        deferred=deferred_label,
                        agent=agent,
                    ),
                    template_vars={"field": SELLER_PHONE.key},
                )
            if spec.key == "photos":
                have = len(sheet.get("photos") or [])
                if 0 < have < self.cfg.min_photos:
                    return Directive(
                        kind="photos_partial",
                        template_id="photos_partial",
                        body=T.photos_partial(have, self.cfg.min_photos),
                        stage=view.stage,
                        asked_field="photos",
                        instruction=photos_partial_instruction(have, self.cfg.min_photos),
                        template_vars={"have": have},
                    )
            return Directive(
                kind="ask_field",
                template_id=f"ask:{spec.key}",
                body=T.ask_field(spec, preface="Thanks. " if changed else ""),
                stage=view.stage,
                asked_field=spec.key,
                instruction=ask_field_instruction(
                    spec,
                    recorded=[k for k in recorded if k not in BOOKKEEPING_FIELDS],
                    seller_question=ex.seller_question,
                    deferred=deferred_label,
                    only_remaining=bool(deferred_now and spec.key == pending),
                    agent=agent,
                ),
                template_vars={"field": spec.key},
            )
        if view.stage == LeadState.VERIFICATION:
            last = self._last_outbound(lead)
            if (
                last
                and last.validation_notes
                and last.validation_notes.get("template") == "verification_wait"
            ):
                return None
            return Directive(
                kind="verification_wait",
                template_id="verification_wait",
                body=T.verification_wait(self.identity(lead)),
                stage=view.stage,
                instruction=verification_wait_instruction(),
            )
        if view.stage == LeadState.PRICED:
            return None  # on_priced handles presentation (human or automated)
        return None

    def _deliver(
        self, lead: Lead, thread: Thread, directive: Directive, view: StageView | None, sheet: FactSheet
    ) -> Message | None:
        """Stages 5–7: word it (model or script), gate it, send it, log it."""
        gate_ctx = self._gate_ctx(
            sheet,
            directive.stage,
            ladder=directive.ladder,
            current_offer=directive.current_offer,
            allowed_years=directive.allowed_years,
            allowed_odometers=directive.allowed_odometers,
            sole_figure=directive.sole_figure,
            must_include=directive.must_include,
        )
        result = Composer(self.s, self.model, self.cfg).compose(
            lead,
            thread,
            directive,
            sheet=sheet,
            outstanding=view.outstanding if view else [],
            identity=self.identity(lead),
            seller_first_name=(lead.upstream_payload.get("seller", {}).get("display_name") or "").split(" ")[
                0
            ],
            channel=thread.channel.value,
            gate_ctx=gate_ctx,
        )
        if result.escalate_reason:
            reason = f"model_flagged:{result.escalate_reason}"
            escalate(self.s, lead, reason, {"directive": directive.kind, **result.notes})
            self._last_escalation = reason
            return None
        return self._send(
            lead,
            thread,
            result.body,
            directive.template_id,
            asked_field=directive.asked_field,
            stage=directive.stage,
            ladder=directive.ladder,
            current_offer=directive.current_offer,
            allowed_years=directive.allowed_years,
            allowed_odometers=directive.allowed_odometers,
            sole_figure=directive.sole_figure,
            must_include=directive.must_include,
            model_version=result.model_version,
            prompt_hash=result.notes.get("prompt_hash"),
            generator=result.generator,
            notes={k: v for k, v in result.notes.items() if k != "prompt_hash"},
            **directive.template_vars,
        )

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
            first = prev is None
            expiry_phrase = T.fmt_expiry(expires, self.cfg.timezone)
            body = (
                T.offer(amount, expires, sheet.get, self.identity(lead), self.cfg.timezone)
                if first
                else T.concession(amount, expires, self.cfg.timezone)
            )
            self._deliver(
                lead,
                thread,
                self._offer_directive(
                    kind="offer" if first else "concession",
                    body=body,
                    instruction=C.offer_instruction(
                        amount=T.fmt_money(amount),
                        expiry=expiry_phrase,
                        vehicle=T._vehicle(sheet.get) or "the car",
                        first_offer=first,
                    ),
                    amount=amount,
                    ladder=val.ladder,
                    expiry_phrase=expiry_phrase,
                    stage=LeadState.OFFER_MADE,
                ),
                None,
                sheet,
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

    def notify_offer_lapsed(self, lead: Lead, thread: Thread) -> Message | None:
        """Tell the seller the 48 hours are up. Scripted on purpose — there is no figure to word,
        and outside the Messenger window this correctly becomes a `send_window_closed` task rather
        than an illegal send."""
        return self._send(lead, thread, T.offer_lapsed(self.identity(lead)), "offer_lapsed", stage=lead.state)

    def _ceiling_request(
        self,
        lead: Lead,
        offer: Offer,
        counter: float | None,
        ladder: dict[str, float] | None,
    ) -> tuple[str, dict[str, Any]]:
        """The automated ladder is spent. Ask a person for the ceiling, with the sum already done.

        Two different questions, so two different reasons: a counter the ceiling would cover is an
        approval ("yes, go to $X"), and one above it is a decision that needs an amount."""
        floor = float(ladder["floor"]) if ladder and "floor" in ladder else None
        details: dict[str, Any] = {
            "counter": counter,
            "last_step": offer.ladder_step.value,
            "amount": float(offer.amount),
            "ceiling": floor,
            "gap": (round(counter - float(offer.amount), 2) if counter is not None else None),
        }
        within_ceiling = floor is not None and (counter is None or counter <= floor)
        reason = "ceiling_approval" if within_ceiling else "above_authorised_ladder"
        escalate(self.s, lead, reason, details)
        return reason, details

    def _offer_directive(
        self,
        *,
        kind: str,
        body: str,
        instruction: str,
        amount: float,
        ladder: dict[str, float] | None,
        expiry_phrase: str | None,
        stage: LeadState,
    ) -> Directive:
        """An offer message the model may word but not author.

        `sole_figure` narrows the gate from "any authorised ladder value" to this one amount, and
        `must_include` pins the amount and the expiry as code wrote them. Between them the model can
        change every word of an offer and none of its content."""
        money = T.fmt_money(amount)
        return Directive(
            kind=kind,
            template_id=kind,
            body=body,
            stage=stage,
            instruction=instruction,
            ladder=ladder,
            current_offer=amount,
            sole_figure=amount,
            must_include=[money] + ([expiry_phrase] if expiry_phrase else []),
            template_vars={"amount": amount},
        )

    def _resolve_offer_tasks(self, lead: Lead, by: str, offer: Offer) -> None:
        for e in self.s.query(Escalation).filter(
            Escalation.lead_id == lead.lead_id,
            Escalation.reason.in_(
                [
                    "offer_presentation",
                    "offer_response_needed",
                    "offer_expired",
                    # Presenting the ceiling IS the answer to the request to present the ceiling.
                    "ceiling_approval",
                    "above_authorised_ladder",
                ]
            ),
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
        amount = float(offer.amount)
        # A figure at or below ours only reads as acceptance when nothing in the message pushes back:
        # "$5,600 is too low" quotes our own number and is a counter, not a yes.
        pushback = "reject" in ex.intents or bool(REJECT.search(msg.body) or PUSHBACK.search(msg.body))
        quotes_ours = ex.counter_price is not None and abs(ex.counter_price - amount) < 1
        accepts_by_figure = ex.counter_price is not None and ex.counter_price <= amount and not pushback
        if ("accept" in ex.intents and not pushback) or accepts_by_figure:
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
            # Figure 5: "an expired packet returns to the queue with a flag rather than going
            # silent." The timer starts now, so the alarm is set now.
            enqueue(
                self.s,
                "handoff_sla",
                {"packet_id": str(packet.packet_id)},
                run_at=packet.sla_expires_at,
                dedupe_key=f"handoff_sla:{packet.packet_id}",
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
            counter = None if quotes_ours else ex.counter_price
            transition(self.s, lead, LeadState.NEGOTIATING, "seller_countered", {"counter": counter})
            if not self.cfg.auto_present_offer:
                escalate(
                    self.s,
                    lead,
                    "offer_response_needed",
                    {"counter": counter, "current": amount, "message": msg.body[:300]},
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
            sent = self._deliver(
                lead,
                thread,
                self._offer_directive(
                    kind="at_ceiling",
                    body=T.at_ceiling(float(offer.amount), self.identity(lead)),
                    instruction=C.at_ceiling_instruction(
                        amount=T.fmt_money(float(offer.amount)), agent=self.identity(lead).agent
                    ),
                    amount=float(offer.amount),
                    ladder=ladder,
                    expiry_phrase=None,
                    stage=LeadState.NEGOTIATING,
                ),
                None,
                fact_sheet(self.s, lead.lead_id),
            )
            # Section 6.4: the ceiling is a human decision. The automation does not present it and
            # does not decide against it either — it asks, with the figure already worked out, so
            # approving is one click rather than a fresh judgement call.
            reason, details = self._ceiling_request(lead, offer, counter, ladder)
            return HandleResult(
                action="escalated",
                lead_id=lead.lead_id,
                stage=lead.state.value,
                escalated=reason,
                details=details,
                sent=sent.body if sent else None,
            )

        if "price_question" in ex.intents:
            sheet = fact_sheet(self.s, lead.lead_id)
            body = T.offer(
                float(offer.amount), offer.expires_at, sheet.get, self.identity(lead), self.cfg.timezone
            )
            expiry_phrase = T.fmt_expiry(offer.expires_at, self.cfg.timezone)
            sent = self._deliver(
                lead,
                thread,
                self._offer_directive(
                    kind="offer_restate",
                    body=body,
                    instruction=C.offer_restate_instruction(
                        amount=T.fmt_money(float(offer.amount)), expiry=expiry_phrase
                    ),
                    amount=float(offer.amount),
                    ladder=ladder,
                    expiry_phrase=expiry_phrase,
                    stage=lead.state,
                ),
                None,
                sheet,
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

    @job_handler("inbound_message")
    def _inbound_message(session: Session, payload: dict[str, Any]) -> None:
        """Webhooks enqueue; the worker runs the loop. Keeps the model's latency off the webhook."""
        from acqbot.transport.registry import get_transport

        msg = InboundMessage.from_payload(payload)
        Conversation(session, get_transport(msg.channel)).handle_inbound(msg)

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
        if lead is None or lead.state not in {LeadState.OFFER_MADE, LeadState.NEGOTIATING}:
            return
        # An expiry nobody is told about is not an expiry, it is a number quietly still on the table.
        # Say it plainly — and never re-offer automatically, which would make the deadline a lie.
        pair = _conversation_for(session, lead)
        if pair is not None:
            pair[0].notify_offer_lapsed(lead, pair[1])
        create_task(
            session,
            lead,
            "offer_expired",
            {"offer_id": str(offer.offer_id), "amount": float(offer.amount)},
        )

    @job_handler("nudge")
    def _nudge(session: Session, payload: dict[str, Any]) -> None:
        lead = session.get(Lead, uuid.UUID(payload["lead_id"]))
        if lead is None:
            return
        pair = _conversation_for(session, lead)
        if pair is None:
            return
        pair[0].nudge(lead, pair[1])

    @job_handler("handoff_sla")
    def _handoff_sla(session: Session, payload: dict[str, Any]) -> None:
        """An agreed deal that nobody picked up is the most expensive thing this system can produce:
        the seller has been told yes and is waiting. It goes back on the queue, loudly."""
        packet = session.get(HandoffPacket, uuid.UUID(payload["packet_id"]))
        if packet is None or packet.claimed_at is not None:
            return
        expires = packet.sla_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires > datetime.now(UTC):
            return
        lead = session.get(Lead, packet.lead_id)
        if lead is None:
            return
        create_task(
            session,
            lead,
            "handoff_sla_expired",
            {
                "packet_id": str(packet.packet_id),
                "agreed_price_aud": packet.packet.get("agreed_price_aud"),
                "sla_expires_at": expires.isoformat(),
            },
        )


register_handlers()
