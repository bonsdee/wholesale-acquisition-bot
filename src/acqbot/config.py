"""Runtime configuration. Every value comes from the environment (prefix ACQBOT_) or a .env file.

Business parameters live here rather than in code so the spec's defaults can be changed
without a deploy. Nothing in this module is read by the language model directly.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The test suite sets ACQBOT_DOTENV=0 so it never reads a developer's .env. Before this, a real
# Anthropic key or ACQBOT_ENVIRONMENT=production in someone's .env reached every test that did not
# override it — spending API credit and failing tests that expect scripted wording.
_ENV_FILE = None if os.environ.get("ACQBOT_DOTENV", "1") == "0" else ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACQBOT_", env_file=_ENV_FILE, extra="ignore")

    # Database
    database_url: str = "postgresql+pg8000://postgres:postgres@127.0.0.1:5432/acqbot"

    # Inbound lead webhook
    lead_webhook_secret: str = "dev-secret-change-me"
    allow_unsigned_leads: bool = False
    webhook_timestamp_tolerance_seconds: int = 300

    # Dealership identity — placeholders until confirmed
    # Direct Auto, a trading name of Stateline Holdings Pty Ltd (ABN 69 695 170 453), 697 Burke Rd,
    # Camberwell. The LMCT is as printed in the directauto.net.au footer — CONFIRM it against the
    # Consumer Affairs Victoria register before go-live: it appears in the first message to every
    # seller, and a wrong licence number there is worse than none.
    dealership_name: str = "Direct Auto"
    dealership_lmct: str = "0013130"  # number only; templates add the "LMCT" prefix
    # The named buyers: Direct Auto's salespeople (client, 21 Sep). These are real people, so the
    # assistant introduces itself as working WITH one of them and never speaks as them. Vic (GM,
    # leads the sales team) and Winnie (interim COO, admin) are not on it. Each seller keeps the same
    # name for the whole conversation (agent_assignments).
    agent_names: list[str] = Field(default_factory=lambda: ["Vikram", "Peter", "Simon", "Robin"])
    # How many live conversations one named buyer may be holding before new leads prefer another
    # name. A soft cap: a seller is never made to wait for a free slot, but `acqbot doctor` says
    # so when every name is full. 0 disables the spreading entirely.
    max_concurrent_per_agent: int = 12

    # Providers
    vin_provider: str = "stub"
    ppsr_provider: str = "stub"
    rego_provider: str = "stub"
    guide_provider: str = "stub"
    comps_provider: str = "stub"

    # Business parameters (spec defaults; margin and transport are placeholders)
    # Below this opening figure the automation does not make an offer at all — a person does, or
    # declines. A placeholder for the dealership to set: under about $1,000 a private-sale car is
    # usually a wrecker's proposition, and "$150, subject to inspection" insults the seller as
    # surely as "$0" did.
    min_offer_aud: int = 1000
    dedupe_window_days: int = 90
    high_value_threshold_aud: int = 60_000
    target_margin_pct: float = 0.10
    target_margin_by_segment: dict[str, float] = Field(default_factory=dict)  # e.g. {"ute": 0.08}
    transport_cost_aud: float = 250.0
    ladder_opening: float = 0.88
    ladder_step_1: float = 0.93
    ladder_step_2: float = 0.97
    offer_expiry_hours: int = 48

    # Behaviour flags
    auto_present_offer: bool = False
    # Phase 6 — channel migration and nudges. The cadence is an open client decision (A.2 "stall
    # recovery cadence"), so these are defaults to be argued with, not findings.
    # Observability (Section 11). All optional: without a DSN the system runs, just blind.
    sentry_dsn: str = ""
    environment: str = "development"
    release: str = ""
    sms_migration: bool = True
    nudge_before_window_closes_hours: int = 20  # inside Messenger's 24h, while we may still send
    sms_nudge_hours: tuple[int, ...] = (24, 72)  # after the first, spaced for SMS courtesy norms
    max_nudges: int = 3
    # Photographs needed to leave discovery. Five, as Direct Auto's own sell form asks (21 Sep). The
    # question names five shots (PHOTO_SHOTS in facts/fields.py); at any other number the wording
    # turns the list into a guide, but change the list too if the shots themselves change.
    min_photos: int = 5
    human_sla_hours: int = 4  # escalation and handoff SLA (A.1 — placeholder until agreed)
    no_progress_turns: int = 3  # consecutive seller turns without progression → escalate (5.2)
    timezone: str = "Australia/Melbourne"

    # Messenger Platform (Stage 1 transport)
    messenger_page_access_token: str = ""
    messenger_app_secret: str = ""
    messenger_verify_token: str = "change-me"
    messenger_page_username: str = "yourpage"  # for m.me/<page>?ref=<lead_id> links

    # SMS (Stage 3 transport)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""  # also verifies inbound webhooks; /webhooks/sms refuses all without it
    # The exact public URL Twilio posts to (https://<host>/webhooks/sms). Twilio signs that URL, and
    # behind a proxy or load balancer the app sees a different one (http, an internal host), which
    # would fail every signature. Empty = use the URL as the app received it.
    twilio_webhook_url: str = ""
    twilio_from_number: str = ""
    # Who speaks first. "messenger": wait for the seller to message the Page from the m.me invite
    # (Section 4.2 Stage 1). "sms": text the seller first on the number in their listing — for a
    # dealership with Twilio but no Messenger access. A lead without a phone waits either way.
    first_contact: str = "messenger"

    # Language model (Phase 4 — discovery only). Section 7.1: a small model extracts, a stronger one writes.
    #   llm_provider: "auto" → anthropic when an API key is set, otherwise off (scripted templates)
    #                 "anthropic" | "fake" (deterministic, no network; for demos and tests) | "off"
    llm_provider: str = "auto"
    anthropic_api_key: str = ""
    extraction_model: str = "claude-haiku-4-5-20251001"
    conversation_model: str = "claude-sonnet-5"
    llm_effort: str | None = "low"  # output_config.effort; short replies want speed, not deliberation
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2  # SDK-level retries on rate limits and connection errors
    llm_gate_retries: int = 1  # rewrite attempts after a gate rejection before the template takes over
    llm_max_output_tokens: int = 1024
    history_verbatim_turns: int = 15  # Section 7.3: last N turns verbatim, older turns as a rolling summary
    history_summary_batch: int = 10  # re-summarise once this many turns have fallen out of the window
    fact_min_confidence: float = 0.7  # model-extracted facts below this are not recorded; we ask again

    # GoHighLevel CRM (Direct Auto sub-account). Empty api key = no sync; empty inbound token = the
    # /webhooks/ghl intake answers 404. The bot keeps texting through Twilio either way: GHL is the
    # dealership's view of the pipeline, not a second channel to the seller.
    ghl_api_key: str = ""  # Private Integration token, created inside the sub-account
    ghl_location_id: str = ""
    ghl_inbound_token: str = ""  # shared secret the GHL workflow webhook sends back to us
    ghl_pipeline_name: str = "Car Acquisition"
    ghl_pipeline_id: str = ""  # optional; wins over the name when set
    ghl_base_url: str = "https://services.leadconnectorhq.com"
    # Public address of this app, for the console link written onto each GHL contact. Empty = no link.
    public_base_url: str = ""

    # Minimal human console (admin endpoints); empty disables them
    admin_token: str = ""

    # Worker
    worker_poll_seconds: float = 1.0
    worker_id: str = "worker-1"

    @field_validator("ladder_opening", "ladder_step_1", "ladder_step_2")
    @classmethod
    def _ladder_below_one(cls, v: float) -> float:
        if not 0 < v < 1:
            raise ValueError("ladder multipliers must be strictly between 0 and 1")
        return v

    @field_validator("agent_names")
    @classmethod
    def _at_least_one_name(cls, v: list[str]) -> list[str]:
        # There used to be a silent fallback to a made-up "Alex". Now that the opening tells every
        # seller which real person the assistant works with, an invented name would be a false
        # statement in the first message — so an empty roster is a startup error instead.
        names = list(dict.fromkeys(n.strip() for n in v if n and n.strip()))
        if not names:
            raise ValueError(
                "agent_names needs at least one name: the opening tells each seller who they'll deal with"
            )
        return names

    @field_validator("min_photos")
    @classmethod
    def _at_least_one_photo(cls, v: int) -> int:
        if v < 1:
            raise ValueError("min_photos must be at least 1 — the dash photo is how the odometer is verified")
        return v

    @field_validator("llm_provider")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"auto", "anthropic", "fake", "off"}:
            raise ValueError("llm_provider must be one of auto, anthropic, fake, off")
        return v

    @field_validator("first_contact")
    @classmethod
    def _known_first_contact(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"messenger", "sms"}:
            raise ValueError("first_contact must be messenger or sms")
        return v

    @field_validator("llm_effort")
    @classmethod
    def _known_effort(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v = v.strip().lower()
        if v not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("llm_effort must be one of low, medium, high, xhigh, max (or empty)")
        return v

    @property
    def is_production(self) -> bool:
        """Real sellers. Test data is refused here (see enrichment/providers.py)."""
        return self.environment.strip().lower() in {"production", "prod"}

    @property
    def ghl_enabled(self) -> bool:
        """Whether lead state is pushed to GoHighLevel."""
        return bool(self.ghl_api_key and self.ghl_location_id)

    @property
    def resolved_llm_provider(self) -> str:
        """'anthropic', 'fake' or 'off' — what the conversation layer will actually use."""
        if self.llm_provider == "auto":
            return "anthropic" if self.anthropic_api_key else "off"
        return self.llm_provider


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: drop the cached Settings so environment changes are picked up."""
    get_settings.cache_clear()
