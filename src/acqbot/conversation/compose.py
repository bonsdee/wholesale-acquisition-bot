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

# What the model is allowed to write in Phase 4. Everything else stays scripted (Phase 5 widens this).
MODEL_DIRECTIVES = frozenset({"ask_field", "clarify", "contradiction", "photos_partial", "verification_wait"})


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
        system = generation_system(
            agent=identity.agent,
            dealership=identity.dealership,
            lmct=identity.lmct,
            channel=channel,
            max_length=gate_ctx.max_length,
        ) + turn_context(
            stage=directive.stage.value,
            outstanding=outstanding,
            sheet=sheet.as_dict(),
            instruction=directive.instruction,
            seller_first_name=seller_first_name,
            channel=channel,
            max_length=gate_ctx.max_length,
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
