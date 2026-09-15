"""Model clients that never touch the network.

RuleBackedFakeClient answers extraction with the Phase 3 regex parsers (in the model's wire
format, so the coercion path is exercised) and generation with the scripted template the planner
would otherwise have sent. It makes `acqbot demo --model fake` and the end-to-end tests run the
whole Phase 4 plumbing — schemas, coercion, gate retries, trace rows, history summaries —
deterministically. ScriptedFakeClient layers canned responses on top for unit tests.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from acqbot.conversation.extract import extract
from acqbot.llm.client import ModelError, ModelRequest, ModelResponse


def _wire_value(key: str, value: Any) -> str | None:
    if key == "finance_owing" and isinstance(value, dict):
        if not value.get("owing"):
            return "none"
        amt = value.get("amount_aud")
        return f"owing:{int(amt)}" if amt else "owing"
    if key == "write_off_status" and isinstance(value, dict):
        if not value.get("written_off"):
            return "none"
        return f"written_off:{value['type']}" if value.get("type") else "written_off"
    if key == "mechanical_faults" and isinstance(value, dict):
        if value.get("none"):
            return "none"
        items = list(value.get("items") or [])
        if value.get("warning_lights") and not any("light" in i for i in items):
            items.append("warning light")
        return "; ".join(items) if items else None
    if key == "rego_status" and isinstance(value, dict):
        status = value.get("status")
        if status == "current":
            exp = value.get("expiry")
            return f"current:{exp[:7]}" if exp else "current"
        return status
    if value is None:
        return None
    return str(value)


def _response(parsed: dict[str, Any], model: str) -> ModelResponse:
    return ModelResponse(
        text=json.dumps(parsed, ensure_ascii=False),
        parsed=parsed,
        model=model,
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
    )


class RuleBackedFakeClient:
    name = "fake"
    model = "fake:rule-backed"

    def complete(self, req: ModelRequest) -> ModelResponse:
        if req.purpose == "extract":
            return self._extract(req)
        if req.purpose == "generate":
            return self._generate(req)
        if req.purpose == "summarise":
            n = req.meta.get("turn_count", 0)
            existing = req.meta.get("existing") or ""
            summary = f"{existing} [{n} earlier turns folded in]".strip()
            return _response({"summary": summary[:600]}, self.model)
        raise ModelError(f"unknown purpose {req.purpose}", retryable=False)

    def _extract(self, req: ModelRequest) -> ModelResponse:
        m = req.meta
        ex = extract(
            m.get("body", ""),
            m.get("attachments") or [],
            pending_field=m.get("pending_field"),
            stage_priced=bool(m.get("stage_priced")),
        )
        facts = []
        for key, value in ex.facts.items():
            wire = _wire_value(key, value)
            if wire is not None:
                facts.append({"field": key, "value": wire, "confidence": 1.0})
        intents = sorted(ex.intents)
        if "stop" in ex.flags:
            intents.append("stop")
        parsed = {
            "facts": facts,
            "answered_pending": ex.parsed_pending,
            "intents": intents,
            "flags": sorted(f for f in ex.flags if f != "stop"),
            "counter_price_aud": ex.counter_price,
            "phone": ex.phone,
            "seller_question": None,
            "notes": [],
        }
        return _response(parsed, self.model)

    def _generate(self, req: ModelRequest) -> ModelResponse:
        m = req.meta
        parsed = {
            "message": m.get("fallback_body", ""),
            "proposed_state": m.get("stage", "DISCOVERY"),
            "confidence": 0.9,
            "escalate": False,
            "escalate_reason": None,
        }
        return _response(parsed, self.model)


class ScriptedFakeClient:
    """Canned responses per purpose, in order; falls through to a default client when the script runs out.

    A queued item may be a dict (returned as the parsed output), an Exception (raised), or a
    callable taking the request and returning a dict.
    """

    name = "fake"
    model = "fake:scripted"

    def __init__(self, default: Any | None = None) -> None:
        self.default = default or RuleBackedFakeClient()
        self.queues: dict[str, deque[Any]] = {}
        self.requests: list[ModelRequest] = []

    def queue(self, purpose: str, *items: Any) -> ScriptedFakeClient:
        self.queues.setdefault(purpose, deque()).extend(items)
        return self

    def complete(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        q = self.queues.get(req.purpose)
        if not q:
            return self.default.complete(req)
        item = q.popleft()
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(req)
        return _response(dict(item), self.model)
