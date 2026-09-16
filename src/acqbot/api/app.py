"""HTTP surface: the signed lead webhook, a read-only lead inspector, and health."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from acqbot import __version__
from acqbot.config import get_settings
from acqbot.contracts import LeadContractError
from acqbot.conversation.transitions import state_history
from acqbot.db import session_scope
from acqbot.facts.store import fact_sheet
from acqbot.ingestion import security
from acqbot.ingestion.service import ingest_lead
from acqbot.models import Escalation, Lead, MarketData, Valuation

log = logging.getLogger("acqbot.api")


def create_app() -> FastAPI:
    from acqbot.api.admin import router as admin_router
    from acqbot.api.webhooks import router as webhooks_router
    from acqbot.console.routes import router as console_router

    app = FastAPI(title="acqbot", version=__version__, docs_url="/docs")
    app.include_router(webhooks_router)
    app.include_router(admin_router)
    app.include_router(console_router, include_in_schema=False)

    @app.get("/")
    def root() -> dict[str, Any]:
        """Signpost. Nothing operational lives here."""
        return {
            "service": "acqbot",
            "version": __version__,
            "docs": "/docs",
            "endpoints": {
                "health": "GET /health",
                "lead_intake": "POST /leads (HMAC-signed)",
                "lead_detail": "GET /leads/{lead_id}",
                "messenger_webhook": "GET|POST /webhooks/messenger",
                "sms_webhook": "POST /webhooks/sms",
                "console": "GET /console (the screen)",
                "admin_api": "GET /admin/escalations, GET /admin/handoffs (header: x-admin-token)",
            },
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        with session_scope() as s:
            s.execute(text("select 1"))
        return {"ok": True, "version": __version__}

    @app.post("/leads", status_code=201)
    async def post_lead(request: Request) -> JSONResponse:
        settings = get_settings()
        body = await request.body()
        if not settings.allow_unsigned_leads:
            try:
                security.verify(
                    settings.lead_webhook_secret,
                    body,
                    request.headers.get(security.SIGNATURE_HEADER),
                    tolerance_seconds=settings.webhook_timestamp_tolerance_seconds,
                )
            except security.SignatureError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="body is not valid JSON") from exc
        try:
            with session_scope() as s:
                result = ingest_lead(s, payload)
        except LeadContractError as exc:
            raise HTTPException(status_code=422, detail={"message": str(exc), "errors": exc.errors}) from exc
        status = 201 if result.status == "accepted" else 200
        return JSONResponse(result.as_dict(), status_code=status)

    @app.get("/leads/{lead_id}")
    def get_lead(lead_id: uuid.UUID) -> dict[str, Any]:
        with session_scope() as s:
            lead = s.get(Lead, lead_id)
            if lead is None:
                raise HTTPException(status_code=404, detail="lead not found")
            sheet = fact_sheet(s, lead_id)
            market = list(
                s.scalars(
                    select(MarketData).where(MarketData.lead_id == lead_id).order_by(MarketData.fetched_at)
                )
            )
            valuations = list(
                s.scalars(
                    select(Valuation).where(Valuation.lead_id == lead_id).order_by(Valuation.computed_at)
                )
            )
            escalations = list(
                s.scalars(select(Escalation).where(Escalation.lead_id == lead_id).order_by(Escalation.at))
            )
            return {
                "lead_id": str(lead.lead_id),
                "state": lead.state.value,
                "source": lead.source,
                "listing_url": lead.listing_url,
                "created_at": lead.created_at.isoformat(),
                "enriched_at": lead.enriched_at.isoformat() if lead.enriched_at else None,
                "enrichment_errors": lead.enrichment_errors,
                "facts": sheet.as_dict(),
                "market_data": [
                    {
                        "kind": m.kind.value,
                        "provider": m.provider,
                        "fetched_at": m.fetched_at.isoformat(),
                        "payload": m.payload,
                    }
                    for m in market
                ],
                "valuations": [
                    {
                        "valuation_id": str(v.valuation_id),
                        "basis": v.basis.value,
                        "band": [float(v.band_low), float(v.band_high)],
                        "wholesale_max": float(v.wholesale_max),
                        "ladder": v.ladder,
                        "computed_at": v.computed_at.isoformat(),
                    }
                    for v in valuations
                ],
                "escalations": [
                    {
                        "reason": e.reason,
                        "at": e.at.isoformat(),
                        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
                    }
                    for e in escalations
                ],
                "state_log": [
                    {
                        "from": h.from_state.value if h.from_state else None,
                        "to": h.to_state.value,
                        "trigger": h.trigger,
                        "at": h.at.isoformat(),
                    }
                    for h in state_history(s, lead_id)
                ],
            }

    return app


app = create_app()
