"""Worker loop. Handlers are plain functions registered by job kind.

Each job runs in its own transaction: the claim commits first (so a crash leaves a RUNNING row that
`requeue_stale` recovers), the handler runs in a fresh session, and the result is written in a third.
"""

from __future__ import annotations

import logging
import time
import traceback
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from acqbot.db import session_scope
from acqbot.queue import jobs as q

log = logging.getLogger("acqbot.worker")

Handler = Callable[[Session, dict[str, Any]], None]
_HANDLERS: dict[str, Handler] = {}


def job_handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return deco


def registered_kinds() -> list[str]:
    return sorted(_HANDLERS)


class RetryableError(Exception):
    """Raise from a handler to fail the job with backoff instead of marking it dead immediately."""


def run_once(worker_id: str, kinds: list[str] | None = None) -> bool:
    """Claim and run one job. Returns True if a job was processed."""
    with session_scope() as s:
        job = q.claim(s, worker_id, kinds)
        if job is None:
            return False
        job_id, kind, payload, attempts = job.job_id, job.kind, dict(job.payload), job.attempts

    handler = _HANDLERS.get(kind)
    if handler is None:
        _finish(job_id, error=f"no handler registered for kind {kind!r}", dead=True)
        return True

    try:
        with session_scope() as s:
            handler(s, payload)
    except Exception as exc:  # noqa: BLE001 - the queue is the error boundary
        log.warning("job %s (%s, attempt %s) failed: %s", job_id, kind, attempts, exc)
        _finish(job_id, error="".join(traceback.format_exception(exc))[-4000:])
        return True

    _finish(job_id)
    return True


def _finish(job_id: uuid.UUID, error: str | None = None, dead: bool = False) -> None:
    with session_scope() as s:
        job = q.get_job(s, job_id)
        if job is None:
            return
        if error is None:
            q.complete(s, job)
        else:
            if dead:
                job.attempts = job.max_attempts
            q.fail(s, job, error)


def run_forever(worker_id: str, poll_seconds: float = 1.0, kinds: list[str] | None = None) -> None:
    log.info("worker %s started; handlers: %s", worker_id, registered_kinds())
    last_sweep = 0.0
    while True:
        if time.monotonic() - last_sweep > 60:
            with session_scope() as s:
                n = q.requeue_stale(s, timedelta(minutes=10))
                if n:
                    log.warning("requeued %s stale jobs", n)
            last_sweep = time.monotonic()
        if not run_once(worker_id, kinds):
            time.sleep(poll_seconds)


def drain(worker_id: str = "drain", kinds: list[str] | None = None, max_jobs: int = 10_000) -> int:
    """Run until the queue is empty. Used by tests and the CLI."""
    n = 0
    while n < max_jobs and run_once(worker_id, kinds):
        n += 1
    return n
