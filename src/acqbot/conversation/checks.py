"""Checks done by a person — the PPSR search and the rego — until those APIs are connected.

With `ACQBOT_PPSR_PROVIDER=manual` (and the rego likewise) nothing automated verifies finance,
write-off, theft or registration. A person runs the search on ppsr.gov.au, reads the certificate,
and records it here. The facts written are the ones the pipeline writes when a real provider runs
(enrichment/pipeline.py), from the same sources, so nothing downstream can tell who looked — and
nothing needs to.

Section 3.3 makes the PPSR a hard gate: "a written-off or stolen result terminates the lead before
any contact occurs." A person can honour that exactly by running the search before sending the
seller the Page link, and when they do, an adverse result ends the lead untouched. When the search
runs after the conversation has started (Appendix A.1 leaves the timing open), ending it silently
would leave a seller mid-conversation with no reply — so it goes to a person instead, who tells
them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from acqbot.conversation.transitions import escalate, transition
from acqbot.facts.store import fact_sheet, record_fact
from acqbot.models import Direction, Escalation, FactSource, Lead, LeadState, Message
from acqbot.queue.jobs import enqueue

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

# Where recording checks makes no sense: the conversation is over.
_CLOSED = {LeadState.TERMINATED, LeadState.ARCHIVED, LeadState.HANDOFF, LeadState.REJECTED}

# Verified facts a person has now looked at. If the seller said something different, the person has
# seen both; the bot must not then ask the seller "which is right?" about a registry result. (The
# year and the odometer are different: a seller may simply have misremembered, and asking is the
# designed behaviour, Section 6.2.)
_REGISTRY_FIELDS = ("finance_owing", "write_off_status", "stolen", "rego_status")


class ChecksError(ValueError):
    pass


@dataclass
class ManualChecks:
    finance_owing: bool
    written_off: Literal["no", "repairable", "statutory"]
    stolen: bool
    rego_status: Literal["current", "expired", "unregistered"]
    year: int
    amount_owing_aud: float | None = None
    rego_expiry: date | None = None
    vin: str | None = None
    odometer_km: int | None = None  # read off the dash photo
    certificate: str | None = None  # the PPSR search certificate number, for the record


@dataclass
class ChecksOutcome:
    result: Literal["clear", "terminated", "escalated"]
    recorded: list[str] = field(default_factory=list)
    reason: str | None = None


def _contacted(session: Session, lead: Lead) -> bool:
    return bool(
        session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.lead_id == lead.lead_id, Message.direction == Direction.OUTBOUND)
        )
    )


def validate(checks: ManualChecks) -> ManualChecks:
    this_year = datetime.now(UTC).year
    if not 1980 <= checks.year <= this_year + 1:
        raise ChecksError(f"The year should be between 1980 and {this_year + 1}.")
    if checks.finance_owing and checks.amount_owing_aud is not None and checks.amount_owing_aud < 0:
        raise ChecksError("The amount owing can't be negative.")
    if checks.vin:
        checks.vin = checks.vin.strip().upper()
        if not _VIN_RE.match(checks.vin):
            raise ChecksError("The VIN should be 17 characters, without I, O or Q.")
    if checks.odometer_km is not None and not 0 <= checks.odometer_km <= 1_500_000:
        raise ChecksError("That odometer reading doesn't look right.")
    if checks.rego_status == "current" and checks.rego_expiry is None:
        raise ChecksError("Current rego needs its expiry date — it's on the certificate.")
    return checks


def record_checks(session: Session, lead: Lead, checks: ManualChecks, *, by: str) -> ChecksOutcome:
    """Record what the PPSR certificate says, then either clear the lead for pricing or stop it."""
    if lead.state in _CLOSED:
        raise ChecksError(f"This lead is {lead.state.value.lower()} — there's nothing to check.")
    checks = validate(checks)
    now = datetime.now(UTC).isoformat()
    location = (lead.upstream_payload or {}).get("location", {})
    recorded: list[str] = []

    def put(key: str, value: Any, source: FactSource) -> None:
        rr = record_fact(session, lead.lead_id, key, value, source=source, verified=True)
        if rr.created:
            recorded.append(key)

    if checks.vin:
        put("vin", checks.vin, FactSource.PPSR)
    put("year", checks.year, FactSource.PPSR)
    put(
        "finance_owing",
        {
            "owing": checks.finance_owing,
            "amount_aud": checks.amount_owing_aud if checks.finance_owing else None,
            "secured_parties": [],
            "checked_at": now,
            "checked_by": by,
        },
        FactSource.PPSR,
    )
    put(
        "write_off_status",
        {
            "written_off": checks.written_off != "no",
            "type": None if checks.written_off == "no" else checks.written_off,
            "checked_at": now,
            "checked_by": by,
        },
        FactSource.PPSR,
    )
    put("stolen", checks.stolen, FactSource.PPSR)
    put(
        "rego_status",
        {
            "status": checks.rego_status,
            "expiry": checks.rego_expiry.isoformat() if checks.rego_expiry else None,
            "state": location.get("state"),
            "checked_by": by,
        },
        FactSource.REGO,
    )
    if checks.certificate:
        put("ppsr_certificate", checks.certificate.strip()[:64], FactSource.PPSR)
    if checks.odometer_km is not None:
        put("odometer_km", checks.odometer_km, FactSource.PHOTO)

    # The person has seen the seller's answer and the registry's; the bot does not re-litigate it.
    sheet = fact_sheet(session, lead.lead_id)
    seen = [f for f in _REGISTRY_FIELDS if f in sheet.contradicted]
    if seen:
        acked = list(sheet.get("contradictions_acknowledged") or [])
        new = acked + [f for f in seen if f not in acked]
        if new != acked:
            # Same source and confidence the conversation uses when a seller answers "which is
            # right?", so a later acknowledgement by the seller can still supersede this list.
            record_fact(
                session,
                lead.lead_id,
                "contradictions_acknowledged",
                new,
                source=FactSource.SELLER,
                confidence=1.0,
            )

    done = f"PPSR recorded by {by}" + (f" (certificate {checks.certificate})" if checks.certificate else "")
    reasons = ["checks_needed"] + (["verification_review"] if checks.odometer_km is not None else [])
    for e in session.scalars(
        select(Escalation).where(
            Escalation.lead_id == lead.lead_id,
            Escalation.reason.in_(reasons),
            Escalation.resolved_at.is_(None),
        )
    ):
        e.resolved_by, e.resolution, e.resolved_at = by, done, datetime.now(UTC)

    if checks.stolen or checks.written_off != "no":
        reason = "ppsr_stolen" if checks.stolen else "ppsr_written_off"
        details = {
            "written_off": checks.written_off,
            "stolen": checks.stolen,
            "certificate": checks.certificate,
            "recorded_by": by,
        }
        if lead.state == LeadState.NEW and not _contacted(session, lead):
            transition(session, lead, LeadState.TERMINATED, reason, details)
            return ChecksOutcome("terminated", recorded, reason)
        escalate(session, lead, reason, details)
        return ChecksOutcome("escalated", recorded, reason)

    # Before discovery is over, the conversation carries on and prices the car when it gets there;
    # the checks simply won't need doing again. After it, price now.
    if lead.state in {LeadState.VERIFICATION, LeadState.PRICED}:
        enqueue(
            session,
            "value_lead",
            {"lead_id": str(lead.lead_id), "then": "on_priced"},
            dedupe_key=f"value:{lead.lead_id}",
        )
    session.flush()
    return ChecksOutcome("clear", recorded)
