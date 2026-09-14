"""Handoff packets (Figure 5).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

WRITE_ONCE_FN = """
CREATE OR REPLACE FUNCTION acqbot_handoff_write_once() RETURNS trigger AS $$
BEGIN
    IF OLD.claimed_at IS NOT NULL THEN
        RAISE EXCEPTION 'acqbot: handoff_packets claim is write-once'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.claimed_at IS NULL OR NEW.claimed_by IS NULL THEN
        RAISE EXCEPTION 'acqbot: handoff_packets rows are append-only; only claimed_by/claimed_at may be set'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.packet_id, NEW.lead_id, NEW.packet, NEW.created_at, NEW.sla_expires_at)
       IS DISTINCT FROM
       (OLD.packet_id, OLD.lead_id, OLD.packet, OLD.created_at, OLD.sla_expires_at) THEN
        RAISE EXCEPTION 'acqbot: handoff_packets rows are append-only; only claimed_by/claimed_at may be set'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "handoff_packets",
        sa.Column("packet_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column("packet", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sla_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(64)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_handoff_packets_open", "handoff_packets", ["claimed_at", "sla_expires_at"])
    op.execute(WRITE_ONCE_FN)
    op.execute(
        "CREATE TRIGGER trg_handoff_packets_no_delete BEFORE DELETE ON handoff_packets "
        "FOR EACH ROW EXECUTE FUNCTION acqbot_forbid_change();"
    )
    op.execute(
        "CREATE TRIGGER trg_handoff_packets_write_once BEFORE UPDATE ON handoff_packets "
        "FOR EACH ROW EXECUTE FUNCTION acqbot_handoff_write_once();"
    )


def downgrade() -> None:
    raise RuntimeError("Downgrade is intentionally unsupported: conversation data is never destroyed by a migration.")
