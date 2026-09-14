"""Inbound channel webhooks: Messenger (GET verify + POST events) and Twilio SMS."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from acqbot.config import get_settings
from acqbot.conversation.service import Conversation
from acqbot.db import session_scope
from acqbot.models import Channel
from acqbot.transport import messenger, sms
from acqbot.transport.protocol import TransportError
from acqbot.transport.registry import get_transport

log = logging.getLogger("acqbot.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


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
    if settings.messenger_app_secret and not messenger.verify_signature(
        settings.messenger_app_secret, body, request.headers.get("X-Hub-Signature-256")
    ):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    results = []
    for msg in messenger.parse_webhook(payload):
        with session_scope() as s:
            r = Conversation(s, get_transport(Channel.MESSENGER)).handle_inbound(msg)
        results.append({"action": r.action, "lead_id": str(r.lead_id) if r.lead_id else None})
    return {"received": len(results), "results": results}


@router.post("/sms")
async def sms_events(request: Request) -> PlainTextResponse:
    form = dict(await request.form())
    msg = sms.parse_twilio_webhook(form)
    if msg is None:
        raise HTTPException(status_code=400, detail="missing From")
    with session_scope() as s:
        Conversation(s, get_transport(Channel.SMS)).handle_inbound(msg)
    # Twilio expects TwiML; an empty response means "no reply from here" (we send via the API).
    return PlainTextResponse("<Response></Response>", media_type="application/xml")
