"""Knowing when it stopped — Section 11, "full trace capture is not optional here".

Two different jobs, and conflating them is how a system reports healthy while the pipeline is dead:

  CRASHES are exceptions. Sentry takes them, with the lead and the job attached. Entirely optional:
  without a DSN every function here is a no-op and nothing imports the SDK.

  QUIET FAILURES are worse and Sentry cannot see them, because nothing raised. A lead sitting past
  its SLA, an agreed deal nobody claimed, a gate refusing every draft, a model key that expired at
  midnight — each of these is the system working exactly as written and getting nothing done. They
  are found by asking the database a question, which is what `checks()` does.

`acqbot doctor` runs the checks by hand; the `health_check` job runs them on a schedule and reports
anything that is not OK; `/health` exposes them so an uptime monitor can watch the same thing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from acqbot.config import Settings, get_settings

log = logging.getLogger("acqbot.observability")

_sentry: Any = None
_started = False


# ------------------------------------------------------------------ crashes


def init_sentry(settings: Settings | None = None) -> bool:
    """Start Sentry if a DSN is configured. Safe to call more than once.

    Returns False when there is no DSN or the SDK is not installed — both are ordinary states, not
    errors: the system runs without error tracking, it just runs blind."""
    global _sentry, _started
    if _started:
        return _sentry is not None
    _started = True
    cfg = settings or get_settings()
    if not cfg.sentry_dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        log.warning("ACQBOT_SENTRY_DSN is set but sentry-sdk is not installed; running without it")
        return False
    sentry_sdk.init(
        dsn=cfg.sentry_dsn,
        environment=cfg.environment,
        release=cfg.release,
        # Conversations carry a seller's words. Nothing here should ship message bodies to a third
        # party, so PII stays off and the events below carry ids and counts only.
        send_default_pii=False,
        traces_sample_rate=0.0,
    )
    _sentry = sentry_sdk
    log.info("sentry enabled (environment=%s)", cfg.environment)
    return True


def capture_exception(exc: BaseException, **context: Any) -> None:
    """Report a crash with the lead or job that caused it. Always logs, whether Sentry is on or not."""
    log.exception("%s: %s", type(exc).__name__, exc, extra={"acqbot": context})
    if _sentry is None:
        return
    with _sentry.push_scope() as scope:
        for k, v in context.items():
            scope.set_tag(k, str(v)) if len(str(v)) < 200 else scope.set_extra(k, v)
        _sentry.capture_exception(exc)


def capture_message(message: str, *, level: str = "warning", **context: Any) -> None:
    log.log(logging.ERROR if level == "error" else logging.WARNING, "%s %s", message, context or "")
    if _sentry is None:
        return
    with _sentry.push_scope() as scope:
        for k, v in context.items():
            scope.set_extra(k, v)
        _sentry.capture_message(message, level=level)


# ------------------------------------------------------------------ quiet failures


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    value: Any = None
    # An unhealthy check that should take the service out of an uptime monitor's rotation, as
    # against one that needs a person but not a page.
    fatal: bool = False


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def serving(self) -> bool:
        """Whether the service can do its job at all. A backlog is a problem; a dead database is an
        outage, and only the second should fail a load balancer's health probe."""
        return all(c.ok for c in self.checks if c.fatal)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "serving": self.serving,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail, "value": c.value} for c in self.checks
            ],
        }


def checks(session: Session, settings: Settings | None = None) -> Report:
    """Ask the database the questions that no exception would ever answer."""
    from acqbot.llm.registry import missing_key_reason
    from acqbot.models import Escalation, HandoffPacket, Job, JobStatus, Message, ModelCall

    cfg = settings or get_settings()
    now = datetime.now(UTC)
    out: list[Check] = []

    # --- can we serve at all
    try:
        session.execute(select(1))
        out.append(Check("database", True, "reachable", fatal=True))
    except Exception as exc:  # pragma: no cover - exercised only when Postgres is down
        out.append(Check("database", False, f"unreachable: {exc}", fatal=True))
        return Report(out)  # nothing below can run

    # --- the queue is moving
    dead = session.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.DEAD)) or 0
    out.append(
        Check("jobs_dead", dead == 0, f"{dead} job(s) exhausted their retries", dead)
        if dead
        else Check("jobs_dead", True, "no dead jobs", 0)
    )

    overdue = (
        session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status == JobStatus.QUEUED, Job.run_at < now - timedelta(minutes=15))
        )
        or 0
    )
    out.append(
        Check(
            "queue_moving",
            overdue == 0,
            f"{overdue} job(s) due more than 15 minutes ago — is a worker running?"
            if overdue
            else "nothing overdue",
            overdue,
        )
    )

    # --- people are being waited on
    sla = now - timedelta(hours=cfg.human_sla_hours)
    late = (
        session.scalar(
            select(func.count())
            .select_from(Escalation)
            .where(Escalation.resolved_at.is_(None), Escalation.at < sla)
        )
        or 0
    )
    out.append(
        Check(
            "human_queue",
            late == 0,
            f"{late} item(s) past the {cfg.human_sla_hours}h SLA" if late else "nothing past SLA",
            late,
        )
    )

    unclaimed = (
        session.scalar(
            select(func.count())
            .select_from(HandoffPacket)
            .where(HandoffPacket.claimed_at.is_(None), HandoffPacket.sla_expires_at < now)
        )
        or 0
    )
    out.append(
        Check(
            "deals_unclaimed",
            unclaimed == 0,
            f"{unclaimed} agreed deal(s) nobody has picked up" if unclaimed else "none waiting",
            unclaimed,
        )
    )

    # --- are we still able to speak
    since = now - timedelta(hours=1)
    recent = list(
        session.scalars(select(Message).where(Message.sent_at > since, Message.direction == "outbound"))
    )
    refused = [m for m in recent if not (m.validation_notes or {}).get("gate", {}).get("ok", True)]
    fallbacks = [m for m in recent if (m.validation_notes or {}).get("generator") == "template_fallback"]
    # A gate that refuses occasionally is the gate working. A gate refusing most of an hour's traffic
    # means the prompt, the ladder or the fact sheet has broken and every seller is getting scripts.
    bad_rate = (len(fallbacks) / len(recent)) if recent else 0.0
    out.append(
        Check(
            "wording",
            bad_rate < 0.5 or len(recent) < 4,
            f"{len(fallbacks)} of {len(recent)} messages in the last hour fell back to the script"
            if recent
            else "no outbound traffic in the last hour",
            round(bad_rate, 3),
        )
    )
    if refused:
        log.info("gate refused %d scripted message(s) in the last hour", len(refused))

    # --- is the model answering
    reason = missing_key_reason(cfg)
    if reason:
        out.append(Check("model", cfg.llm_provider in ("off", "fake"), reason, cfg.llm_provider))
    else:
        calls = list(session.scalars(select(ModelCall).where(ModelCall.at > since)))
        failed = [c for c in calls if c.error]
        rate = (len(failed) / len(calls)) if calls else 0.0
        out.append(
            Check(
                "model",
                rate < 0.25 or len(calls) < 4,
                f"{len(failed)} of {len(calls)} model calls failed in the last hour"
                if calls
                else "no model calls in the last hour",
                round(rate, 3),
            )
        )

    return Report(out)


def report_anomalies(session: Session, settings: Settings | None = None) -> Report:
    """Run the checks and send anything that is not OK where a person will see it."""
    report = checks(session, settings)
    for c in report.failures:
        capture_message(
            f"acqbot check failed: {c.name}",
            level="error" if c.fatal else "warning",
            detail=c.detail,
            value=c.value,
        )
    return report
