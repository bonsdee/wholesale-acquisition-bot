"""Handoff packet — Figure 5. Written once at ACCEPTED so the closer needs no re-discovery."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acqbot.facts.fields import DISCOVERY_REQUIRED
from acqbot.facts.store import FactSheet, fact_sheet
from acqbot.models import HandoffPacket, Lead, MarketData, MarketDataKind, Message, Offer, Valuation


def _render_fact(key: str, value: Any) -> str | None:
    """One fact as a person would say it. None when there is nothing worth saying."""
    if value is None:
        return None
    if key == "finance_owing":
        if not value.get("owing"):
            return "no finance owing"
        amount = value.get("amount_aud")
        who = ", ".join(value.get("secured_parties") or []) or "an undisclosed lender"
        return f"FINANCE OWING{f' ~${int(amount):,}' if amount else ''} to {who} — settlement required"
    if key == "write_off_status":
        if not value.get("written_off"):
            return "no write-off history"
        return f"WRITTEN OFF ({value.get('type') or 'type unspecified'})"
    if key == "mechanical_faults":
        if value.get("none"):
            return "no mechanical faults reported"
        items = ", ".join(str(i) for i in (value.get("items") or [])) or "unspecified"
        lights = " (warning lights on)" if value.get("warning_lights") else ""
        return f"faults: {items}{lights}"
    if key == "rego_status":
        status = value.get("status")
        if status == "current":
            expiry = value.get("expiry")
            return f"registered{f' until {expiry}' if expiry else ''}"
        return f"registration {status}" if status else None
    if key == "service_history":
        return {
            "full": "full service history",
            "partial": "partial service history",
            "none": "no service history",
        }.get(value, f"service history {value}")
    if key == "panel_paint_condition":
        return f"panel and paint {value}"
    if key == "tyre_condition":
        return {
            "new": "new tyres",
            "good": "tyres good",
            "worn": "tyres worn",
            "replace": "tyres need replacing",
        }.get(value, f"tyres {value}")
    if key == "keys_count":
        return f"{value} key" if value == 1 else f"{value} keys"
    if key == "odometer_km":
        return f"{int(value):,} km"
    return f"{key.replace('_', ' ')}: {value}"


UNVERIFIABLE_BY_CONVERSATION = (
    "panel_paint_condition",
    "mechanical_faults",
    "tyre_condition",
    "keys_count",
    "service_history",
)


def conversation_summary(sheet: FactSheet, offers: list[Offer], contradictions: dict[str, Any]) -> str:
    """Prose a closer can read in one pass — no raw dict dumps, no 'owing False'."""
    veh = " ".join(str(sheet.get(k)) for k in ("year", "make", "model", "variant") if sheet.get(k))
    odo = _render_fact("odometer_km", sheet.get("odometer_km")) or "odometer unconfirmed"
    lines = [f"{veh}, {odo}."]

    facts = [
        _render_fact(spec.key, sheet.get(spec.key))
        for spec in DISCOVERY_REQUIRED
        if spec.key not in {"rego", "photos", "odometer_km"}
    ]
    facts = [f for f in facts if f]
    if facts:
        body = "; ".join(facts)
        lines.append(body[0].upper() + body[1:] + ".")

    unverified = [
        k for k in UNVERIFIABLE_BY_CONVERSATION if sheet.facts.get(k) and not sheet.facts[k].verified
    ]
    if unverified:
        lines.append(
            "Seller's word only, not yet inspected: "
            + ", ".join(k.replace("_", " ") for k in unverified)
            + "."
        )

    if contradictions:
        lines.append(
            "Claims contradicted by verified data: "
            + "; ".join(
                f"{k.replace('_', ' ')} claimed {v['claimed']}, {v['source']} shows {v['actual']}"
                for k, v in contradictions.items()
            )
            + "."
        )
    if offers:
        lines.append(f"Offers: {' → '.join(f'${int(o.amount):,} ({o.ladder_step.value})' for o in offers)}.")
    return " ".join(lines)


def build_packet(
    session: Session, lead: Lead, accepted_offer: Offer, valuation: Valuation, *, sla_hours: int
) -> dict[str, Any]:
    sheet = fact_sheet(session, lead.lead_id)
    seller = lead.upstream_payload.get("seller", {})
    offers = list(
        session.scalars(select(Offer).where(Offer.lead_id == lead.lead_id).order_by(Offer.presented_at))
    )
    comps_row = session.scalars(
        select(MarketData)
        .where(MarketData.lead_id == lead.lead_id, MarketData.kind == MarketDataKind.COMPS)
        .order_by(MarketData.fetched_at.desc())
    ).first()
    ppsr_fo = sheet.get("finance_owing") or {}
    ppsr_wo = sheet.get("write_off_status") or {}
    flags: list[str] = []
    if sheet.contradicted:
        flags.append("contradicted_claims_present")
    if ppsr_fo.get("owing"):
        flags.append("encumbered_settlement_required")
    if sheet.get("seller_phone"):
        flags.append("phone_captured")
    if (sheet.get("rego_status") or {}).get("status") in {"expired", "unregistered"}:
        flags.append("rego_not_current")
    # Section 9: "the flags array carries anything the model noticed that was not a structured field".
    flags += [f"seller_note: {n}" for n in (sheet.get("seller_notes") or [])[:10]]
    channel = None
    for t in lead.threads:
        channel = t.channel.value
    return {
        "lead_id": str(lead.lead_id),
        "seller": {
            "name": seller.get("display_name"),
            "phone": sheet.get("seller_phone") or seller.get("phone"),
            "preferred_contact": channel or "messenger",
        },
        "vehicle": {
            "confirmed": sheet.confirmed,
            "unverified_claims": sheet.claimed,
            "contradictions": sheet.contradicted,
        },
        "ppsr": {
            "encumbered": bool(ppsr_fo.get("owing")),
            "encumbrance_amount_aud": ppsr_fo.get("amount_aud"),
            "written_off": bool(ppsr_wo.get("written_off")),
            "checked_at": ppsr_fo.get("checked_at"),
        },
        "valuation": {
            "band": [float(valuation.band_low), float(valuation.band_high)],
            "wholesale_max": float(valuation.wholesale_max),
            "recon_estimate": float(valuation.recon_estimate),
            "recon_lines": valuation.recon_lines,
            "comps": (comps_row.payload.get("comps") if comps_row else []),
            "computed_at": valuation.computed_at.isoformat(),
            "engine_version": valuation.engine_version,
        },
        "agreed_price_aud": float(accepted_offer.amount),
        "ladder_steps_used": len(offers),
        "offer_expires_at": accepted_offer.expires_at.isoformat(),
        "conversation_summary": conversation_summary(sheet, offers, sheet.contradicted),
        "full_transcript_url": f"/admin/leads/{lead.lead_id}/transcript",
        "flags": flags,
        "next_action": "book_inspection",
        "sla_expires_at": (datetime.now(UTC) + timedelta(hours=sla_hours)).isoformat(),
    }


def write_packet(
    session: Session, lead: Lead, accepted_offer: Offer, valuation: Valuation, *, sla_hours: int
) -> HandoffPacket:
    packet = build_packet(session, lead, accepted_offer, valuation, sla_hours=sla_hours)
    row = HandoffPacket(
        lead_id=lead.lead_id, packet=packet, sla_expires_at=datetime.fromisoformat(packet["sla_expires_at"])
    )
    session.add(row)
    session.flush()
    return row


def transcript(session: Session, lead_id: uuid.UUID) -> list[dict[str, Any]]:
    from acqbot.models import Thread

    msgs = list(
        session.scalars(
            select(Message).where(Message.lead_id == lead_id).order_by(Message.sent_at, Message.msg_id)
        )
    )
    # A conversation can change channel partway through (4.2 Stage 3), and a closer reading the
    # transcript needs to see where — "we said this on Messenger, that on SMS" is the difference
    # between one conversation and two.
    channels = {
        t.thread_id: t.channel.value for t in session.scalars(select(Thread).where(Thread.lead_id == lead_id))
    }
    return [
        {
            "at": m.sent_at.isoformat(),
            "direction": m.direction.value,
            "body": m.body,
            "attachments": m.attachments,
            "model_version": m.model_version,
            "validated": m.validated,
            "channel": channels.get(m.thread_id),
        }
        for m in msgs
    ]
