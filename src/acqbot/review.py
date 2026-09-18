"""Prompt review harness — run the awkward sellers past the model and score what came back.

    uv run acqbot review --model anthropic --out review.md

One command, every persona in `personas.py`, one markdown report. The report is built for reading
in ten minutes and acting on: it leads with what went wrong, and for every message the gate
rejected it quotes the draft the model wanted to send. That quote is the whole point — a gate
rejection is the cheapest possible bug report on a prompt, because the bad message was caught
before a seller saw it.

Nothing here decides whether the system is correct; the test suite does that. This decides whether
the WORDING is right, which is a judgement only a person can make. The harness gets it in front of
that person in a form they can skim.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from acqbot.conversation.service import Conversation
from acqbot.conversation.transitions import state_history
from acqbot.db import session_scope
from acqbot.facts.store import fact_sheet
from acqbot.ingestion.service import ingest_lead
from acqbot.models import Escalation, HandoffPacket, LadderStep, Lead, Message, ModelCall, Offer
from acqbot.personas import ALL, Persona
from acqbot.queue.worker import drain
from acqbot.review_quality import Finding
from acqbot.review_quality import findings as quality_findings
from acqbot.simulator import make_lead
from acqbot.transport.registry import reset_console_transport

# Published per-million-token prices, Sep 2026. Only used to put a dollar sign on the report —
# check your own plan before quoting these to anyone.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-fable-5-1": (10.0, 50.0),
}
ENDED = {"HANDOFF", "ARCHIVED", "REJECTED", "HUMAN", "TERMINATED", "ACCEPTED"}


def price_for(model: str, input_tokens: int, output_tokens: int) -> float:
    for prefix, (pin, pout) in PRICES_PER_MTOK.items():
        if model.startswith(prefix):
            return (input_tokens * pin + output_tokens * pout) / 1_000_000
    return 0.0


@dataclass
class Turn:
    direction: str
    body: str
    template: str | None = None
    generator: str | None = None
    model_version: str | None = None
    attachments: int = 0
    asked_field: str | None = None


@dataclass
class Rejection:
    """A draft the gate refused. The most useful line in the whole report."""

    template: str
    attempt: int
    violations: list[str]
    draft: str


@dataclass
class ConversationReview:
    persona: str
    why: str
    lead_id: uuid.UUID
    vehicle: str
    final_state: str = ""
    turns: list[Turn] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    missing_facts: list[str] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    templates_used: set[str] = field(default_factory=set)
    rejections: list[Rejection] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)  # templates that fell back to the script
    # How the conversation READS, as against whether it worked — see review_quality.py. Kept apart
    # from `problems` because these do not mean the run failed: they mean a person should look.
    quality: list[Finding] = field(default_factory=list)
    model_errors: list[str] = field(default_factory=list)
    offers: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_aud: float = 0.0
    failed_calls: int = 0
    questions_asked: int = 0
    needs_model: bool = False
    problems: list[str] = field(default_factory=list)  # expectation failures, plain English

    @property
    def ok(self) -> bool:
        return not self.problems and not self.rejections and not self.fallbacks and not self.model_errors


@dataclass
class Review:
    model: str
    started_at: datetime
    conversations: list[ConversationReview] = field(default_factory=list)
    aborted: str | None = None  # set when the run stopped early because the model stopped answering

    @property
    def cost_aud(self) -> float:
        return sum(c.cost_aud for c in self.conversations)

    @property
    def calls(self) -> int:
        return sum(c.calls for c in self.conversations)


# ---------------------------------------------------------------------------- running one persona


def _asked(lead_id: uuid.UUID) -> tuple[str | None, str | None]:
    """(asked_field, template) from the last outbound message."""
    with session_scope() as s:
        last = s.scalars(
            select(Message)
            .where(Message.lead_id == lead_id, Message.direction == "outbound")
            .order_by(Message.sent_at.desc(), Message.msg_id.desc())
        ).first()
        if last is None:
            return None, None
        notes = last.validation_notes or {}
        return notes.get("asked_field"), notes.get("template")


def _state(lead_id: uuid.UUID) -> str:
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        return lead.state.value if lead else "GONE"


def _verification_review(lead_id: uuid.UUID) -> bool:
    """Stand in for the person who reads the odometer off the dash photo (Phase 4 has no vision)."""
    from acqbot.facts.store import record_fact
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
            "human:review",
            "odometer confirmed from dash photo",
            datetime.now(UTC),
        )
        enqueue(
            s, "value_lead", {"lead_id": str(lead_id), "then": "on_priced"}, dedupe_key=f"value:{lead_id}"
        )
    return True


def run_persona(
    persona: Persona, *, scenario: str = "clean", seed: int = 42, auto_offer: bool = True
) -> ConversationReview:
    console = reset_console_transport(echo=False)
    payload = make_lead(scenario, seed=seed)
    seller_id = f"review-{persona.name}-{uuid.uuid4().hex[:6]}"
    payload["seller"]["platform_id"] = seller_id
    with session_scope() as s:
        lead_id = ingest_lead(s, payload).lead_id
    drain("review")  # enrichment

    vc = payload["vehicle_claimed"]
    from acqbot.enrichment import catalog

    entry = catalog.find(vc["make"], vc["model"])
    fill = {
        "variant": vc.get("variant") or (entry.variants[0] if entry else "base"),
        "rego": vc.get("rego") or "1AB2CD",
        "odometer": f"{vc['odometer_km'] + 400:,}",
    }
    review = ConversationReview(
        persona=persona.name,
        why=persona.why,
        lead_id=lead_id,
        vehicle=f"{vc['year']} {vc['make']} {vc['model']}",
        needs_model=persona.needs_model,
    )

    def say(text: str, attachments=None) -> None:
        console.inject(seller_id, text, referral_ref=None, attachments=attachments)
        for msg in console.poll(datetime(2000, 1, 1, tzinfo=UTC)):
            with session_scope() as s:
                Conversation(s, console).handle_inbound(msg)
        drain("review")

    photos = [{"type": "image", "url": f"https://example.invalid/{lead_id}/{i}.jpg"} for i in range(8)]
    interjections = dict(persona.interjections)
    offer_script = list(persona.offer_script)
    asked_counts: dict[str, int] = {}
    inbound_turns = 0

    # The seller opens the thread from the m.me link.
    console.inject(seller_id, persona.opener, referral_ref=str(lead_id))
    for msg in console.poll(datetime(2000, 1, 1, tzinfo=UTC)):
        with session_scope() as s:
            Conversation(s, console).handle_inbound(msg)
    drain("review")
    inbound_turns += 1

    for _ in range(persona.max_turns):
        state = _state(lead_id)
        if state in ENDED:
            break
        if state == "VERIFICATION":
            drain("review")
            if _state(lead_id) == "VERIFICATION" and not _verification_review(lead_id):
                break
            drain("review")
            continue
        if state == "PRICED":
            if auto_offer:
                drain("review")
                if _state(lead_id) == "PRICED":
                    break  # automation should have presented by now; something is wrong
                continue
            with session_scope() as s:
                lead = s.get(Lead, lead_id)
                Conversation(s, console).present_offer(lead, LadderStep.OPENING, presented_by="human:review")
            drain("review")
            continue

        inbound_turns += 1
        if state in {"OFFER_MADE", "NEGOTIATING"}:
            if not offer_script:
                break
            line = offer_script.pop(0)
            if "{offer" in line:
                with session_scope() as s:
                    current = Conversation(s, console).current_offer(s.get(Lead, lead_id))
                    line = line.format(offer=int(current.amount) if current else 0)
            say(line)
            continue

        if inbound_turns in interjections:
            say(interjections[inbound_turns])
            continue

        asked_field, _template = _asked(lead_id)
        if asked_field == "photos":
            say("Here you go", attachments=photos)
            continue
        if asked_field and asked_field.startswith("contradiction:"):
            say("Ah you're right, go with what the check says")
            continue
        if asked_field:
            nth = asked_counts.get(asked_field, 0)
            asked_counts[asked_field] = nth + 1
            say(persona.answer_for_any(asked_field, nth).format(**fill))
            continue
        say("Ok")

    _collect(review, persona)
    return review


def _collect(review: ConversationReview, persona: Persona) -> None:
    lead_id = review.lead_id
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        review.final_state = lead.state.value if lead else "GONE"
        msgs = list(
            s.scalars(
                select(Message).where(Message.lead_id == lead_id).order_by(Message.sent_at, Message.msg_id)
            )
        )
        by_id = {str(c.call_id): c for c in s.scalars(select(ModelCall).where(ModelCall.lead_id == lead_id))}
        for m in msgs:
            notes = m.validation_notes or {}
            review.turns.append(
                Turn(
                    direction=m.direction.value,
                    body=m.body,
                    template=notes.get("template"),
                    generator=notes.get("generator"),
                    model_version=m.model_version,
                    attachments=len(m.attachments or []),
                    asked_field=notes.get("asked_field"),
                )
            )
            if m.direction.value != "outbound":
                continue
            template = notes.get("template") or "?"
            review.templates_used.add(template)
            if notes.get("generator") == "template_fallback":
                review.fallbacks.append(template)
            for i, attempt in enumerate(notes.get("attempts") or []):
                if attempt.get("error"):
                    review.model_errors.append(f"{template}: {attempt['error']}")
                gate = attempt.get("gate") or {}
                if gate and not gate.get("ok", True):
                    call = by_id.get(attempt.get("call_id", ""))
                    draft = ((call.response or {}).get("parsed") or {}).get("message", "") if call else ""
                    review.rejections.append(
                        Rejection(
                            template=template,
                            attempt=i + 1,
                            violations=list(gate.get("violations") or []),
                            draft=draft,
                        )
                    )
        for c in s.scalars(select(ModelCall).where(ModelCall.lead_id == lead_id)):
            review.calls += 1
            review.input_tokens += c.input_tokens or 0
            review.output_tokens += c.output_tokens or 0
            review.latency_ms += c.latency_ms or 0
            review.cost_aud += price_for(c.model, c.input_tokens or 0, c.output_tokens or 0)
            if c.error:
                review.failed_calls += 1
            if c.error and not any(c.error in e for e in review.model_errors):
                review.model_errors.append(f"{c.purpose}: {c.error}")
        sheet = fact_sheet(s, lead_id)
        review.facts = {k: v.value for k, v in sheet.facts.items()}
        review.escalations = [
            e.reason
            for e in s.scalars(
                select(Escalation).where(Escalation.lead_id == lead_id).order_by(Escalation.at)
            )
        ]
        review.offers = [
            {
                "step": o.ladder_step.value,
                "amount": float(o.amount),
                "outcome": o.outcome.value if o.outcome else None,
            }
            for o in s.scalars(select(Offer).where(Offer.lead_id == lead_id).order_by(Offer.presented_at))
        ]
        review.questions_asked = sum(
            1
            for m in msgs
            if m.direction.value == "outbound" and (m.validation_notes or {}).get("asked_field")
        )
        review.missing_facts = sorted(persona.expect_facts - set(review.facts))
        has_packet = (
            s.scalars(select(HandoffPacket).where(HandoffPacket.lead_id == lead_id)).first() is not None
        )
        turns = len(state_history(s, lead_id))

    # --- score it ---
    if review.final_state not in persona.expect_final:
        review.problems.append(
            f"ended in {review.final_state}, expected {' or '.join(sorted(persona.expect_final))}"
        )
    if review.missing_facts:
        review.problems.append("never collected: " + ", ".join(review.missing_facts))
    missing_templates = sorted(persona.expect_templates - review.templates_used)
    if missing_templates:
        review.problems.append("expected but never sent: " + ", ".join(missing_templates))
    if persona.expect_escalation and persona.expect_escalation not in review.escalations:
        review.problems.append(
            f"expected escalation '{persona.expect_escalation}', got "
            + (", ".join(review.escalations) or "none")
        )
    if not persona.expect_escalation:
        unexpected = [e for e in review.escalations if e.startswith("model_flagged:")]
        if unexpected:
            review.problems.append("model escalated unnecessarily: " + ", ".join(unexpected))
    if review.final_state == "HANDOFF" and not has_packet:
        review.problems.append("reached HANDOFF with no deal packet")
    if turns == 0:
        review.problems.append("no state transitions recorded")
    if persona.expect_max_questions and review.questions_asked > persona.expect_max_questions:
        review.problems.append(
            f"took {review.questions_asked} questions, expected at most {persona.expect_max_questions} "
            "— a multi-field answer was probably not absorbed"
        )

    # --- and how it reads
    review.quality = quality_findings(review.turns)
    return None


def run_review(
    personas: list[Persona] | None = None,
    *,
    scenario: str = "clean",
    seed: int = 42,
    model_label: str = "",
    auto_offer: bool = True,
    on_persona: Any = None,
) -> Review:
    """Run every persona. `auto_offer` lets the automation traverse the ladder, so the negotiation
    wording is reviewed too; without it every counter escalates and the run stops at the opening."""
    import os

    from acqbot.config import reset_settings_cache

    previous = os.environ.get("ACQBOT_AUTO_PRESENT_OFFER")
    os.environ["ACQBOT_AUTO_PRESENT_OFFER"] = "true" if auto_offer else "false"
    reset_settings_cache()
    review = Review(model=model_label, started_at=datetime.now(UTC))
    try:
        for persona in personas or list(ALL):
            result = run_persona(persona, scenario=scenario, seed=seed, auto_offer=auto_offer)
            review.conversations.append(result)
            if on_persona is not None:
                on_persona(result)
            # Every call failed: the key died, the balance ran out, or the API is down. Fourteen
            # more conversations of the same would only produce a longer report about nothing.
            if result.calls and result.failed_calls == result.calls:
                review.aborted = f"stopped after '{result.persona}': every model call failed — " + (
                    result.model_errors[0] if result.model_errors else "no detail recorded"
                )
                break
    finally:
        if previous is None:
            os.environ.pop("ACQBOT_AUTO_PRESENT_OFFER", None)
        else:
            os.environ["ACQBOT_AUTO_PRESENT_OFFER"] = previous
        reset_settings_cache()
    return review


# ---------------------------------------------------------------------------------- the report


def _fmt_turn(t: Turn) -> str:
    who = "seller" if t.direction == "inbound" else "bot"
    body = t.body or (f"[{t.attachments} photos]" if t.attachments else "[empty]")
    tag = ""
    if t.direction == "outbound":
        mark = {"model": "M", "template": "T", "template_fallback": "F"}.get(t.generator or "", "?")
        tag = f" `{mark}·{t.template}`"
    return f"**{who}**{tag}\n> {body.replace(chr(10), chr(10) + '> ')}"


def render_markdown(review: Review) -> str:
    out: list[str] = []
    is_real = "claude-" in review.model
    ok = [c for c in review.conversations if c.ok]
    out.append("# Prompt review")
    out.append("")
    out.append(
        f"{review.started_at:%d %b %Y %H:%M} UTC · model `{review.model}` · "
        f"{len(ok)}/{len(review.conversations)} clean · {review.calls} calls · "
        f"US${review.cost_aud:.2f}"
    )
    out.append("")
    if review.aborted:
        out.append(f"> **Run did not finish.** {review.aborted}")
        out.append("")
    out.append("| Seller | Ended | Questions | Problems | Reads badly | Gate rejections | Fallbacks | Cost |")
    out.append("|---|---|---|---|---|---|---|---|")
    for c in review.conversations:
        flag = "✅" if c.ok else ("➖" if c.needs_model and not is_real else "⚠️")
        name = c.persona + (" ¹" if c.needs_model else "")
        out.append(
            f"| {flag} {name} | {c.final_state} | {c.questions_asked} | {len(c.problems)} | "
            f"{len(c.quality) or '—'} | {len(c.rejections)} | {len(c.fallbacks)} | ${c.cost_aud:.3f} |"
        )
    out.append("")
    if any(c.needs_model for c in review.conversations):
        out.append(
            "¹ needs a real model — the rule-based extractor cannot read a typo, a hedge or four "
            "answers in one sentence, so these are expected to fail on `--model fake`."
            + ("" if is_real else " **This run used no model, so ➖ rows are not failures.**")
        )
        out.append("")

    rejections = [(c, r) for c in review.conversations for r in c.rejections]
    if rejections:
        out.append("## Drafts the gate refused")
        out.append("")
        out.append(
            "Each of these is a message the model wanted to send and the gate stopped. This is the "
            "list to read first — every entry is either a prompt that needs a line, or a gate rule "
            "that is too tight."
        )
        out.append("")
        for c, r in rejections:
            out.append(f"**{c.persona}** · `{r.template}` · attempt {r.attempt} · {'; '.join(r.violations)}")
            out.append(f"> {r.draft or '(draft not recorded)'}")
            out.append("")

    problems = [c for c in review.conversations if c.problems and (is_real or not c.needs_model)]
    if problems:
        out.append("## Conversations that did not do what they should")
        out.append("")
        for c in problems:
            out.append(f"**{c.persona}** — {c.why}")
            for p in c.problems:
                out.append(f"- {p}")
            out.append("")

    quality = [c for c in review.conversations if c.quality]
    if quality:
        out.append("## Conversations that worked but read badly")
        out.append("")
        out.append(
            "Nothing here broke a rule — the gate passed every one of these and the machinery did "
            "its job. They are the things a seller would notice and a checklist would not: the same "
            "opener three times, two questions in one message, a field asked for a third time. Only "
            "messages the model wrote are considered; the scripted wording is fixed by design."
        )
        out.append("")
        for c in quality:
            out.append(f"**{c.persona}** — {c.why}")
            for f in c.quality:
                out.append(f"- *{f.check}* — {f.detail}")
                if f.quote:
                    out.append(f"  > {f.quote.strip()[:300]}")
            out.append("")

    fallbacks = [c for c in review.conversations if c.fallbacks or c.model_errors]
    if fallbacks:
        out.append("## Fell back to the scripted wording")
        out.append("")
        for c in fallbacks:
            detail = ", ".join(c.fallbacks) or "—"
            out.append(f"- **{c.persona}**: {detail}")
            for e in c.model_errors:
                out.append(f"  - error: {e}")
        out.append("")

    out.append("## Transcripts")
    out.append("")
    out.append("`M` written by the model · `T` scripted · `F` model tried and the script was used instead")
    out.append("")
    for c in review.conversations:
        out.append(f"<details><summary><b>{c.persona}</b> — {c.why}</summary>")
        out.append("")
        out.append(f"{c.vehicle} · ended {c.final_state} · {c.calls} calls · ${c.cost_aud:.3f}")
        out.append("")
        for t in c.turns:
            out.append(_fmt_turn(t))
            out.append("")
        if c.offers:
            out.append(
                "Offers: "
                + " → ".join(f"${o['amount']:,.0f} ({o['step']}, {o['outcome'] or 'open'})" for o in c.offers)
            )
            out.append("")
        if c.escalations:
            out.append("Human queue: " + ", ".join(c.escalations))
            out.append("")
        out.append("</details>")
        out.append("")

    out.append("---")
    out.append("")
    out.append(
        f"Tokens: {sum(c.input_tokens for c in review.conversations):,} in, "
        f"{sum(c.output_tokens for c in review.conversations):,} out. "
        f"Prices are the published Sep 2026 rates — check your own plan."
    )
    return "\n".join(out)
