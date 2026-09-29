"""Inbound channel webhooks: Messenger (GET verify + POST events) and Twilio SMS.

Both endpoints verify, parse and enqueue. The worker runs the conversation loop, so the model's
latency never sits on the webhook response and a redelivered event is deduplicated by message id.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from acqbot.config import get_settings
from acqbot.db import session_scope
from acqbot.queue.jobs import enqueue
from acqbot.transport import messenger, sms
from acqbot.transport.protocol import InboundMessage, TransportError

log = logging.getLogger("acqbot.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _enqueue_inbound(msg: InboundMessage) -> str:
    dedupe = f"inbound:{msg.channel.value}:{msg.external_msg_id}" if msg.external_msg_id else None
    with session_scope() as s:
        job = enqueue(s, "inbound_message", msg.to_payload(), dedupe_key=dedupe, max_attempts=3)
        return str(job.job_id) if job is not None else "duplicate"


@router.get("/messenger")
def messenger_verify(request: Request) -> PlainTextResponse:
    q = request.query_params
    try:
        challenge = messenger.verify_subscription(
            q.get("hub.mode"),
            q.get("hub.verify_token"),
            q.get("hub.challenge"),
            get_settings().messenger_verify_token,
        )
    except TransportError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return PlainTextResponse(challenge)


@router.post("/messenger")
async def messenger_events(request: Request) -> dict:
    settings = get_settings()
    body = await request.body()
    # Fail closed: with no secret there is nothing to verify against, and an unverified event is
    # indistinguishable from a forged seller message. It used to skip the check instead.
    if not settings.messenger_app_secret:
        raise HTTPException(status_code=503, detail="messenger is not configured")
    if not messenger.verify_signature(
        settings.messenger_app_secret, body, request.headers.get("X-Hub-Signature-256")
    ):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    jobs = [_enqueue_inbound(msg) for msg in messenger.parse_webhook(payload)]
    return {"received": len(jobs), "queued": jobs}


@router.post("/sms")
async def sms_events(request: Request) -> PlainTextResponse:
    settings = get_settings()
    # Unsigned, this endpoint would take anyone's POST as a seller's reply: an accepted offer, a
    # STOP, a claim that there is no finance owing. Nothing is parsed before the signature passes.
    if not settings.twilio_auth_token:
        raise HTTPException(status_code=503, detail="sms is not configured")
    raw = await request.form()
    params = [(k, v) for k, v in raw.multi_items() if isinstance(v, str)]
    url = settings.twilio_webhook_url or str(request.url)
    if not sms.verify_twilio_signature(
        settings.twilio_auth_token, url, params, request.headers.get(sms.TWILIO_SIGNATURE_HEADER)
    ):
        log.warning("rejected /webhooks/sms with a bad signature (verified against %s)", url)
        raise HTTPException(status_code=403, detail="bad signature")
    msg = sms.parse_twilio_webhook(dict(params))
    if msg is None:
        raise HTTPException(status_code=400, detail="missing From")
    _enqueue_inbound(msg)
    # Twilio expects TwiML; an empty response means "no reply from here" (we send via the API).
    return PlainTextResponse("<Response></Response>", media_type="application/xml")
