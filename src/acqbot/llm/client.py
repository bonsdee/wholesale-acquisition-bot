"""Thin model client: one request shape, one response shape, structured JSON output.

Sampling temperature is no longer an API parameter; the "temperature zero" the spec asks of the
extraction step (7.1) is delivered by a strict output schema, low effort, and code-side validation
of every value before it touches the fact store.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("acqbot.llm")


@dataclass
class ModelRequest:
    purpose: str  # extract | generate | summarise
    model: str
    system: str
    messages: list[dict[str, str]]  # [{"role": "user" | "assistant", "content": "..."}]
    schema: dict[str, Any] | None = None  # JSON schema the output must conform to
    max_tokens: int = 512
    effort: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)  # never sent; used by the fake client

    def wire(self) -> dict[str, Any]:
        """Exactly what is sent — stored in model_calls.request and hashed into prompt_hash."""
        return {
            "model": self.model,
            "system": self.system,
            "messages": self.messages,
            "schema": self.schema,
            "max_tokens": self.max_tokens,
            "effort": self.effort,
        }

    def prompt_hash(self, prompt_version: str) -> str:
        payload = json.dumps({"v": prompt_version, **self.wire()}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class ModelResponse:
    text: str
    parsed: dict[str, Any] | None
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "parsed": self.parsed,
            "model": self.model,
            "stop_reason": self.stop_reason,
        }


class ModelError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelClient(Protocol):
    name: str

    def complete(self, req: ModelRequest) -> ModelResponse: ...


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        out = json.loads(text)
    except (TypeError, ValueError):
        return None
    return out if isinstance(out, dict) else None


class AnthropicClient:
    """Claude API via the official SDK, structured outputs through `output_config.format`."""

    name = "anthropic"

    def __init__(self, api_key: str, *, timeout: float = 30.0, max_retries: int = 2) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)

    def complete(self, req: ModelRequest) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": req.messages,
        }
        output_config: dict[str, Any] = {}
        if req.schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": req.schema}
        if req.effort:
            output_config["effort"] = req.effort
        if output_config:
            kwargs["output_config"] = output_config

        started = time.perf_counter()
        try:
            resp = self._call(kwargs)
        except self._anthropic.BadRequestError as exc:
            # Older models may not accept `effort`; drop it once rather than fail the turn.
            if req.effort and "effort" in str(exc).lower():
                kwargs["output_config"].pop("effort", None)
                if not kwargs["output_config"]:
                    kwargs.pop("output_config")
                try:
                    resp = self._call(kwargs)
                except self._anthropic.APIError as exc2:
                    raise ModelError(str(exc2), retryable=False) from exc2
            else:
                raise ModelError(str(exc), retryable=False) from exc
        except (
            self._anthropic.RateLimitError,
            self._anthropic.APIConnectionError,
            self._anthropic.APITimeoutError,
            self._anthropic.InternalServerError,
        ) as exc:
            raise ModelError(str(exc), retryable=True) from exc
        except self._anthropic.APIStatusError as exc:
            raise ModelError(str(exc), retryable=exc.status_code >= 500) from exc
        except Exception as exc:  # noqa: BLE001 — a client-side failure (no key, bad kwargs) is still one turn
            raise ModelError(f"{type(exc).__name__}: {exc}", retryable=False) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        parsed = _parse_json(text) if req.schema is not None and resp.stop_reason == "end_turn" else None
        usage = getattr(resp, "usage", None)
        return ModelResponse(
            text=text,
            parsed=parsed,
            model=getattr(resp, "model", req.model),
            stop_reason=resp.stop_reason,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            latency_ms=latency_ms,
        )

    def _call(self, kwargs: dict[str, Any]) -> Any:
        return self._client.messages.create(**kwargs)
