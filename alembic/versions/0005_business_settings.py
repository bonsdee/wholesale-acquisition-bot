"""Business parameters the dealership can change from the console: the high-value threshold and the
target margin. Each change is a new row, so the history of who moved them is kept.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_settings",
        sa.Column("setting_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Numeric(14, 4), nullable=False),
        sa.Column("changed_by", sa.String(64), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_business_settings_key_changed", "business_settings", ["key", "changed_at"])
    op.execute(
        "CREATE TRIGGER trg_business_settings_immutable BEFORE UPDATE OR DELETE ON business_settings "
        "FOR EACH ROW EXECUTE FUNCTION acqbot_forbid_change();"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is intentionally unsupported: conversation data is never destroyed by a migration."
    )
