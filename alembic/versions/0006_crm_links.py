"""Which GoHighLevel contact and opportunity each lead is mirrored to.

A pointer table, mutable like threads: the history of what the lead did is in state_log, and this
only remembers where in GHL we last wrote it.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_links",
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.lead_id"),
            primary_key=True,
        ),
        sa.Column("contact_id", sa.String(64), nullable=False),
        sa.Column("opportunity_id", sa.String(64)),
        sa.Column("synced_state", postgresql.ENUM(name="lead_state", create_type=False)),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_crm_links_contact", "crm_links", ["contact_id"])


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is intentionally unsupported: conversation data is never destroyed by a migration."
    )
