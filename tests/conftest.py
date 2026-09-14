"""Test fixtures. Tests run against a real Postgres (the append-only triggers are the point).

Set ACQBOT_TEST_DATABASE_URL to override the default local database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

TEST_DB_URL = os.environ.get(
    "ACQBOT_TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/acqbot_test"
)
# The suite drops and recreates the public schema. Refuse anything that does not look like a test database.
_dbname = TEST_DB_URL.rsplit("/", 1)[-1].split("?", 1)[0]
if "test" not in _dbname.lower():
    raise SystemExit(
        f"Refusing to run tests against database {_dbname!r}: the suite wipes the schema. "
        "Point ACQBOT_TEST_DATABASE_URL at a database whose name contains 'test'."
    )
os.environ["ACQBOT_DATABASE_URL"] = TEST_DB_URL
os.environ["ACQBOT_LEAD_WEBHOOK_SECRET"] = "test-secret"
os.environ["ACQBOT_ALLOW_UNSIGNED_LEADS"] = "false"
os.environ.setdefault("ACQBOT_HIGH_VALUE_THRESHOLD_AUD", "60000")

ROOT = Path(__file__).resolve().parents[1]

import acqbot.queue.handlers  # noqa: E402,F401  (register job handlers)
from acqbot.config import reset_settings_cache  # noqa: E402
from acqbot.db import get_engine, get_sessionmaker, reset_engine_cache  # noqa: E402
from acqbot.enrichment.providers import reset_providers_cache  # noqa: E402
from acqbot.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    reset_settings_cache()
    reset_engine_cache()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    engine = get_engine()
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    reset_settings_cache()
    reset_providers_cache()


@pytest.fixture
def session() -> Iterator[Session]:
    s = get_sessionmaker()()
    try:
        yield s
        s.commit()
    finally:
        s.close()


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch):
    """Set ACQBOT_* variables for one test and refresh the cached settings."""

    def _set(**kwargs: str) -> None:
        for k, v in kwargs.items():
            monkeypatch.setenv(f"ACQBOT_{k.upper()}", str(v))
        reset_settings_cache()
        reset_providers_cache()

    return _set
