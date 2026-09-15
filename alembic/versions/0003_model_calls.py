"""Model call trace (Section 11) and the rolling history summary (Section 7.3).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("call_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id")),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("threads.thread_id")),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("response", postgresql.JSONB()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_model_calls_lead_at", "model_calls", ["lead_id", "at"])
    op.execute(
        "CREATE TRIGGER trg_model_calls_immutable BEFORE UPDATE OR DELETE ON model_calls "
        "FOR EACH ROW EXECUTE FUNCTION acqbot_forbid_change();"
    )
    op.add_column("threads", sa.Column("history_summary", sa.Text()))
    op.add_column("threads", sa.Column("history_summary_through", sa.DateTime(timezone=True)))


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is intentionally unsupported: conversation data is never destroyed by a migration."
    )
