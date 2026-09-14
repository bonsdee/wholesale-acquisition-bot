"""Scripted seller demo — runs a whole conversation through the Console transport with no model.

This is Phase 3's proof: ingestion → enrichment → opening → discovery → verification → valuation →
offer → negotiation → acceptance → handoff, with every message passing the validation gate and
every transition landing in state_log. Used by `acqbot demo` and the end-to-end tests.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from acqbot.config import get_settings
from acqbot.conversation.service import Conversation, HandleResult
from acqbot.conversation.transitions import state_history
from acqbot.db import session_scope
from acqbot.ingestion.service import ingest_lead
from acqbot.models import Escalation, HandoffPacket, LadderStep, Lead, Message, Offer
from acqbot.queue.worker import drain
from acqbot.simulator import make_lead
from acqbot.transport.console import ConsoleTransport
from acqbot.transport.registry import reset_console_transport

SELLER_ID = "console-seller"  # default seller external id (the chat command uses it)

# What a cooperative seller says when asked for each field.
ANSWERS: dict[str, str] = {
    "variant": "It's the {variant}",
    "rego": "Rego is {rego}",
    "odometer_km": "It's on {odometer} km right now",
    "service_history": "Full logbook, every service done at the dealer",
    "finance_owing": "No finance, paid it off years ago",
    "write_off_status": "Never been written off, no hail",
    "panel_paint_condition": "Good overall, a couple of small scratches on the rear bumper",
    "mechanical_faults": "None, drives perfectly, no lights on the dash",
    "tyre_condition": "Tyres are good, plenty of tread",
    "keys_count": "Two keys",
    "rego_status": "Yes it's current, registered until March next year",
}

SELLER_SCRIPTS: dict[str, list[str]] = {
    "accept": ["Yes, deal"],
    "negotiate": ["Too low, I was hoping for more", "Can you do a bit better?", "Ok deal"],
    "reject": ["No thanks, not interested"],
    "bot_question": ["Wait, am I talking to a bot?"],
    "legal": ["My lawyer says I should take you to consumer affairs"],
    "silent_pushback": ["hmm", "not sure", "let me think"],
}


@dataclass
class DemoRun:
    lead_id: uuid.UUID
    scenario: str
    seller_script: str
    final_state: str = ""
    transcript: list[dict[str, Any]] = field(default_factory=list)
    state_log: list[dict[str, Any]] = field(default_factory=list)
    offers: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    handoff: dict[str, Any] | None = None
    results: list[dict[str, Any]] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"lead {self.lead_id}  scenario={self.scenario}  seller={self.seller_script}", ""]
        for m in self.transcript:
            who = "SELLER" if m["direction"] == "inbound" else "  BOT "
            body = m["body"] or (f"<{len(m['attachments'] or [])} photos>" if m["attachments"] else "")
            lines.append(f"{who} | {body}")
        lines += ["", "state log:"]
        lines += [f"  {h['from'] or '-':12s} -> {h['to']:12s}  {h['trigger']}" for h in self.state_log]
        if self.offers:
            lines += ["", "offers:"] + [
                f"  {o['step']:8s} ${o['amount']:,.0f}  {o['outcome'] or 'open'}  by {o['by']}"
                for o in self.offers
            ]
        if self.tasks:
            lines += ["", "human queue:"] + [
                f"  {t['reason']}  {'(resolved)' if t['resolved'] else '(open)'}" for t in self.tasks
            ]
        if self.handoff:
            lines += [
                "",
                f"handoff packet: agreed ${self.handoff['agreed_price_aud']:,.0f}, next action {self.handoff['next_action']}, flags {self.handoff['flags']}",
            ]
        lines += ["", f"final state: {self.final_state}"]
        return "\n".join(lines)


def _seller_says(
    console: ConsoleTransport,
    lead_id: uuid.UUID,
    text: str,
    *,
    attachments=None,
    ref: bool = False,
    seller_id: str = SELLER_ID,
) -> HandleResult:
    console.inject(seller_id, text, referral_ref=str(lead_id) if ref else None, attachments=attachments)
    results: list[HandleResult] = []
    for msg in console.poll(datetime(2000, 1, 1, tzinfo=UTC)):
        with session_scope() as s:
            results.append(Conversation(s, console).handle_inbound(msg))
    drain("demo")
    return results[-1]


def _asked_field(lead_id: uuid.UUID) -> str | None:
    with session_scope() as s:
        last = s.scalars(
            select(Message)
            .where(Message.lead_id == lead_id, Message.direction == "outbound")
            .order_by(Message.sent_at.desc(), Message.msg_id.desc())
        ).first()
        return (last.validation_notes or {}).get("asked_field") if last else None


def _human_verification_review(lead_id: uuid.UUID) -> bool:
    """Phase 3 has no vision model: a person reads the odometer off the dash photo. Simulated here."""
    from datetime import UTC as _UTC

    from acqbot.facts.store import fact_sheet, record_fact
    from acqbot.models import FactSource
    from acqbot.queue.jobs import enqueue

    with session_scope() as s:
        task = s.scalars(
            select(Escalation).where(
                Escalation.lead_id == lead_id,
                Escalation.reason == "verification_review",
                Escalation.resolved_at.is_(None),
            )
        ).first()
        if task is None:
            return False
        sheet = fact_sheet(s, lead_id)
        if "odometer_km" in (task.details or {}).get("missing_verified", []):
            record_fact(
                s, lead_id, "odometer_km", sheet.get("odometer_km"), source=FactSource.PHOTO, verified=True
            )
        task.resolved_by, task.resolution, task.resolved_at = (
            "human:demo",
            "odometer confirmed from dash photo",
            datetime.now(_UTC),
        )
        enqueue(
            s, "value_lead", {"lead_id": str(lead_id), "then": "on_priced"}, dedupe_key=f"value:{lead_id}"
        )
    return True


def _state(lead_id: uuid.UUID) -> str:
    with session_scope() as s:
        return s.get(Lead, lead_id).state.value


def run_demo(
    scenario: str = "clean",
    seller_script: str = "negotiate",
    *,
    seed: int = 42,
    echo: bool = False,
    human_presents: bool | None = None,
    max_turns: int = 30,
) -> DemoRun:
    import os

    from acqbot.config import reset_settings_cache

    console = reset_console_transport(echo=echo)
    auto = get_settings().auto_present_offer if human_presents is None else not human_presents
    previous = os.environ.get("ACQBOT_AUTO_PRESENT_OFFER")
    os.environ["ACQBOT_AUTO_PRESENT_OFFER"] = "true" if auto else "false"
    reset_settings_cache()
    try:
        return _run(console, scenario, seller_script, seed=seed, auto=auto, max_turns=max_turns)
    finally:
        if previous is None:
            os.environ.pop("ACQBOT_AUTO_PRESENT_OFFER", None)
        else:
            os.environ["ACQBOT_AUTO_PRESENT_OFFER"] = previous
        reset_settings_cache()


def _run(
    console: ConsoleTransport, scenario: str, seller_script: str, *, seed: int, auto: bool, max_turns: int
) -> DemoRun:

    payload = make_lead(scenario, seed=seed)
    seller_id = f"{SELLER_ID}-{uuid.uuid4().hex[:8]}"  # fresh seller each run so dedupe never bites
    payload["seller"]["platform_id"] = seller_id  # the console external id doubles as the platform id
    with session_scope() as s:
        lead_id = ingest_lead(s, payload).lead_id
    drain("demo")  # enrichment

    def say(text: str, **kw) -> HandleResult:
        return _seller_says(console, lead_id, text, seller_id=seller_id, **kw)

    run = DemoRun(lead_id=lead_id, scenario=scenario, seller_script=seller_script)
    vc = payload["vehicle_claimed"]
    fill = {
        "variant": vc.get("variant") or "",
        "rego": vc.get("rego") or "1AB2CD",
        "odometer": f"{vc['odometer_km'] + 400:,}",
    }
    with session_scope() as s:
        from acqbot.enrichment import catalog

        entry = catalog.find(vc["make"], vc["model"])
        if not fill["variant"] and entry:
            fill["variant"] = entry.variants[0]

    # Seller opens the thread from the m.me link.
    r = say("Hi, saw you're keen on my car", ref=True)
    run.results.append(r.__dict__)

    photos = [{"type": "image", "url": f"https://example.invalid/photo/{lead_id}/{i}.jpg"} for i in range(8)]
    script = list(SELLER_SCRIPTS[seller_script])
    for _ in range(max_turns):
        state = _state(lead_id)
        if state in {"HANDOFF", "ARCHIVED", "REJECTED", "HUMAN", "TERMINATED", "ACCEPTED"}:
            break
        asked = _asked_field(lead_id)
        if state in {"OFFER_MADE", "NEGOTIATING"}:
            if not script:
                break
            r = say(script.pop(0))
        elif state == "PRICED":
            if auto:
                break  # nothing to say; automation should already have presented
            with session_scope() as s:
                lead = s.get(Lead, lead_id)
                Conversation(s, console).present_offer(lead, LadderStep.OPENING, presented_by="human:demo")
            drain("demo")
            continue
        elif state == "VERIFICATION":
            drain("demo")
            if _state(lead_id) == "VERIFICATION":
                if not _human_verification_review(lead_id):
                    break  # waiting on something the demo cannot simulate
                drain("demo")
            continue
        elif asked and asked.startswith("contradiction:"):
            r = say("Ah you're right, go with what the check says")
        elif asked == "photos":
            r = say("Here you go", attachments=photos)
        elif asked in ANSWERS:
            r = say(ANSWERS[asked].format(**fill))
        elif seller_script in {"bot_question", "legal", "silent_pushback"} and script:
            r = say(script.pop(0))
        else:
            r = say("Sure")
        run.results.append(r.__dict__)
        if seller_script in {"bot_question", "legal"} and script and state == "DISCOVERY":
            r = say(script.pop(0))
            run.results.append(r.__dict__)

    with session_scope() as s:
        run.final_state = s.get(Lead, lead_id).state.value
        msgs = s.scalars(
            select(Message).where(Message.lead_id == lead_id).order_by(Message.sent_at, Message.msg_id)
        )
        run.transcript = [
            {
                "direction": m.direction.value,
                "body": m.body,
                "attachments": m.attachments,
                "template": (m.validation_notes or {}).get("template"),
            }
            for m in msgs
        ]
        run.state_log = [
            {
                "from": h.from_state.value if h.from_state else None,
                "to": h.to_state.value,
                "trigger": h.trigger,
            }
            for h in state_history(s, lead_id)
        ]
        run.offers = [
            {
                "step": o.ladder_step.value,
                "amount": float(o.amount),
                "outcome": o.outcome.value if o.outcome else None,
                "by": o.presented_by,
            }
            for o in s.scalars(select(Offer).where(Offer.lead_id == lead_id).order_by(Offer.presented_at))
        ]
        run.tasks = [
            {"reason": e.reason, "resolved": e.resolved_at is not None, "details": e.details}
            for e in s.scalars(
                select(Escalation).where(Escalation.lead_id == lead_id).order_by(Escalation.at)
            )
        ]
        packet = s.scalars(select(HandoffPacket).where(HandoffPacket.lead_id == lead_id)).first()
        run.handoff = packet.packet if packet else None
    return run
