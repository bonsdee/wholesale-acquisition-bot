"""Durable Postgres-backed job queue.

Why not Celery/Redis: at this volume durability matters more than throughput (Section 11), Redis is
another hosted dependency, and Celery is awkward on Windows. A jobs table claimed with
`FOR UPDATE SKIP LOCKED` gives exactly-once claiming across any number of worker processes, survives
restarts, and keeps every job's history in the same database as everything else.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from acqbot.models import Job, JobStatus


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> Job | None:
    """Queue a job. With a dedupe_key, an identical active job makes this a no-op (returns None)."""
    job = Job(
        kind=kind,
        payload=payload,
        status=JobStatus.QUEUED,
        dedupe_key=dedupe_key,
        run_at=run_at or datetime.now(UTC),
        attempts=0,
        max_attempts=max_attempts,
    )
    if dedupe_key is None:
        session.add(job)
        session.flush()
        return job
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        return None
    return job


def claim(session: Session, worker_id: str, kinds: list[str] | None = None) -> Job | None:
    """Atomically claim the next runnable job, or None. Commits nothing — caller owns the transaction."""
    kind_filter = "AND kind = ANY(:kinds)" if kinds else ""
    stmt = text(
        f"""
        UPDATE jobs
           SET status = 'running', locked_by = :worker_id, locked_at = now(), attempts = attempts + 1
         WHERE job_id = (
               SELECT job_id FROM jobs
                WHERE status IN ('queued', 'failed') AND run_at <= now() {kind_filter}
                ORDER BY run_at, created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
         )
     RETURNING job_id
        """
    )
    params: dict[str, Any] = {"worker_id": worker_id}
    if kinds:
        params["kinds"] = kinds
    row = session.execute(stmt, params).first()
    if row is None:
        return None
    # populate_existing: the identity map may hold a stale copy from an earlier enqueue().
    return session.get(Job, row[0], populate_existing=True)


def complete(session: Session, job: Job) -> None:
    job.status = JobStatus.DONE
    job.finished_at = datetime.now(UTC)
    job.locked_by = None
    session.flush()


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(600, 5 * (2 ** max(0, attempts - 1))))


def fail(session: Session, job: Job, error: str) -> None:
    job.last_error = error[:4000]
    job.locked_by = None
    if job.attempts >= job.max_attempts:
        job.status = JobStatus.DEAD
        job.finished_at = datetime.now(UTC)
    else:
        job.status = JobStatus.FAILED
        job.run_at = datetime.now(UTC) + backoff(job.attempts)
    session.flush()


def requeue_stale(session: Session, older_than: timedelta = timedelta(minutes=10)) -> int:
    """Jobs stuck in RUNNING beyond `older_than` (worker died mid-job) go back to the queue."""
    cutoff = datetime.now(UTC) - older_than
    stale = list(session.scalars(select(Job).where(Job.status == JobStatus.RUNNING, Job.locked_at < cutoff)))
    for job in stale:
        job.status = JobStatus.FAILED if job.attempts < job.max_attempts else JobStatus.DEAD
        job.last_error = "requeued: worker lock expired"
        job.locked_by = None
    session.flush()
    return len(stale)


def pending_count(session: Session, kind: str | None = None) -> int:
    q = session.query(Job).filter(Job.status.in_([JobStatus.QUEUED, JobStatus.FAILED, JobStatus.RUNNING]))
    if kind:
        q = q.filter(Job.kind == kind)
    return q.count()


def get_job(session: Session, job_id: uuid.UUID) -> Job | None:
    return session.get(Job, job_id)
