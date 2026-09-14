from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from acqbot.db import session_scope
from acqbot.models import Job, JobStatus
from acqbot.queue import jobs as q
from acqbot.queue.worker import RetryableError, drain, job_handler, run_once

CALLS: list[dict] = []


@job_handler("test_echo")
def _echo(session, payload):
    CALLS.append(payload)


@job_handler("test_boom")
def _boom(session, payload):
    raise RetryableError("boom")


def test_enqueue_claim_complete(session):
    job = q.enqueue(session, "test_echo", {"n": 1})
    session.commit()
    claimed = q.claim(session, "w1")
    assert claimed is not None and claimed.job_id == job.job_id
    assert claimed.status == JobStatus.RUNNING and claimed.attempts == 1 and claimed.locked_by == "w1"
    assert q.claim(session, "w2") is None  # nothing else runnable
    q.complete(session, claimed)
    session.commit()
    assert session.get(Job, job.job_id).status == JobStatus.DONE


def test_dedupe_key_suppresses_active_duplicates(session):
    assert q.enqueue(session, "test_echo", {"n": 1}, dedupe_key="echo:1") is not None
    assert q.enqueue(session, "test_echo", {"n": 1}, dedupe_key="echo:1") is None
    session.commit()
    assert session.query(Job).count() == 1
    # Once it is done, the same key can be queued again.
    j = q.claim(session, "w1")
    q.complete(session, j)
    session.commit()
    assert q.enqueue(session, "test_echo", {"n": 2}, dedupe_key="echo:1") is not None


def test_future_run_at_is_not_claimable(session):
    q.enqueue(session, "test_echo", {"n": 1}, run_at=datetime.now(UTC) + timedelta(hours=1))
    session.commit()
    assert q.claim(session, "w1") is None


def test_fail_backs_off_then_dies(session):
    job = q.enqueue(session, "test_echo", {"n": 1}, max_attempts=2)
    session.commit()
    j = q.claim(session, "w1")
    q.fail(session, j, "first")
    session.commit()
    assert j.status == JobStatus.FAILED and j.run_at > datetime.now(UTC)
    # Make it runnable again and fail a second time → dead.
    session.execute(text("UPDATE jobs SET run_at = now() WHERE job_id = :id"), {"id": job.job_id})
    session.commit()
    j = q.claim(session, "w1")
    q.fail(session, j, "second")
    session.commit()
    assert j.status == JobStatus.DEAD and j.attempts == 2


def test_requeue_stale(session):
    q.enqueue(session, "test_echo", {"n": 1})
    session.commit()
    j = q.claim(session, "w1")
    session.commit()
    session.execute(
        text("UPDATE jobs SET locked_at = now() - interval '1 hour' WHERE job_id = :id"), {"id": j.job_id}
    )
    session.commit()
    assert q.requeue_stale(session, timedelta(minutes=10)) == 1
    session.commit()
    assert session.get(Job, j.job_id).status == JobStatus.FAILED


def test_worker_dispatches_and_records_failures(session):
    CALLS.clear()
    with session_scope() as s:
        q.enqueue(s, "test_echo", {"hello": "world"})
        q.enqueue(s, "test_boom", {}, max_attempts=1)
        q.enqueue(s, "no_such_kind", {})
    assert drain("w-test") == 3
    assert CALLS == [{"hello": "world"}]
    with session_scope() as s:
        by_kind = {j.kind: j for j in s.query(Job).all()}
    assert by_kind["test_echo"].status == JobStatus.DONE
    assert by_kind["test_boom"].status == JobStatus.DEAD and "boom" in by_kind["test_boom"].last_error
    assert by_kind["no_such_kind"].status == JobStatus.DEAD
    assert run_once("w-test") is False
