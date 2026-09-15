"""Data model — Section 10 of the spec, plus the job queue and two support tables.

Append-only policy (enforced by database triggers in the initial migration, not just by convention):

  strictly immutable   messages, state_log, valuations, market_data, lead_duplicates
  write-once columns   vehicle_facts.superseded_by, offers.outcome/outcome_at,
                       escalations.resolved_by/resolution/resolved_at
                       (NULL → value exactly once; every other column is frozen)
  mutable              leads.state/updated_at/enriched_at/enrichment_errors, threads.*, jobs.*
                       (operational pointers whose history is carried by state_log and messages)

Deviations from the spec's column list are deliberate and small:
  - vehicle_facts.recorded_at instead of updated_at — rows are never updated.
  - valuations carries band_low/band_high/wholesale_max/ladder/basis explicitly rather than a bare `band`.
  - market_data is new: guide and comps results are valuation inputs, not vehicle facts.
  - lead_duplicates is new: suppressed duplicates are kept for the re-engagement policy (A.2).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


# --------------------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------------------


class LeadState(str, enum.Enum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    ENGAGED = "ENGAGED"
    DISCOVERY = "DISCOVERY"
    VERIFICATION = "VERIFICATION"
    PRICED = "PRICED"
    OFFER_MADE = "OFFER_MADE"
    NEGOTIATING = "NEGOTIATING"
    ACCEPTED = "ACCEPTED"
    HANDOFF = "HANDOFF"
    REJECTED = "REJECTED"
    STALLED = "STALLED"
    ARCHIVED = "ARCHIVED"
    ESCALATED = "ESCALATED"
    HUMAN = "HUMAN"
    TERMINATED = "TERMINATED"  # PPSR written-off / stolen: ended before any contact


class Channel(str, enum.Enum):
    CONSOLE = "console"
    MESSENGER = "messenger"
    SMS = "sms"


class Direction(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class FactSource(str, enum.Enum):
    SELLER = "seller"
    PPSR = "ppsr"
    VIN = "vin"
    REGO = "rego"
    PHOTO = "photo"
    INSPECTION = "inspection"


class MarketDataKind(str, enum.Enum):
    GUIDE = "guide"
    COMPS = "comps"


class ValuationBasis(str, enum.Enum):
    INDICATIVE = "indicative"  # may depend on unverified claims — internal triage only
    VERIFIED = "verified"  # every input verified — the only basis that can be released


class LadderStep(str, enum.Enum):
    OPENING = "opening"
    STEP_1 = "step_1"
    STEP_2 = "step_2"
    FLOOR = "floor"
    HUMAN = "human"  # above floor — a human decision


class OfferOutcome(str, enum.Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"  # will retry
    DEAD = "dead"  # exhausted retries


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


LEAD_STATE_T = _enum(LeadState, "lead_state")
CHANNEL_T = _enum(Channel, "channel")
DIRECTION_T = _enum(Direction, "direction")
FACT_SOURCE_T = _enum(FactSource, "fact_source")
MARKET_DATA_KIND_T = _enum(MarketDataKind, "market_data_kind")
VALUATION_BASIS_T = _enum(ValuationBasis, "valuation_basis")
LADDER_STEP_T = _enum(LadderStep, "ladder_step")
OFFER_OUTCOME_T = _enum(OfferOutcome, "offer_outcome")
JOB_STATUS_T = _enum(JobStatus, "job_status")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# --------------------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------------------


class Lead(Base):
    __tablename__ = "leads"

    lead_id: Mapped[uuid.UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[LeadState] = mapped_column(LEAD_STATE_T, nullable=False, default=LeadState.NEW)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    seller_platform_id: Mapped[str] = mapped_column(String(128), nullable=False)
    odometer_km: Mapped[int] = mapped_column(Integer, nullable=False)  # claimed; used by the dedupe tolerance
    listing_url: Mapped[str | None] = mapped_column(Text)
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upstream_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrichment_errors: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    threads: Mapped[list[Thread]] = relationship(back_populates="lead")

    __table_args__ = (Index("ix_leads_dedupe", "fingerprint", "seller_platform_id", "created_at"),)


class LeadDuplicate(Base):
    __tablename__ = "lead_duplicates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    duplicate_of: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.lead_id"), nullable=False)
    received_at: Mapped[datetime] = _now()
    upstream_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class Thread(Base):
    __tablename__ = "threads"

    thread_id: Mapped[uuid.UUID] = _uuid_pk()
    # NULL until the lead arrives — a Messenger referral can land before the upstream webhook.
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.lead_id"))
    channel: Mapped[Channel] = mapped_column(CHANNEL_T, nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)  # PSID / E.164 phone / console id
    referral_ref: Mapped[str | None] = mapped_column(String(128))  # the ?ref= value from m.me links
    window_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    # Section 7.3: turns older than the verbatim window are carried as a rolling summary.
    history_summary: Mapped[str | None] = mapped_column(Text)
    history_summary_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lead: Mapped[Lead | None] = relationship(back_populates="threads")

    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_threads_channel_external"),)


class Message(Base):
    __tablename__ = "messages"

    msg_id: Mapped[uuid.UUID] = _uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("threads.thread_id"), nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.lead_id"))
    direction: Mapped[Direction] = mapped_column(DIRECTION_T, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = _now()
    external_msg_id: Mapped[str | None] = mapped_column(String(256))
    attachments: Mapped[list[Any] | None] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(String(128))  # "template:v1" / model id
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    validated: Mapped[bool | None] = mapped_column(Boolean)
    validation_notes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_messages_thread_sent", "thread_id", "sent_at"),)


class VehicleFact(Base):
    __tablename__ = "vehicle_facts"

    fact_id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.lead_id"), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source: Mapped[FactSource] = mapped_column(FACT_SOURCE_T, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicle_facts.fact_id"))
    recorded_at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_vehicle_facts_lead_field", "lead_id", "field", "recorded_at"),)


class MarketData(Base):
    __tablename__ = "market_data"

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.lead_id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[MarketDataKind] = mapped_column(MARKET_DATA_KIND_T, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_market_data_lead_kind", "lead_id", "kind", "fetched_at"),)


class Valuation(Base):
    __tablename__ = "valuations"

    valuation_id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.lead_id"), nullable=False)
    basis: Mapped[ValuationBasis] = mapped_column(VALUATION_BASIS_T, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    band_low: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    band_high: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    wholesale_max: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    ladder: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recon_estimate: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    recon_lines: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    inputs_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_valuations_lead_computed", "lead_id", "computed_at"),)


class Offer(Base):
    __tablename__ = "offers"

    offer_id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.lead_id"), nullable=False)
    valuation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("valuations.valuation_id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    ladder_step: Mapped[LadderStep] = mapped_column(LADDER_STEP_T, nullable=False)
    presented_by: Mapped[str] = mapped_column(String(64), nullable=False)  # "system" or "human:<name>"
    presented_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[OfferOutcome | None] = mapped_column(OFFER_OUTCOME_T)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_offers_lead_presented", "lead_id", "presented_at"),)


class StateLog(Base):
    __tablename__ = "state_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.lead_id"), nullable=False)
    from_state: Mapped[LeadState | None] = mapped_column(LEAD_STATE_T)
    to_state: Mapped[LeadState] = mapped_column(LEAD_STATE_T, nullable=False)
    trigger: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_state_log_lead_at", "lead_id", "at"),)


class Escalation(Base):
    __tablename__ = "escalations"

    escalation_id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.lead_id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = _now()
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_escalations_open", "resolved_at", "at"),)


class HandoffPacket(Base):
    """Figure 5 — everything the closer needs, written once at ACCEPTED. `claimed_*` are write-once."""

    __tablename__ = "handoff_packets"

    packet_id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.lead_id"), nullable=False)
    packet: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now()
    sla_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_handoff_packets_open", "claimed_at", "sla_expires_at"),)


class Job(Base):
    """Durable Postgres-backed queue. Claimed with FOR UPDATE SKIP LOCKED; retried with backoff."""

    __tablename__ = "jobs"

    job_id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[JobStatus] = mapped_column(JOB_STATUS_T, nullable=False, default=JobStatus.QUEUED)
    dedupe_key: Mapped[str | None] = mapped_column(String(256))
    run_at: Mapped[datetime] = _now()
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_at"),
        Index(
            "uq_jobs_dedupe_active",
            "dedupe_key",
            unique=True,
            postgresql_where="status IN ('queued','running','failed')",
        ),
    )


class ModelCall(Base):
    """One language-model call, stored whole (Section 11: full trace capture is not optional).

    `request` holds the exact system prompt, messages and output schema that were sent, so any
    outbound message can be reconstructed from its `prompt_hash`. Immutable like `messages`.
    """

    __tablename__ = "model_calls"

    call_id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.lead_id"))
    thread_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("threads.thread_id"))
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)  # extract | generate | summarise
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_model_calls_lead_at", "lead_id", "at"),)


IMMUTABLE_TABLES = ("messages", "state_log", "valuations", "market_data", "lead_duplicates", "model_calls")
