"""Which model client the conversation uses — settings-driven, overridable in tests."""

from __future__ import annotations

import logging

from acqbot.config import Settings, get_settings
from acqbot.llm.client import ModelClient

log = logging.getLogger("acqbot.llm")

_override: ModelClient | None = None
_cached: tuple[str, ModelClient] | None = None
_warned_missing_key = False


def missing_key_reason(cfg: Settings) -> str | None:
    """Why the configured provider cannot run, or None when it can."""
    if cfg.resolved_llm_provider == "anthropic" and not cfg.anthropic_api_key:
        return (
            "ACQBOT_LLM_PROVIDER=anthropic but ACQBOT_ANTHROPIC_API_KEY is empty — add the key to .env "
            "(console.anthropic.com → API keys)"
        )
    return None


def set_model_client(client: ModelClient | None) -> None:
    """Force a client (tests, demos). None clears the override."""
    global _override, _cached
    _override = client
    _cached = None


def get_model_client(settings: Settings | None = None) -> ModelClient | None:
    """The configured client, or None when the language model is off (scripted templates only)."""
    global _cached, _warned_missing_key
    if _override is not None:
        return _override
    cfg = settings or get_settings()
    provider = cfg.resolved_llm_provider
    if provider == "off":
        return None
    reason = missing_key_reason(cfg)
    if reason:
        # A misconfigured key must not take the conversation down: run scripted, say so once.
        if not _warned_missing_key:
            log.error("%s; running on scripted templates until it is", reason)
            _warned_missing_key = True
        return None
    key = f"{provider}:{cfg.anthropic_api_key[-6:]}:{cfg.llm_timeout_seconds}:{cfg.llm_max_retries}"
    if _cached and _cached[0] == key:
        return _cached[1]
    if provider == "fake":
        from acqbot.llm.fake import RuleBackedFakeClient

        client: ModelClient = RuleBackedFakeClient()
    else:
        from acqbot.llm.client import AnthropicClient

        client = AnthropicClient(
            cfg.anthropic_api_key, timeout=cfg.llm_timeout_seconds, max_retries=cfg.llm_max_retries
        )
    _cached = (key, client)
    return client
