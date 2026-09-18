"""Which named buyer each lead was given to — decided once, at first contact.

Before this, the agent name was a hash of the lead id computed fresh on every message. That was
stable, but blind: nothing stopped every lead in a busy week landing on the same name, and a seller
who mentions their mate also dealt with "Alex" is a conversation nobody wants to have.

Storing the decision is what lets it be made on load instead of on a hash, and the immutability
trigger is what guarantees the name a seller sees never changes underneath them.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_assignments",
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.lead_id"),
            primary_key=True,
        ),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("over_cap", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index("ix_agent_assignments_agent", "agent_assignments", ["agent"])
    op.execute(
        "CREATE TRIGGER trg_agent_assignments_immutable BEFORE UPDATE OR DELETE ON agent_assignments "
        "FOR EACH ROW EXECUTE FUNCTION acqbot_forbid_change();"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is intentionally unsupported: conversation data is never destroyed by a migration."
    )
