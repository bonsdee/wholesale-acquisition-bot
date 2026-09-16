"""Stages 4–6 with a model — assemble context, generate, validate, retry, fall back.

The planner (in service.py) decides *what* the next message does and expresses it as a
Directive that already carries the scripted wording. With no model configured that wording is
sent as-is (Phase 3). With a model, the Directive becomes an instruction, the model writes the
message, the gate checks it, one rewrite is allowed with the violations fed back, and if that
also fails the scripted wording goes out instead. The model can therefore never block a
conversation and never send an unchecked sentence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from acqbot.config import Settings
from acqbot.conversation import templates as T
from acqbot.conversation.gate import GateContext, validate
from acqbot.conversation.history import build_history
from acqbot.facts.fields import FieldSpec, spec_for
from acqbot.facts.store import FactSheet
from acqbot.llm.client import ModelClient, ModelRequest
from acqbot.llm.prompts import PROMPT_VERSION, gate_feedback, generation_system, turn_context
from acqbot.llm.schemas import GENERATION_SCHEMA, parse_generation
from acqbot.llm.trace import traced_call
from acqbot.models import Lead, LeadState, Thread

log = logging.getLogger("acqbot.conversation")

# What the model is allowed to write. Phase 4 was discovery only; Phase 5 adds the offer messages,
# which the model may WORD but never CHOOSE: the amount, the step and the expiry are decided in code
# and pinned by `sole_figure` and `must_include` on the gate context. Everything outside this set —
# the opening disclosure, the bot-question answer, the handoff close — stays scripted.
MODEL_DIRECTIVES = frozenset(
    {
        "ask_field",
        "clarify",
        "contradiction",
        "photos_partial",
        "verification_wait",
        "offer",
        "concession",
        "offer_restate",
        "at_ceiling",
    }
)


@dataclass
class Directive:
    kind: str
    template_id: str
    body: str  # scripted wording: the message itself in template mode, the fallback in model mode
    stage: LeadState
    asked_field: str | None = None
    allowed_years: set[int] = field(default_factory=set)
    allowed_odometers: set[int] = field(default_factory=set)
    instruction: str = ""
    template_vars: dict[str, Any] = field(default_factory=dict)
    # Phase 5 — what the gate pins when the model is wording an offer.
    ladder: dict[str, float] | None = None
    current_offer: float | None = None
    sole_figure: float | None = None
    must_include: list[str] = field(default_factory=list)


@dataclass
class ComposeResult:
    body: str
    generator: str  # template | model | template_fallback
    model_version: str
    notes: dict[str, Any] = field(default_factory=dict)
    escalate_reason: str | None = None  # set when the model asked for a person instead of a message


# ------------------------------------------------------------------------------ instructions


def _choices(spec: FieldSpec) -> str:
    return f" Offer the options: {' / '.join(spec.choices)}." if spec.choices else ""


def ask_field_instruction(
    spec: FieldSpec,
    *,
    recorded: list[str],
    seller_question: str | None,
    deferred: str | None,
    agent: str,
    only_remaining: bool = False,
) -> str:
    parts = [f'Ask for {spec.label.lower()}: "{spec.ask}" Put it in your own words.{_choices(spec)}']
    if recorded:
        parts.append(f"Acknowledge in a few words what they just gave you ({', '.join(recorded)}) — no fuss.")
    if deferred and only_remaining:
        parts.append(
            f"They said they'd come back to you on {deferred}, and it is the last thing needed: say that's fine, "
            "ask them to send it through when they can, and leave it there. No pressure, no deadline."
        )
    elif deferred:
        parts.append(
            f"They said they'd come back to you on {deferred}; don't push it, move on to this question."
        )
    if seller_question:
        parts.append(
            f'They asked: "{seller_question}". Answer it only from the process notes; if it is not covered, '
            f"say {agent} will confirm. Then ask the question."
        )
    return " ".join(parts)


def clarify_instruction(
    spec: FieldSpec, *, low_confidence: str | None, seller_question: str | None, agent: str
) -> str:
    why = (
        f'Their answer ("{low_confidence}") was too vague to record.'
        if low_confidence
        else "Their last message did not give a usable answer."
    )
    hint = {
        "odometer_km": "Ask for the exact reading on the dash, in km.",
        "keys_count": "Ask for the number of keys.",
        "rego": "Ask for the plate as written on the car, or the 17-character VIN.",
    }.get(spec.key, f"Ask again, more specifically.{_choices(spec)}")
    parts = [f"About {spec.label.lower()}: {why} {hint} Apologise at most once, briefly."]
    if seller_question:
        parts.append(
            f'They also asked: "{seller_question}" — answer from the process notes or defer to {agent}.'
        )
    return " ".join(parts)


def contradiction_instruction(field_key: str, claimed: Any, actual: Any, source: str) -> str:
    label = (spec_for(field_key).label if spec_for(field_key) else field_key).lower()
    src = {
        "ppsr": "the PPSR check",
        "vin": "the VIN decode",
        "rego": "the registration check",
        "photo": "the photos",
        "inspection": "the inspection",
    }.get(source, source)
    return (
        f"Verified data disagrees with what we were told about {label}: the listing or seller said {claimed}; "
        f"{src} shows {actual}. Ask which is right, without accusing anyone. You may state both values "
        f"exactly as given here and nothing else numeric."
    )


def photos_partial_instruction(have: int, need: int) -> str:
    return (
        f"They sent {have} photo{'s' if have != 1 else ''}; {need} are needed in total — the six exterior "
        f"angles, the interior, and the dash with the ignition on so the odometer shows. Thank them briefly "
        f"and ask for the rest."
    )


def verification_wait_instruction() -> str:
    return (
        "Everything needed has been collected. Say you're running the checks now and will come back with a "
        "firm number shortly. Do not estimate, hint at, or qualify any value, and give no timeframe beyond "
        '"shortly". No question this time — end there.'
    )


# ---------------------------------------------------------------------- offers (Phase 5)
#
# Section 6.4 puts the entire universe of permitted figures in the ladder and says the constraint is
# enforced in code at the gate, "not by instruction in the prompt". These instructions therefore tell
# the model what the message has to DO; they are not what stops it inventing a number.


def offer_instruction(*, amount: str, expiry: str, vehicle: str, first_offer: bool) -> str:
    opening = (
        f"This is the first offer on the {vehicle}. Lead with it — do not build up to it."
        if first_offer
        else "They pushed back and this is the improved number. Acknowledge the push in a few words, "
        "then give it. Do not apologise for the earlier figure and do not say it is your last."
    )
    return (
        f"{opening} State the amount as exactly {amount} and say it is subject to inspection. "
        f"Say plainly that it is open until exactly {expiry}, and that the valuation inputs move after "
        f"that, which is why it lapses — this is true, so say it without drama. "
        f"Close by asking whether they want to go ahead. No other figure, no range, no hint at what "
        f"else might be possible, nothing about other buyers, and no deadline other than the one given."
    )


def offer_restate_instruction(*, amount: str, expiry: str) -> str:
    return (
        f"They asked about the price again. Restate the same offer — exactly {amount}, subject to "
        f"inspection, open until exactly {expiry} — without treating the question as a negotiation and "
        f"without re-explaining the whole thing. Short. Then ask if they want to go ahead."
    )


def at_ceiling_instruction(*, amount: str, agent: str) -> str:
    return (
        f"They have countered again and the automated ladder is finished: exactly {amount} is as far as "
        f"it goes without a person. Say the number stands, say honestly that going further is not yours "
        f"to decide and {agent} will come back to them on it, and leave it there. Do not hint that more "
        f"is available, do not promise more, do not ask them to counter again, and give no timeframe."
    )


# ------------------------------------------------------------------------------ composer


class Composer:
    def __init__(self, session: Session, client: ModelClient | None, settings: Settings) -> None:
        self.s = session
        self.client = client
        self.cfg = settings

    def compose(
        self,
        lead: Lead,
        thread: Thread,
        directive: Directive,
        *,
        sheet: FactSheet,
        outstanding: list[str],
        identity: T.Identity,
        seller_first_name: str,
        channel: str,
        gate_ctx: GateContext,
    ) -> ComposeResult:
        if self.client is None or directive.kind not in MODEL_DIRECTIVES:
            return ComposeResult(body=directive.body, generator="template", model_version=T.TEMPLATE_VERSION)

        history = build_history(self.s, thread, lead, client=self.client, settings=self.cfg)
        # The one figure the writer is given, or none at all. `sole_figure` is set by the planner
        # only for an offer message, so the same flag that pins the gate also picks the prompt.
        offer_amount = T.fmt_money(directive.sole_figure) if directive.sole_figure is not None else None
        system = generation_system(
            agent=identity.agent,
            dealership=identity.dealership,
            lmct=identity.lmct,
            channel=channel,
            max_length=gate_ctx.max_length,
            presenting_offer=offer_amount is not None,
        ) + turn_context(
            stage=directive.stage.value,
            outstanding=outstanding,
            sheet=sheet.as_dict(),
            instruction=directive.instruction,
            seller_first_name=seller_first_name,
            channel=channel,
            max_length=gate_ctx.max_length,
            offer_amount=offer_amount,
        )
        messages = list(history.messages)
        notes: dict[str, Any] = {
            "attempts": [],
            "history_turns": len(history.turns),
            "summary_used": bool(history.summary),
        }
        model_version = f"{self.cfg.conversation_model}|{PROMPT_VERSION}"

        for attempt in range(1 + max(0, self.cfg.llm_gate_retries)):
            req = ModelRequest(
                purpose="generate",
                model=self.cfg.conversation_model,
                system=system,
                messages=messages,
                schema=GENERATION_SCHEMA,
                max_tokens=self.cfg.llm_max_output_tokens,
                effort=self.cfg.llm_effort,
                meta={"fallback_body": directive.body, "stage": directive.stage.value, "attempt": attempt},
            )
            resp, row = traced_call(
                self.s,
                self.client,
                req,
                prompt_version=PROMPT_VERSION,
                lead_id=lead.lead_id,
                thread_id=thread.thread_id,
            )
            record: dict[str, Any] = {"call_id": str(row.call_id)}
            notes["attempts"].append(record)
            if resp is None:
                record["error"] = row.error
                break  # a failed call is not worth a second one this turn; the template goes out
            out = parse_generation(resp.parsed)
            if out is None:
                record["error"] = f"unparseable output (stop_reason={resp.stop_reason})"
                continue
            record.update(
                {"proposed_state": out.proposed_state, "confidence": out.confidence, "escalate": out.escalate}
            )
            if out.proposed_state and out.proposed_state != directive.stage.value:
                record["state_disagreement"] = (
                    f"model {out.proposed_state} vs machine {directive.stage.value}"
                )
            if out.escalate:
                return ComposeResult(
                    body="",
                    generator="model",
                    model_version=resp.model + "|" + PROMPT_VERSION,
                    notes=notes,
                    escalate_reason=out.escalate_reason or "other",
                )
            gate = validate(out.message, gate_ctx)
            record["gate"] = gate.as_dict()
            if gate.ok:
                notes["prompt_hash"] = row.prompt_hash
                return ComposeResult(
                    body=out.message,
                    generator="model",
                    model_version=resp.model + "|" + PROMPT_VERSION,
                    notes=notes,
                )
            log.info(
                "gate rejected model draft for lead %s (attempt %d): %s",
                lead.lead_id,
                attempt,
                gate.violations,
            )
            messages = messages + [
                {"role": "assistant", "content": out.message},
                {"role": "user", "content": gate_feedback(gate.violations)},
            ]

        notes["fallback"] = directive.template_id
        notes["model_tried"] = model_version
        return ComposeResult(
            body=directive.body, generator="template_fallback", model_version=T.TEMPLATE_VERSION, notes=notes
        )
