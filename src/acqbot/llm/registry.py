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


# The smallest possible call: does this key work, does this model answer, is there money on the
# account. `model-check` runs it on demand; `review` runs it before spending twenty minutes.
PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def probe(client: ModelClient, model: str, *, effort: str | None = None) -> tuple[bool, str]:
    """(ok, detail). `detail` is a one-line reason on failure, or timing on success."""
    from acqbot.llm.client import ModelError, ModelRequest

    req = ModelRequest(
        purpose="extract",
        model=model,
        system="Reply with JSON matching the schema.",
        messages=[{"role": "user", "content": "Set ok to true."}],
        schema=PROBE_SCHEMA,
        max_tokens=64,
        effort=effort,
    )
    try:
        resp = client.complete(req)
    except ModelError as exc:
        return False, _humanise(str(exc))
    if resp.parsed is None:
        return False, f"answered but not in the requested shape (stop_reason={resp.stop_reason})"
    return True, f"{resp.latency_ms} ms, {resp.input_tokens} in / {resp.output_tokens} out"


def _humanise(error: str) -> str:
    """Turn the API's error into the sentence that says what to do about it."""
    low = error.lower()
    if "credit balance is too low" in low:
        return (
            "the API account has no credits — add some under Plans & Billing at "
            "https://platform.claude.com (the key itself is fine)"
        )
    if "authentication" in low or "invalid x-api-key" in low or "401" in low:
        return "the API key was rejected — check ACQBOT_ANTHROPIC_API_KEY in .env"
    if "not_found" in low or "model" in low and "404" in low:
        return "that model name was not recognised — check ACQBOT_EXTRACTION_MODEL / _CONVERSATION_MODEL"
    if "rate" in low and "limit" in low:
        return "rate limited — wait a moment and try again"
    return error
