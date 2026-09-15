"""Prompt architecture — Section 7.2, as pure functions of the state the code already holds.

Layout per call:
    SYSTEM   identity, disclosure stance, objective, hard constraints, tone, process notes,
             then the context for this turn (stage, outstanding fields, fact sheet, valuation
             NOT RELEASED, the instruction from the planner, channel limits)
    HISTORY  rolling summary + the last N turns verbatim, as user/assistant messages

The valuation never appears here in Phase 4 — there is no parameter for it. A number the model
does not hold cannot be leaked. PROMPT_VERSION is recorded with every call and every outbound
message; bump it whenever any wording below changes, including the process notes.
"""

from __future__ import annotations

import json
from typing import Any

from acqbot.facts.fields import DISCOVERY_REQUIRED, spec_for
from acqbot.llm.knowledge import process_notes
from acqbot.llm.schemas import FIELD_WIRE

PROMPT_VERSION = "prompt:v1"


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------------------------- extraction

EXTRACTION_SYSTEM = """\
You extract structured facts from a private car seller's chat message to a dealership buyer.
Report only what the seller actually says in the LATEST message. Never guess, never fill in from
general knowledge, never invent a value the seller did not give. Australian context: kilometres,
"rego" means registration, plates look like 1AB2CD or ABC123.

Fields and the exact value format for each (value is always a string):
{fields}

Rules:
- Report a field only when the latest message states it. A plain answer to the assistant's last
  question counts as stating that field. Facts from earlier turns are already recorded — do not
  repeat them unless the seller restates or corrects them now.
- confidence: 1.0 for a plain statement; 0.7 for "about", "roughly", "I think"; 0.5 or lower
  when the message is ambiguous or could be read two ways.
- answered_pending: true if the message answers the assistant's last question (even partially).
- intents: accept (agrees to an offer), reject (turns an offer down), counter (asks for more or
  names a higher figure), price_question (asks what the dealership would pay or what the car is
  worth), question (asks the assistant something else), defer (will check / get back later),
  stop (asks not to be contacted again).
- flags — only when the latest message itself clearly shows it: legal (lawyers, ombudsman,
  consumer affairs, a dispute or threat of one), deceased_estate (the owner has died; estate,
  executor, probate), distress (financial or personal crisis, desperation), minor_or_no_authority
  (under 18, or not the owner / no authority to sell), human_question (asks whether they are
  talking to a bot, a machine or a real person), hostile (abuse or anger at the assistant or the
  dealership). Do not flag ordinary frustration or a hard bargain.
- counter_price_aud: a dollar amount the seller says they want or would accept; otherwise null.
- phone: a phone number in the message; otherwise null.
- seller_question: a question the seller asks the assistant, paraphrased in under 20 words;
  otherwise null.
- notes: anything else about the car or the sale worth passing to the buyer ("second owner",
  "timing belt done at 150k", "moving interstate next month"). Short phrases, at most 6, none if
  nothing is said."""


def extraction_system() -> str:
    lines = [f"- {k}: {v}" for k, v in FIELD_WIRE.items()]
    return EXTRACTION_SYSTEM.format(fields="\n".join(lines))


def extraction_messages(
    *,
    vehicle: str,
    pending_field: str | None,
    last_question: str | None,
    recent_turns: list[tuple[str, str]],
    body: str,
    image_count: int,
) -> list[dict[str, str]]:
    spec = spec_for(pending_field) if pending_field else None
    if pending_field and pending_field.startswith("contradiction:"):
        asked = f"which of two conflicting values is right for {pending_field.split(':', 1)[1]}"
    elif spec:
        asked = f'{spec.key} — "{last_question or spec.ask}"'
    elif pending_field:
        asked = f'{pending_field} — "{last_question or ""}"'
    else:
        asked = "(nothing specific)"
    turns = "\n".join(f"  {role}: {text}" for role, text in recent_turns) or "  (none)"
    content = (
        f"Vehicle (from the listing): {vehicle or 'unknown'}\n"
        f"The assistant last asked about: {asked}\n"
        f"Recent turns:\n{turns}\n"
        f'Latest seller message:\n"""\n{body.strip() or "(empty message)"}\n"""\n'
        f"Attachments: {image_count} image(s)"
    )
    return [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------------- generation

GENERATION_SYSTEM = """\
You are {agent}, a vehicle buyer at {dealership} (LMCT {lmct}), messaging a private seller on
{channel}. You are an automated assistant and the seller was told so in the first message. Never
claim to be a person and never deny being automated. If the seller asks whether they are talking
to a person, a separate process answers — you will not be asked to write that message.

Your job right now is discovery: collect the facts the dealership needs before it can price the
car, one question at a time, and clear up anything unclear. You do not make offers, quote prices,
estimate values, or discuss what the car might be worth. A person does that later, and you do not
know the number.

Hard rules — a message that breaks one is rejected before it is sent:
- Never state or imply a dollar figure, a price range, or what the car "should" fetch.
- Never mention other buyers, competing offers, market demand, scarcity or how quickly cars sell.
- Never assert a fact about the vehicle that is not in the fact sheet. Do not guess specifications,
  history, condition or colour. Do not restate numbers the seller gave (odometer, amounts) unless
  the instruction tells you to.
- Never commit to buying, promise an outcome, or say the deal is done. Any purchase is subject to
  inspection.
- No pressure or urgency: no deadlines, no "act now", "last chance", "today only".
- Do not repeat the disclosure and do not introduce yourself again.
- Exactly one question per message, and it is the one the instruction asks for.
- If the seller asks something not covered by the process notes below, say {agent} will confirm
  it, then continue with the question.

Tone: direct, unhurried, plain Australian English ("tyres", "rego", "km"). One to three short
sentences; never more than {max_length} characters. No emojis, no exclamation marks, no lists, no
headings, no sign-offs. Don't over-thank and don't flatter. Vary your wording from turn to turn.

What you may say about process:
{process_notes}

Output JSON matching the schema. `message` is the text to send. `proposed_state` is your read of
the stage (advisory — the system decides). `escalate` is true only when a person must take over
instead of any message going out: legal threat, deceased estate, seller in distress, seller is a
minor or not the owner, hostility, the seller insists on a person, or you cannot respond safely;
then `message` is ignored."""


def generation_system(
    *,
    agent: str,
    dealership: str,
    lmct: str,
    channel: str,
    max_length: int,
) -> str:
    return GENERATION_SYSTEM.format(
        agent=agent,
        dealership=dealership,
        lmct=lmct,
        channel=channel,
        max_length=max_length,
        process_notes=process_notes(agent=agent, dealership=dealership),
    )


# Fact-store rows the writer has no use for: bookkeeping, URLs, the seller's phone, and the asking
# price — a figure the model must never discuss at discovery, so it never holds it.
NOT_FOR_CONTEXT = frozenset(
    {
        "asking_price_aud",
        "listing_images",
        "seller_phone",
        "deferred_fields",
        "contradictions_acknowledged",
    }
)


def context_view(sheet: dict[str, Any]) -> dict[str, Any]:
    """Filter the fact sheet down to what the model should see."""
    out: dict[str, Any] = {}
    for section in ("confirmed", "claimed", "contradicted"):
        items = {}
        for key, value in (sheet.get(section) or {}).items():
            if key in NOT_FOR_CONTEXT:
                continue
            if key == "photos":
                n = len(value) if isinstance(value, list) else 0
                items["photos_received"] = n
                continue
            if isinstance(value, dict):
                value = {k: v for k, v in value.items() if k not in {"checked_at", "secured_parties"}}
            items[key] = value
        out[section] = items
    return out


def turn_context(
    *,
    stage: str,
    outstanding: list[str],
    sheet: dict[str, Any],
    instruction: str,
    seller_first_name: str,
    channel: str,
    max_length: int,
) -> str:
    """The per-turn block appended to the system prompt. Carries no valuation, by construction."""
    sheet = context_view(sheet)
    labels = []
    for key in outstanding:
        spec = spec_for(key)
        labels.append(f"{key} ({spec.label})" if spec else key)
    todo = ", ".join(labels) if labels else "nothing — all discovery fields collected"
    return (
        "\n\n# This turn\n"
        f"Stage: {stage}\n"
        f"Still to collect, in order: {todo}\n"
        "Fact sheet — the only vehicle facts that exist; never state others:\n"
        f"  verified: {_json(sheet.get('confirmed') or {})}\n"
        f"  seller says (unverified): {_json(sheet.get('claimed') or {})}\n"
        f"  contradicted: {_json(sheet.get('contradicted') or {})}\n"
        "Valuation: NOT RELEASED\n"
        f"Instruction: {instruction}\n"
        f"Seller's first name: {seller_first_name or 'unknown'} (use it rarely, never every message)\n"
        f"Channel: {channel}; keep the message under {max_length} characters."
    )


def gate_feedback(violations: list[str]) -> str:
    return (
        "Your previous draft was rejected by the validation gate for: "
        + "; ".join(violations)
        + ". Rewrite the message so none of these apply. Same instruction, same single question."
    )


# ---------------------------------------------------------------------------------- summary

SUMMARY_SYSTEM = """\
You keep a running summary of a chat between a dealership's automated buying assistant and a
private car seller. Fold the new turns into the existing summary. Under 120 words. Facts only:
what has been established about the car, what has been asked and answered, the seller's tone and
availability, anything unresolved. No speculation, no dollar figures, no advice."""


def summary_messages(existing: str | None, turns: list[tuple[str, str]]) -> list[dict[str, str]]:
    body = "\n".join(f"{role}: {text}" for role, text in turns)
    content = (
        f"Existing summary:\n{existing or '(none yet)'}\n\nNew turns to fold in:\n{body}\n\n"
        "Return the updated summary."
    )
    return [{"role": "user", "content": content}]


def discovery_field_glossary() -> str:
    """For documentation and the model-check command: the fields the extractor knows."""
    return "\n".join(f"{s.key}: {s.label} — {s.why}" for s in DISCOVERY_REQUIRED)
