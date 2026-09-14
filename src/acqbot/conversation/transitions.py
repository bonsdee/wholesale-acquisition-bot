"""State transitions and escalations — the only code path that changes `leads.state`.

Every change is written to state_log with its trigger before the lead row is updated, so the
history is complete even if the lead row is later repaired by hand.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from acqbot.models import Escalation, Lead, LeadState, StateLog

TERMINAL_STATES = frozenset(
    {LeadState.HANDOFF, LeadState.REJECTED, LeadState.ARCHIVED, LeadState.HUMAN, LeadState.TERMINATED}
)


def transition(
    session: Session, lead: Lead, to_state: LeadState, trigger: str, details: dict[str, Any] | None = None
) -> bool:
    """Move the lead to `to_state`. Returns False (and logs nothing) if it is already there."""
    if lead.state == to_state:
        return False
    session.add(
        StateLog(
            lead_id=lead.lead_id, from_state=lead.state, to_state=to_state, trigger=trigger, details=details
        )
    )
    lead.state = to_state
    session.flush()
    return True


def escalate(session: Session, lead: Lead, reason: str, details: dict[str, Any] | None = None) -> Escalation:
    """Route the lead to a human: ESCALATED (the event) then HUMAN (the resting state).

    Idempotent per reason: a lead already in HUMAN with an open escalation for the same reason
    does not get a second one.
    """
    open_same = [
        e
        for e in session.query(Escalation).filter_by(lead_id=lead.lead_id, reason=reason).all()
        if e.resolved_at is None
    ]
    if open_same and lead.state == LeadState.HUMAN:
        return open_same[0]
    esc = Escalation(lead_id=lead.lead_id, reason=reason, details=details)
    session.add(esc)
    transition(session, lead, LeadState.ESCALATED, f"escalation:{reason}", details)
    transition(session, lead, LeadState.HUMAN, "routed_to_human", {"reason": reason})
    session.flush()
    return esc


def create_task(
    session: Session, lead: Lead, reason: str, details: dict[str, Any] | None = None
) -> Escalation:
    """A human to-do that does not take the conversation away from the automation.

    Idempotent per open reason: a second identical open task is not created.
    """
    for e in session.query(Escalation).filter_by(lead_id=lead.lead_id, reason=reason).all():
        if e.resolved_at is None:
            return e
    row = Escalation(lead_id=lead.lead_id, reason=reason, details=details)
    session.add(row)
    session.flush()
    return row


def state_history(session: Session, lead_id: uuid.UUID) -> list[StateLog]:
    return list(session.query(StateLog).filter_by(lead_id=lead_id).order_by(StateLog.at, StateLog.id))
