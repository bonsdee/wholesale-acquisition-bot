"""Conversation memory in context — Section 7.3.

The last N turns go in verbatim; everything older is carried as a rolling summary stored on the
thread. Nothing is ever dropped: turns not yet folded into the summary stay verbatim until they
are, so a failed summary call widens the window rather than losing context.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from acqbot.config import Settings
from acqbot.llm.client import ModelClient, ModelRequest
from acqbot.llm.prompts import PROMPT_VERSION, SUMMARY_SYSTEM, summary_messages
from acqbot.llm.schemas import SUMMARY_SCHEMA
from acqbot.llm.trace import traced_call
from acqbot.models import Direction, Lead, Message, Thread


@dataclass
class History:
    summary: str | None
    turns: list[Message]  # verbatim window, oldest first
    messages: list[dict[str, str]] = field(default_factory=list)  # what the API receives
    summarised_now: bool = False


def _text(m: Message) -> str:
    if m.body and m.body.strip():
        return m.body.strip()
    n = len(m.attachments or [])
    return f"[sent {n} photo{'s' if n != 1 else ''}]" if n else "[empty message]"


def as_turns(msgs: list[Message]) -> list[tuple[str, str]]:
    return [("seller" if m.direction == Direction.INBOUND else "assistant", _text(m)) for m in msgs]


def to_api_messages(summary: str | None, turns: list[Message]) -> list[dict[str, str]]:
    """Alternating user/assistant messages, seller as user, ending on a user turn."""
    raw: list[tuple[str, str]] = []
    if summary:
        raw.append(("user", f"[Earlier in this conversation: {summary}]"))
    for m in turns:
        raw.append(("user" if m.direction == Direction.INBOUND else "assistant", _text(m)))
    merged: list[dict[str, str]] = []
    for role, text in raw:
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] += "\n\n" + text
        else:
            merged.append({"role": role, "content": text})
    if not merged or merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "[start of conversation]"})
    if merged[-1]["role"] != "user":
        merged.append({"role": "user", "content": "[the seller has not replied yet]"})
    return merged


def load_messages(session: Session, lead: Lead) -> list[Message]:
    return list(
        session.scalars(
            select(Message).where(Message.lead_id == lead.lead_id).order_by(Message.sent_at, Message.msg_id)
        )
    )


def build_history(
    session: Session,
    thread: Thread,
    lead: Lead,
    *,
    client: ModelClient | None,
    settings: Settings,
) -> History:
    msgs = load_messages(session, lead)
    through = thread.history_summary_through
    unfolded = [m for m in msgs if through is None or m.sent_at > through]
    n, batch = settings.history_verbatim_turns, settings.history_summary_batch
    summarised = False
    if client is not None and len(unfolded) > n + batch:
        to_fold = unfolded[: len(unfolded) - n]
        req = ModelRequest(
            purpose="summarise",
            model=settings.extraction_model,
            system=SUMMARY_SYSTEM,
            messages=summary_messages(thread.history_summary, as_turns(to_fold)),
            schema=SUMMARY_SCHEMA,
            max_tokens=400,
            effort=settings.llm_effort,
            meta={"turn_count": len(to_fold), "existing": thread.history_summary},
        )
        resp, _ = traced_call(
            session,
            client,
            req,
            prompt_version=PROMPT_VERSION,
            lead_id=lead.lead_id,
            thread_id=thread.thread_id,
        )
        text = (resp.parsed or {}).get("summary") if resp is not None and resp.parsed else None
        if isinstance(text, str) and text.strip():
            thread.history_summary = text.strip()[:2000]
            thread.history_summary_through = to_fold[-1].sent_at
            session.flush()
            unfolded = unfolded[len(to_fold) :]
            summarised = True
    return History(
        summary=thread.history_summary,
        turns=unfolded,
        messages=to_api_messages(thread.history_summary, unfolded),
        summarised_now=summarised,
    )
