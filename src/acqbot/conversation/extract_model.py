"""Stage 2 with a model — Section 7, Phase 4.

The small model reads the seller's message in context and returns facts in a fixed wire format;
`parse_extraction` coerces every value into the fact vocabulary or drops it. The Phase 3 regex
screens still run underneath as a floor: a legal threat the model misses is still caught, and a
"STOP" is still a stop. When the model call fails the turn falls back to the rule-based parser
rather than stalling — the trace row records that it did.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from acqbot.config import Settings
from acqbot.conversation.extract import Extraction, extract
from acqbot.llm.client import ModelClient, ModelRequest
from acqbot.llm.prompts import PROMPT_VERSION, extraction_messages, extraction_system
from acqbot.llm.schemas import EXTRACTION_SCHEMA, FIELD_WIRE, parse_extraction
from acqbot.llm.trace import traced_call

PRICE_INTENTS = {"accept", "reject", "counter", "price_question"}


@dataclass
class ExtractionContext:
    lead_id: uuid.UUID
    thread_id: uuid.UUID
    vehicle: str  # "2015 Mitsubishi Outlander LS" from the fact sheet
    pending_field: str | None
    last_question: str | None
    recent_turns: list[tuple[str, str]]  # [("assistant"|"seller", text)] oldest first, last few only
    stage_priced: bool


class ModelExtractor:
    def __init__(self, session: Session, client: ModelClient, settings: Settings) -> None:
        self.s = session
        self.client = client
        self.cfg = settings

    def extract(self, body: str, attachments: list[dict[str, Any]], ctx: ExtractionContext) -> Extraction:
        rules = extract(body, attachments, pending_field=ctx.pending_field, stage_priced=ctx.stage_priced)
        image_count = len(rules.photo_urls)
        req = ModelRequest(
            purpose="extract",
            model=self.cfg.extraction_model,
            system=extraction_system(),
            messages=extraction_messages(
                vehicle=ctx.vehicle,
                pending_field=ctx.pending_field,
                last_question=ctx.last_question,
                recent_turns=ctx.recent_turns,
                body=body,
                image_count=image_count,
            ),
            schema=EXTRACTION_SCHEMA,
            max_tokens=min(self.cfg.llm_max_output_tokens, 700),
            effort=self.cfg.llm_effort,
            meta={
                "body": body,
                "attachments": attachments,
                "pending_field": ctx.pending_field,
                "stage_priced": ctx.stage_priced,
            },
        )
        resp, row = traced_call(
            self.s,
            self.client,
            req,
            prompt_version=PROMPT_VERSION,
            lead_id=ctx.lead_id,
            thread_id=ctx.thread_id,
        )
        out = parse_extraction(resp.parsed) if resp is not None else None
        if out is None:
            rules.generator = "rules_fallback"
            rules.model_call_id = str(row.call_id)
            return rules
        return merge(rules, out, ctx, min_confidence=self.cfg.fact_min_confidence, call_id=str(row.call_id))


def merge(
    rules: Extraction, out: Any, ctx: ExtractionContext, *, min_confidence: float, call_id: str
) -> Extraction:
    """Model output over the rule-based floor. Screens are a union; facts are the model's, validated."""
    ex = Extraction(generator="model", model_call_id=call_id)
    ex.photo_urls = list(rules.photo_urls)

    # Facts: the model's, coerced and confident enough; the regex parsers fill only what it missed.
    for f in out.facts:
        if f.confidence >= min_confidence:
            ex.facts[f.field] = f.value
        else:
            ex.low_confidence[f.field] = f.raw
    for key, value in rules.facts.items():
        if key not in ex.facts and key not in ex.low_confidence:
            ex.facts[key] = value

    # Screens: whichever side saw it. "stop" is an intent for the model, a flag for the rest of the system.
    ex.flags = set(rules.flags) | set(out.flags)
    if "stop" in out.intents:
        ex.flags.add("stop")

    # Intents that need a price on the table only count once there is one; the rest always do.
    intents = set(out.intents) - {"stop"}
    if not ctx.stage_priced:
        intents -= PRICE_INTENTS - {"price_question"}
    ex.intents = intents | set(rules.intents)
    ex.counter_price = out.counter_price_aud if out.counter_price_aud is not None else rules.counter_price
    if ex.counter_price is not None and ctx.stage_priced:
        ex.intents.add("counter")
        ex.intents.discard("accept")
    ex.phone = out.phone or rules.phone
    ex.seller_question = out.seller_question
    ex.notes = list(out.notes)
    # "Answered" means a usable value landed for the field we asked about — not the model's opinion
    # that it was answered. A low-confidence answer is asked again.
    pending = ctx.pending_field
    if pending in FIELD_WIRE:
        ex.parsed_pending = bool(
            pending in ex.facts or (pending == "rego" and "vin" in ex.facts) or rules.parsed_pending
        )
    elif pending == "photos":
        ex.parsed_pending = bool(ex.photo_urls)
    else:
        ex.parsed_pending = bool(out.answered_pending or rules.parsed_pending)
    return ex
