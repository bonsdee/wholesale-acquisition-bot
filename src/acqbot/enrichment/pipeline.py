"""Pre-contact enrichment (Section 3.3) — runs as the `enrich_lead` job.

    rego lookup  → rego status / expiry; may yield the VIN         soft gate
    VIN decode   → exact variant, build month, factory options      soft gate
    PPSR         → finance owing, written-off, stolen               HARD gate
    guide + comps → valuation inputs (market_data)                  soft gate

Soft-gate failures are recorded on the lead and the job succeeds; the conversation can still
proceed and collect what is missing. A PPSR failure raises so the job retries with backoff —
nothing goes out until that answer exists. A written-off or stolen result terminates the lead.

When the listing carries neither VIN nor rego, PPSR is simply deferred: the state machine lists
`rego` as the first outstanding DISCOVERY field and enrichment re-runs once it is known.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from acqbot.config import get_settings
from acqbot.conversation.transitions import escalate, transition
from acqbot.enrichment.protocols import ProviderError, Providers, VehicleHint
from acqbot.enrichment.providers import get_providers
from acqbot.facts.store import fact_sheet, record_fact
from acqbot.models import FactSource, Lead, LeadState, MarketData, MarketDataKind
from acqbot.queue.jobs import enqueue
from acqbot.queue.worker import RetryableError, job_handler

log = logging.getLogger("acqbot.enrichment")

_SKIP_STATES = {LeadState.TERMINATED, LeadState.ARCHIVED, LeadState.HANDOFF, LeadState.REJECTED}


def _hint(sheet_get, payload: dict[str, Any]) -> VehicleHint:
    vc = payload["vehicle_claimed"]
    return VehicleHint(
        make=sheet_get("make", vc["make"]),
        model=sheet_get("model", vc["model"]),
        variant=sheet_get("variant", vc.get("variant")),
        year=int(sheet_get("year", vc["year"])),
        odometer_km=int(sheet_get("odometer_km", vc["odometer_km"])),
    )


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return _jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime | uuid.UUID):
        return obj.isoformat() if isinstance(obj, datetime) else str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def enrich_lead(session: Session, lead_id: uuid.UUID, providers: Providers | None = None) -> dict[str, Any]:
    """Run enrichment for one lead. Returns a summary dict (used by tests and the CLI)."""
    providers = providers or get_providers()
    settings = get_settings()
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise ValueError(f"lead {lead_id} not found")
    if lead.state in _SKIP_STATES:
        return {"skipped": True, "state": lead.state.value}

    sheet = fact_sheet(session, lead_id)
    payload = lead.upstream_payload
    hint = _hint(sheet.get, payload)
    state = payload["location"]["state"]
    errors: list[dict[str, Any]] = []
    contradictions: list[str] = []
    summary: dict[str, Any] = {"lead_id": str(lead_id)}

    def note(rr) -> None:
        if rr.contradiction:
            contradictions.append(rr.contradiction.field)

    vin: str | None = sheet.get("vin")
    rego: str | None = sheet.get("rego")

    # --- rego lookup (soft) ---
    if rego:
        try:
            r = providers.rego.lookup(rego, state, hint=hint)
            note(
                record_fact(
                    session,
                    lead_id,
                    "rego_status",
                    {
                        "status": r.status,
                        "expiry": r.expiry.isoformat() if r.expiry else None,
                        "state": r.state,
                    },
                    source=FactSource.REGO,
                    verified=True,
                )
            )
            if r.vin and not vin:
                vin = r.vin
                note(record_fact(session, lead_id, "vin", r.vin, source=FactSource.REGO, verified=True))
            summary["rego"] = r.status
        except ProviderError as exc:
            errors.append({"step": "rego", "error": str(exc), "retryable": exc.retryable})

    # --- VIN decode (soft) ---
    if vin:
        try:
            d = providers.vin.decode(vin, hint=hint)
            for key, val in (
                ("make", d.make),
                ("model", d.model),
                ("year", d.year),
                ("variant", d.variant),
                ("build_month", d.build_month),
                ("body", d.body),
                ("factory_options", d.factory_options),
            ):
                if val is not None:
                    note(record_fact(session, lead_id, key, val, source=FactSource.VIN, verified=True))
            summary["vin_decode"] = {"variant": d.variant, "year": d.year}
        except ProviderError as exc:
            errors.append({"step": "vin", "error": str(exc), "retryable": exc.retryable})

    # --- PPSR (hard gate) ---
    if vin:
        try:
            p = providers.ppsr.check(vin)
        except ProviderError as exc:
            if exc.retryable:
                raise RetryableError(f"ppsr: {exc}") from exc
            errors.append({"step": "ppsr", "error": str(exc), "retryable": False})
            lead.enrichment_errors = errors
            escalate(session, lead, "ppsr_unavailable", {"error": str(exc)})
            return {**summary, "escalated": "ppsr_unavailable"}

        record_fact(
            session,
            lead_id,
            "finance_owing",
            {
                "owing": p.encumbered,
                "amount_aud": p.encumbrance_amount_aud,
                "secured_parties": p.secured_parties,
                "checked_at": p.checked_at.isoformat(),
            },
            source=FactSource.PPSR,
            verified=True,
        )
        record_fact(
            session,
            lead_id,
            "write_off_status",
            {
                "written_off": p.written_off,
                "type": p.written_off_type,
                "checked_at": p.checked_at.isoformat(),
            },
            source=FactSource.PPSR,
            verified=True,
        )
        record_fact(session, lead_id, "stolen", p.stolen, source=FactSource.PPSR, verified=True)
        summary["ppsr"] = {"encumbered": p.encumbered, "written_off": p.written_off, "stolen": p.stolen}

        if p.stolen or p.written_off:
            reason = "ppsr_stolen" if p.stolen else "ppsr_written_off"
            lead.enriched_at = datetime.now(UTC)
            lead.enrichment_errors = errors or None
            transition(session, lead, LeadState.TERMINATED, reason, {"vin": vin, "ppsr": _jsonable(p.raw)})
            return {**summary, "terminated": reason}

    # --- guide + comps (soft) ---
    h = _hint(
        fact_sheet(session, lead_id).get, payload
    )  # re-read: VIN decode may have corrected variant/year
    try:
        g = providers.guide.lookup(h.make, h.model, h.variant, h.year, h.odometer_km)
        session.add(
            MarketData(lead_id=lead_id, provider=g.provider, kind=MarketDataKind.GUIDE, payload=_jsonable(g))
        )
        summary["guide"] = [g.trade_low, g.trade_high]
    except ProviderError as exc:
        errors.append({"step": "guide", "error": str(exc), "retryable": exc.retryable})
    try:
        c = providers.comps.search(h.make, h.model, h.variant, h.year, h.odometer_km)
        session.add(
            MarketData(lead_id=lead_id, provider=c.provider, kind=MarketDataKind.COMPS, payload=_jsonable(c))
        )
        summary["comps"] = len(c.comps)
    except ProviderError as exc:
        errors.append({"step": "comps", "error": str(exc), "retryable": exc.retryable})

    lead.enriched_at = datetime.now(UTC)
    lead.enrichment_errors = errors or None
    session.flush()

    # --- escalation triggers that can fire before contact (Section 5.2) ---
    asking = int(payload["vehicle_claimed"]["asking_price_aud"])
    if asking > settings.high_value_threshold_aud:
        escalate(
            session,
            lead,
            "high_value",
            {"asking_price_aud": asking, "threshold": settings.high_value_threshold_aud},
        )
        summary["escalated"] = "high_value"

    summary["contradictions"] = contradictions
    summary["errors"] = errors
    summary["ppsr_pending"] = vin is None
    if lead.state == LeadState.NEW:
        enqueue(session, "maybe_send_opening", {"lead_id": str(lead_id)}, dedupe_key=f"opening:{lead_id}")
    return summary


@job_handler("enrich_lead")
def _handle_enrich_lead(session: Session, payload: dict[str, Any]) -> None:
    enrich_lead(session, uuid.UUID(payload["lead_id"]))
