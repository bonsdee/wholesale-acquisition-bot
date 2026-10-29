"""Inbound webhooks: Messenger (GET verify + POST events), Twilio SMS, and GoHighLevel leads.

Both endpoints verify, parse and enqueue. The worker runs the conversation loop, so the model's
latency never sits on the webhook response and a redelivered event is deduplicated by message id.
"""

from __future__ import annotations

import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

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


@router.post("/ghl")
async def ghl_lead(request: Request) -> JSONResponse:
    """A GoHighLevel workflow's Webhook action: one contact, one car. GHL cannot sign a body, so a
    shared token stands in — in the X-Acqbot-Token header (preferred: query strings end up in access
    logs) or as ?token=."""
    from acqbot.contracts import LeadContractError
    from acqbot.conversation.service import agent_for
    from acqbot.crm import ghl
    from acqbot.ingestion.service import ingest_lead
    from acqbot.models import Lead

    settings = get_settings()
    if not settings.ghl_inbound_token:
        raise HTTPException(status_code=404, detail="not found")
    given = request.headers.get(ghl.INBOUND_TOKEN_HEADER) or request.query_params.get("token") or ""
    if not hmac.compare_digest(given.encode(), settings.ghl_inbound_token.encode()):
        raise HTTPException(status_code=401, detail="bad token")
    try:
        payload = json.loads(await request.body())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")

    contact_id = str(payload.get("contact_id") or payload.get("contactId") or "").strip()
    lead_payload, missing = ghl.lead_from_webhook(payload, settings)
    try:
        with session_scope() as s:
            if lead_payload is None:
                ghl.flag_missing(s, contact_id, missing)
                return JSONResponse({"status": "missing_fields", "missing": missing}, status_code=422)
            result = ingest_lead(s, lead_payload)
            if result.status == "accepted":
                lead = s.get(Lead, result.lead_id)
                ghl.link_inbound(s, lead.lead_id, contact_id)
                agent_for(s, lead, settings)  # same as a console lead: the name is fixed before first contact
                ghl.schedule_sync(s, lead)  # opens the opportunity at New Lead
    except LeadContractError as exc:
        with session_scope() as s:
            ghl.flag_missing(s, contact_id, [str(e.get("loc", "")) for e in exc.errors] or ["valid details"])
        return JSONResponse({"status": "invalid", "errors": exc.errors}, status_code=422)
    return JSONResponse(result.as_dict(), status_code=201 if result.status == "accepted" else 200)
