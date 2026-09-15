"""Trace capture — every model call stored whole, success or failure (Section 11)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from acqbot.llm.client import ModelClient, ModelError, ModelRequest, ModelResponse
from acqbot.models import ModelCall

log = logging.getLogger("acqbot.llm")


def record_call(
    session: Session,
    req: ModelRequest,
    resp: ModelResponse | None,
    *,
    prompt_version: str,
    lead_id: uuid.UUID | None,
    thread_id: uuid.UUID | None,
    error: str | None = None,
) -> ModelCall:
    row = ModelCall(
        lead_id=lead_id,
        thread_id=thread_id,
        purpose=req.purpose,
        model=resp.model if resp else req.model,
        prompt_version=prompt_version,
        prompt_hash=req.prompt_hash(prompt_version),
        request=req.wire(),
        response=resp.as_dict() if resp else None,
        input_tokens=resp.input_tokens if resp else None,
        output_tokens=resp.output_tokens if resp else None,
        latency_ms=resp.latency_ms if resp else None,
        error=error,
    )
    session.add(row)
    session.flush()
    return row


def traced_call(
    session: Session,
    client: ModelClient,
    req: ModelRequest,
    *,
    prompt_version: str,
    lead_id: uuid.UUID | None,
    thread_id: uuid.UUID | None,
) -> tuple[ModelResponse | None, ModelCall]:
    """Call the model and record the outcome either way. Returns (response or None, trace row)."""
    try:
        resp = client.complete(req)
    except ModelError as exc:
        log.warning("model call failed (%s, %s): %s", req.purpose, req.model, exc)
        row = record_call(
            session,
            req,
            None,
            prompt_version=prompt_version,
            lead_id=lead_id,
            thread_id=thread_id,
            error=str(exc),
        )
        return None, row
    row = record_call(session, req, resp, prompt_version=prompt_version, lead_id=lead_id, thread_id=thread_id)
    return resp, row
