"""Runtime configuration. Every value comes from the environment (prefix ACQBOT_) or a .env file.

Business parameters live here rather than in code so the spec's defaults can be changed
without a deploy. Nothing in this module is read by the language model directly.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACQBOT_", env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+pg8000://postgres:postgres@127.0.0.1:5432/acqbot"

    # Inbound lead webhook
    lead_webhook_secret: str = "dev-secret-change-me"
    allow_unsigned_leads: bool = False
    webhook_timestamp_tolerance_seconds: int = 300

    # Dealership identity — placeholders until confirmed
    dealership_name: str = "Placeholder Motors"
    dealership_lmct: str = "00000"  # number only; templates add the "LMCT" prefix
    agent_names: list[str] = Field(default_factory=lambda: ["Alex"])

    # Providers
    vin_provider: str = "stub"
    ppsr_provider: str = "stub"
    rego_provider: str = "stub"
    guide_provider: str = "stub"
    comps_provider: str = "stub"

    # Business parameters (spec defaults; margin and transport are placeholders)
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
    min_photos: int = 6
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
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

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

    @field_validator("llm_provider")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"auto", "anthropic", "fake", "off"}:
            raise ValueError("llm_provider must be one of auto, anthropic, fake, off")
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
