"""Engine and session factory. One engine per process; sessions are short-lived units of work.

The driver is pg8000 — pure Python, no native DLL — because a compiled libpq can be blocked by
Windows Application Control on a locked-down machine. A `postgresql+psycopg://` URL (the earlier
scheme) is accepted and rewritten, so an existing .env keeps working; libpq's `sslmode` query
parameter is translated into what pg8000 understands.
"""

from __future__ import annotations

import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from acqbot.config import get_settings

DRIVER = "pg8000"


def engine_args(database_url: str) -> tuple[str, dict[str, Any]]:
    """Normalise a Postgres URL for pg8000: (url, connect_args).

    `sslmode=disable` → plain; `require`/`prefer`/`allow` → TLS without certificate verification
    (libpq's semantics for those modes); `verify-ca`/`verify-full` → TLS with verification.
    """
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        return database_url, {}
    if url.get_driver_name() != DRIVER:
        url = url.set(drivername=f"postgresql+{DRIVER}")
    query = dict(url.query)
    connect_args: dict[str, Any] = {}
    mode = query.pop("sslmode", None)
    query.pop("sslrootcert", None)
    if mode in {"verify-ca", "verify-full"}:
        connect_args["ssl_context"] = ssl.create_default_context()
    elif mode in {"require", "prefer", "allow"}:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        connect_args["ssl_context"] = ctx
    url = url.set(query=query)
    return url.render_as_string(hide_password=False), connect_args


@lru_cache
def get_engine() -> Engine:
    url, connect_args = engine_args(get_settings().database_url)
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True, future=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on any exception."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Test helper: rebuild the engine after the database URL changes."""
    try:
        get_engine().dispose()
    except Exception:  # pragma: no cover - nothing to dispose yet
        pass
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
