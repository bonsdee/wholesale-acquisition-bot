"""Prompt architecture — Section 7.2, as pure functions of the state the code already holds.

Layout per call:
    SYSTEM   identity, disclosure stance, objective, hard constraints, tone, process notes,
             then the context for this turn (stage, outstanding fields, fact sheet, valuation
             NOT RELEASED, the instruction from the planner, channel limits)
    HISTORY  rolling summary + the last N turns verbatim, as user/assistant messages

Before PRICED the valuation never appears here: a number the model does not hold cannot be leaked.
From PRICED (Phase 5) it is handed exactly one figure — the amount code has decided to present —
and still never the ladder, the ceiling or the band. PROMPT_VERSION is recorded with every call and
every outbound message; bump it whenever any wording below changes, including the process notes.
"""

from __future__ import annotations

import json
from typing import Any

from acqbot.facts.fields import DISCOVERY_REQUIRED, spec_for
from acqbot.llm.knowledge import process_notes
from acqbot.llm.schemas import FIELD_WIRE

# v3: the ladder released to the writer, one figure at a time (Phase 5)
# v4: first real-model run (21 Sep) — one question mark, varied openers, offer phrases quoted verbatim
# v5: first full real run (21 Sep) — normal capitalisation (v4's quoted "Good" was dodged by writing
#     it lowercase), the last two openers named per turn, "hostile" narrowed to abuse not impatience
# v6: second live run (21 Sep) — offer instructions make no statement about limits; forbidden
#     wording described rather than quoted, since the model copies what it is shown
# v7: final live run (21 Sep) — the first offer is told the number is ready now (it stalled twice)
# v8: Direct Auto (21 Sep) — process notes: business-hours reply, a person can explain the figure
# v9: Direct Auto roster (21 Sep) — the assistant works WITH a named salesperson instead of being
#     one (the names are now real people); the photo question names five shots
# v10: live run (28 Sep) — a concession's acknowledgement may not refuse to move (it read "can't
#      move on it" then moved), and its closing question is worded differently from the last one
# v11: client's live test (15 Oct) — told it cannot see photos (it claimed the dash shot was
#      missing from three photos it had never seen)
PROMPT_VERSION = "prompt:v11"


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
  talking to a bot, a machine or a real person), hostile (abuse, threats, slurs or harassment
  aimed at the assistant or the dealership — swearing AT someone, not merely swearing). Impatience
  is NOT hostile: "about time", "can we speed this up", "why so many questions", a curt one-word
  answer, sarcasm about how long this is taking. A blunt seller is still a seller and is still
  selling — flagging them sends a willing lead to a queue. Do not flag frustration or a hard
  bargain either.
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
You are the automated assistant for {dealership} (LMCT {lmct}), messaging a private seller on
{channel}. You work with {agent}, a real person on the dealership's team who looks after this
seller. The first message told the seller both things: that you are automated, and that you work
with {agent}. Write only as yourself — never as {agent} or any other person — and never claim to
be a person or deny being automated. If the seller asks whether they are talking to a person, a
separate process answers — you will not be asked to write that message.

{job}

Hard rules — a message that breaks one is rejected before it is sent:
{figure_rule}
- Never mention other buyers, competing offers, market demand, scarcity or how quickly cars sell.
- Never assert a fact about the vehicle that is not in the fact sheet. Do not guess specifications,
  history, condition or colour. Do not restate numbers the seller gave (odometer, amounts) unless
  the instruction tells you to.
- You cannot see photos. You only know how many have arrived (photos_received). Never say what a
  photo shows, or which shot is missing or still needed.
- Never commit to buying, promise an outcome, or say the deal is done. Any purchase is subject to
  inspection.
{urgency_rule}
- Do not repeat the disclosure and do not introduce yourself again.
- At most ONE question per message, and so at most one question mark. When the instruction asks
  for a question, ask that one, put it last, and add nothing after it — not a follow-up, not "any
  X worth mentioning?". When the instruction does not ask for a question, ask none. A seller on a
  phone answers the last question they read, and the first one is lost.
- Never invent dealership process, timing, price or policy. If the seller asks something the
  process notes below do not cover, say {agent} will confirm it, then carry on.

Tone: direct, unhurried, plain Australian English ("tyres", "rego", "km"). One to three short
sentences — aim for under 300 characters and never exceed {max_length}. No emojis, no exclamation
marks, no lists, no headings, no sign-offs. Don't over-thank and don't flatter.

Capitalisation is normal written English: a capital letter at the start of every sentence, "I"
always capitalised, and people's and place names capitalised. Never write in all lowercase. The
only thing ruled out is a whole word in capitals for emphasis.

Vary your wording from turn to turn — a seller reading six near-identical messages notices. Do
not start a message with the same word as either of your last two, whatever its capitalisation;
the turn notes below tell you which words those were. Acknowledge an answer only when it told you
something worth reacting to; otherwise go straight to the question. Words like good, great,
thanks and noted at the start of every message read as a script.

What you may say about process:
{process_notes}

Output JSON matching the schema. `message` is the text to send. `proposed_state` is your read of
the stage (advisory — the system decides). `escalate` is true only when a person must take over
instead of any message going out: legal threat, deceased estate, seller in distress, seller is a
minor or not the owner, hostility, the seller insists on a person, or you cannot respond safely;
then `message` is ignored. A blunt seller, a slow one, an impatient one, or one driving a hard
bargain is NOT a reason to escalate — write the message."""


DISCOVERY_JOB = """\
Your job right now is discovery: collect the facts the dealership needs before it can price the
car, one question at a time, and clear up anything unclear. You do not make offers, quote prices,
estimate values, or discuss what the car might be worth. A person does that later, and you do not
know the number."""

OFFER_JOB = """\
The car has been priced and your job right now is to put ONE figure to the seller — the one given
to you below, worked out by the dealership's valuation engine. You did not calculate it, you cannot
change it, and you have not been told what else might be possible, because nothing else has been
authorised. Word the message; the number is not yours to move."""

DISCOVERY_URGENCY_RULE = '- No pressure or urgency: no deadlines, no "act now", "last chance", "today only".'

OFFER_URGENCY_RULE = """\
- No pressure: no "act now", "last chance", "today only", no other buyers, and no deadline you
  invented. The expiry you were given is real — the valuation inputs genuinely move — so state it
  once, plainly, and do not dramatise it or use it as a threat."""

DISCOVERY_FIGURE_RULE = (
    '- Never state or imply a dollar figure, a price range, or what the car "should" fetch.'
)

OFFER_FIGURE_RULE = """\
- State the figure you were given, exactly as written, and no other amount: no range, no round
  number, no "around", no earlier figure, no what-the-car-might-fetch, no part-payment or trade.
- Never suggest more is available, hint at a better price, invite a counter-offer, or say this is
  "as high as we can go" — you have not been told what the limit is."""


def generation_system(
    *,
    agent: str,
    dealership: str,
    lmct: str,
    channel: str,
    max_length: int,
    presenting_offer: bool = False,
) -> str:
    """The standing half of the prompt. From Phase 5 it has two shapes, because telling a writer
    that it has no number and then handing it one is how a model talks itself into a range."""
    return GENERATION_SYSTEM.format(
        agent=agent,
        dealership=dealership,
        lmct=lmct,
        channel=channel,
        max_length=max_length,
        process_notes=process_notes(agent=agent, dealership=dealership),
        job=OFFER_JOB if presenting_offer else DISCOVERY_JOB,
        figure_rule=OFFER_FIGURE_RULE if presenting_offer else DISCOVERY_FIGURE_RULE,
        urgency_rule=OFFER_URGENCY_RULE if presenting_offer else DISCOVERY_URGENCY_RULE,
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
    offer_amount: str | None = None,
    avoid_openers: list[str] | None = None,
) -> str:
    """The per-turn block appended to the system prompt.

    `avoid_openers` are the first words of the last two messages the assistant sent. A general rule
    ("vary your wording") was ignored on the first live run — four of nine messages opened with
    "Good" — so the specific words are named here, turn by turn, where they cannot be missed.

    Before PRICED the valuation is not here at all — there is no parameter for it, and a number the
    model does not hold cannot be leaked. From PRICED the model is given ONE figure: the amount it
    has been told to present. Never the ladder, never the ceiling, never the band, never what the
    engine thinks the car is worth — knowing the ceiling is what would let it hint at more."""
    sheet = context_view(sheet)
    labels = []
    for key in outstanding:
        spec = spec_for(key)
        labels.append(f"{key} ({spec.label})" if spec else key)
    todo = ", ".join(labels) if labels else "nothing — all discovery fields collected"
    return (
        "\n\n# This turn\n"
        f"Stage: {stage}\n"
        f"Still to collect, over the coming turns (for your awareness — ask only what the instruction "
        f"below says): {todo}\n"
        "Fact sheet — the only vehicle facts that exist; never state others:\n"
        f"  verified: {_json(sheet.get('confirmed') or {})}\n"
        f"  seller says (unverified): {_json(sheet.get('claimed') or {})}\n"
        f"  contradicted: {_json(sheet.get('contradicted') or {})}\n"
        + (
            "Valuation: NOT RELEASED\n"
            if not offer_amount
            else (
                f"Offer to present: {offer_amount} — this exact figure and no other. It is the only "
                "number you have. There is no range, no ceiling and no 'best we can do' you are "
                "holding back; if asked for more, say it is not your decision.\n"
            )
        )
        + f"Instruction: {instruction}\n"
        + (
            "Your last messages began with: "
            + ", ".join(f'"{w}"' for w in avoid_openers)
            + ". Begin this one with a different word.\n"
            if avoid_openers
            else ""
        )
        + f"Seller's first name: {seller_first_name or 'unknown'} (use it rarely, never every message)\n"
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
