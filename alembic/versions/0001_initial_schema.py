"""Initial schema — Section 10 tables, job queue, append-only triggers.

Revision ID: 0001
Revises:
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


# --- enum types -----------------------------------------------------------------------

ENUMS: dict[str, list[str]] = {
    "lead_state": [
        "NEW", "CONTACTED", "ENGAGED", "DISCOVERY", "VERIFICATION", "PRICED", "OFFER_MADE",
        "NEGOTIATING", "ACCEPTED", "HANDOFF", "REJECTED", "STALLED", "ARCHIVED", "ESCALATED",
        "HUMAN", "TERMINATED",
    ],
    "channel": ["console", "messenger", "sms"],
    "direction": ["inbound", "outbound"],
    "fact_source": ["seller", "ppsr", "vin", "rego", "photo", "inspection"],
    "market_data_kind": ["guide", "comps"],
    "valuation_basis": ["indicative", "verified"],
    "ladder_step": ["opening", "step_1", "step_2", "floor", "human"],
    "offer_outcome": ["accepted", "rejected", "expired", "superseded"],
    "job_status": ["queued", "running", "done", "failed", "dead"],
}


def _e(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _uuid_pk(name: str) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), primary_key=True)


def _ts(name: str, nullable: bool = False, default_now: bool = True) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default_now else None,
    )


# --- append-only trigger SQL ------------------------------------------------------------

FORBID_FN = """
CREATE OR REPLACE FUNCTION acqbot_forbid_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'acqbot: table % is append-only (% not allowed)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;
"""

VEHICLE_FACTS_FN = """
CREATE OR REPLACE FUNCTION acqbot_vehicle_facts_write_once() RETURNS trigger AS $$
BEGIN
    IF OLD.superseded_by IS NOT NULL THEN
        RAISE EXCEPTION 'acqbot: vehicle_facts.superseded_by is write-once'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.superseded_by IS NULL THEN
        RAISE EXCEPTION 'acqbot: vehicle_facts rows are append-only; only superseded_by may be set'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.fact_id, NEW.lead_id, NEW.field, NEW.value, NEW.confidence, NEW.source, NEW.verified, NEW.recorded_at)
       IS DISTINCT FROM
       (OLD.fact_id, OLD.lead_id, OLD.field, OLD.value, OLD.confidence, OLD.source, OLD.verified, OLD.recorded_at) THEN
        RAISE EXCEPTION 'acqbot: vehicle_facts rows are append-only; only superseded_by may be set'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

OFFERS_FN = """
CREATE OR REPLACE FUNCTION acqbot_offers_write_once() RETURNS trigger AS $$
BEGIN
    IF OLD.outcome IS NOT NULL THEN
        RAISE EXCEPTION 'acqbot: offers.outcome is write-once'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.outcome IS NULL OR NEW.outcome_at IS NULL THEN
        RAISE EXCEPTION 'acqbot: offers rows are append-only; only outcome/outcome_at may be set'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.offer_id, NEW.lead_id, NEW.valuation_id, NEW.amount, NEW.ladder_step, NEW.presented_by,
        NEW.presented_at, NEW.expires_at)
       IS DISTINCT FROM
       (OLD.offer_id, OLD.lead_id, OLD.valuation_id, OLD.amount, OLD.ladder_step, OLD.presented_by,
        OLD.presented_at, OLD.expires_at) THEN
        RAISE EXCEPTION 'acqbot: offers rows are append-only; only outcome/outcome_at may be set'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

ESCALATIONS_FN = """
CREATE OR REPLACE FUNCTION acqbot_escalations_write_once() RETURNS trigger AS $$
BEGIN
    IF OLD.resolved_at IS NOT NULL THEN
        RAISE EXCEPTION 'acqbot: escalations resolution is write-once'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.resolved_at IS NULL OR NEW.resolved_by IS NULL THEN
        RAISE EXCEPTION 'acqbot: escalations rows are append-only; only resolution columns may be set'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.escalation_id, NEW.lead_id, NEW.reason, NEW.details, NEW.at)
       IS DISTINCT FROM
       (OLD.escalation_id, OLD.lead_id, OLD.reason, OLD.details, OLD.at) THEN
        RAISE EXCEPTION 'acqbot: escalations rows are append-only; only resolution columns may be set'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

IMMUTABLE = ["messages", "state_log", "valuations", "market_data", "lead_duplicates"]
WRITE_ONCE = {
    "vehicle_facts": "acqbot_vehicle_facts_write_once",
    "offers": "acqbot_offers_write_once",
    "escalations": "acqbot_escalations_write_once",
}


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "leads",
        _uuid_pk("lead_id"),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("state", _e("lead_state"), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("seller_platform_id", sa.String(128), nullable=False),
        sa.Column("odometer_km", sa.Integer(), nullable=False),
        sa.Column("listing_url", sa.Text()),
        _ts("qualified_at", nullable=True, default_now=False),
        sa.Column("upstream_payload", postgresql.JSONB(), nullable=False),
        _ts("enriched_at", nullable=True, default_now=False),
        sa.Column("enrichment_errors", postgresql.JSONB()),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_leads_dedupe", "leads", ["fingerprint", "seller_platform_id", "created_at"])

    op.create_table(
        "lead_duplicates",
        _uuid_pk("id"),
        sa.Column("duplicate_of", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id"), nullable=False),
        _ts("received_at"),
        sa.Column("upstream_payload", postgresql.JSONB(), nullable=False),
    )

    op.create_table(
        "threads",
        _uuid_pk("thread_id"),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id")),
        sa.Column("channel", _e("channel"), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("referral_ref", sa.String(128)),
        _ts("window_expires_at", nullable=True, default_now=False),
        _ts("last_inbound_at", nullable=True, default_now=False),
        _ts("last_outbound_at", nullable=True, default_now=False),
        _ts("created_at"),
        sa.UniqueConstraint("channel", "external_id", name="uq_threads_channel_external"),
    )

    op.create_table(
        "messages",
        _uuid_pk("msg_id"),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("threads.thread_id"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id")),
        sa.Column("direction", _e("direction"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        _ts("sent_at"),
        sa.Column("external_msg_id", sa.String(256)),
        sa.Column("attachments", postgresql.JSONB()),
        sa.Column("model_version", sa.String(128)),
        sa.Column("prompt_hash", sa.String(64)),
        sa.Column("validated", sa.Boolean()),
        sa.Column("validation_notes", postgresql.JSONB()),
    )
    op.create_index("ix_messages_thread_sent", "messages", ["thread_id", "sent_at"])

    op.create_table(
        "vehicle_facts",
        _uuid_pk("fact_id"),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", _e("fact_source"), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_facts.fact_id")),
        _ts("recorded_at"),
    )
    op.create_index("ix_vehicle_facts_lead_field", "vehicle_facts", ["lead_id", "field", "recorded_at"])

    op.create_table(
        "market_data",
        _uuid_pk("id"),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("kind", _e("market_data_kind"), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _ts("fetched_at"),
    )
    op.create_index("ix_market_data_lead_kind", "market_data", ["lead_id", "kind", "fetched_at"])

    op.create_table(
        "valuations",
        _uuid_pk("valuation_id"),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column("basis", _e("valuation_basis"), nullable=False),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column("band_low", sa.Numeric(12, 2), nullable=False),
        sa.Column("band_high", sa.Numeric(12, 2), nullable=False),
        sa.Column("wholesale_max", sa.Numeric(12, 2), nullable=False),
        sa.Column("ladder", postgresql.JSONB(), nullable=False),
        sa.Column("recon_estimate", sa.Numeric(12, 2), nullable=False),
        sa.Column("recon_lines", postgresql.JSONB(), nullable=False),
        sa.Column("inputs_snapshot", postgresql.JSONB(), nullable=False),
        _ts("computed_at"),
    )
    op.create_index("ix_valuations_lead_computed", "valuations", ["lead_id", "computed_at"])

    op.create_table(
        "offers",
        _uuid_pk("offer_id"),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column(
            "valuation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("valuations.valuation_id"), nullable=False
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("ladder_step", _e("ladder_step"), nullable=False),
        sa.Column("presented_by", sa.String(64), nullable=False),
        _ts("presented_at"),
        _ts("expires_at", nullable=False, default_now=False),
        sa.Column("outcome", _e("offer_outcome")),
        _ts("outcome_at", nullable=True, default_now=False),
    )
    op.create_index("ix_offers_lead_presented", "offers", ["lead_id", "presented_at"])

    op.create_table(
        "state_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column("from_state", _e("lead_state")),
        sa.Column("to_state", _e("lead_state"), nullable=False),
        sa.Column("trigger", sa.String(128), nullable=False),
        sa.Column("details", postgresql.JSONB()),
        _ts("at"),
    )
    op.create_index("ix_state_log_lead_at", "state_log", ["lead_id", "at"])

    op.create_table(
        "escalations",
        _uuid_pk("escalation_id"),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSONB()),
        _ts("at"),
        sa.Column("resolved_by", sa.String(64)),
        sa.Column("resolution", sa.Text()),
        _ts("resolved_at", nullable=True, default_now=False),
    )
    op.create_index("ix_escalations_open", "escalations", ["resolved_at", "at"])

    op.create_table(
        "jobs",
        _uuid_pk("job_id"),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", _e("job_status"), nullable=False),
        sa.Column("dedupe_key", sa.String(256)),
        _ts("run_at"),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("locked_by", sa.String(64)),
        _ts("locked_at", nullable=True, default_now=False),
        _ts("created_at"),
        _ts("finished_at", nullable=True, default_now=False),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_at"])
    op.create_index(
        "uq_jobs_dedupe_active",
        "jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running','failed')"),
    )

    # --- append-only enforcement ---
    op.execute(FORBID_FN)
    op.execute(VEHICLE_FACTS_FN)
    op.execute(OFFERS_FN)
    op.execute(ESCALATIONS_FN)
    for table in IMMUTABLE:
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION acqbot_forbid_change();"
        )
    for table, fn in WRITE_ONCE.items():
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION acqbot_forbid_change();"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_write_once BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {fn}();"
        )


def downgrade() -> None:
    # Conversation data is never destroyed by a migration (Section 11: "no destructive migrations").
    raise RuntimeError("Downgrade of the initial schema is intentionally unsupported.")
