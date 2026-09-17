"""Knowing when it stopped.

Section 4.1 names the dangerous failure: "messages that appear sent locally but were never
delivered. The system reports healthy while the pipeline is dead." Every test here is a way of
being dead while looking fine, and the check that catches it.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from acqbot.api.app import create_app
from acqbot.db import session_scope
from acqbot.demo import run_demo
from acqbot.models import HandoffPacket, Job, JobStatus
from acqbot.observability import Report, checks, init_sentry, report_anomalies
from acqbot.queue.jobs import enqueue


def _check(report: Report, name: str):
    return next(c for c in report.checks if c.name == name)


def _run() -> Report:
    with session_scope() as s:
        return checks(s)


# ------------------------------------------------------------------ the default state


def test_a_quiet_system_is_healthy_not_broken():
    # No traffic is not a fault. A check that cries wolf on an empty database gets ignored within
    # a week, and then it is not a check.
    r = _run()
    assert r.ok, [c.detail for c in r.failures]
    assert r.serving
    assert {c.name for c in r.checks} >= {
        "database",
        "jobs_dead",
        "queue_moving",
        "human_queue",
        "deals_unclaimed",
        "wording",
        "model",
    }


def test_sentry_is_optional_and_silent_about_it(settings_env):
    settings_env(sentry_dsn="")
    assert init_sentry() is False  # no DSN, no SDK import, no complaint


# ------------------------------------------------------------------ the quiet failures


def test_a_stopped_worker_is_caught_even_though_nothing_raised(settings_env):
    with session_scope() as s:
        enqueue(s, "nudge", {"lead_id": "x"}, dedupe_key="stale-1")
    with session_scope() as s:
        job = s.scalars(select(Job).where(Job.kind == "nudge")).first()
        job.run_at = datetime.now(UTC) - timedelta(hours=2)
    r = _run()
    c = _check(r, "queue_moving")
    assert not c.ok and "worker" in c.detail
    # But the API is still serving — taking it out of rotation would not start a worker.
    assert r.serving


def test_a_lead_left_waiting_past_the_sla_is_caught(settings_env):
    # `escalations` rows are append-only and the trigger refuses a backdated `at`, so the SLA is
    # shortened to nothing instead — the same condition seen from the other side.
    settings_env(human_sla_hours=0)
    run_demo("clean", "bot_question", seed=42, human_presents=False, model="off")
    c = _check(_run(), "human_queue")
    assert not c.ok and "past the 0h SLA" in c.detail


def test_an_agreed_deal_nobody_picked_up_is_caught(settings_env):
    settings_env(human_sla_hours=0)  # packets are append-only: born expired, not aged
    run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    with session_scope() as s:
        assert s.scalars(select(HandoffPacket)).first() is not None
    c = _check(_run(), "deals_unclaimed")
    assert not c.ok and "nobody has picked up" in c.detail


def test_dead_jobs_are_caught():
    with session_scope() as s:
        enqueue(s, "nudge", {"lead_id": "y"}, dedupe_key="dead-1")
    with session_scope() as s:
        job = s.scalars(select(Job).where(Job.kind == "nudge")).first()
        job.status = JobStatus.DEAD
    c = _check(_run(), "jobs_dead")
    assert not c.ok and "retries" in c.detail


def test_a_database_that_is_gone_fails_the_probe_and_stops_there():
    class _Dead:
        def execute(self, *a, **k):
            raise RuntimeError("connection refused")

    r = checks(_Dead())
    assert not r.ok and not r.serving
    assert len(r.checks) == 1, "no point asking the other questions of a dead database"


# ------------------------------------------------------------------ the endpoint


def test_health_says_what_is_wrong_without_taking_the_service_down(settings_env):
    settings_env(human_sla_hours=0)
    run_demo("clean", "bot_question", seed=42, human_presents=False, model="off")

    r = TestClient(create_app()).get("/health")
    assert r.status_code == 200  # still serving
    body = r.json()
    assert body["ok"] is False and body["serving"] is True
    names = {c["name"]: c for c in body["checks"]}
    assert names["human_queue"]["ok"] is False
    assert "version" in body


def test_reporting_anomalies_never_raises_even_with_nowhere_to_report_to():
    # Sentry is off in tests. The sweep must still run and return a report, because the worker calls
    # it on a timer and an exception there would take the worker down with it.
    with session_scope() as s:
        assert isinstance(report_anomalies(s), Report)
