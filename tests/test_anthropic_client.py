"""The real client, minus the network: request shape, response parsing, error mapping."""

import json

import anthropic
import httpx
import pytest
from anthropic.types import Message, TextBlock, Usage

from acqbot.llm.client import AnthropicClient, ModelError, ModelRequest

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _sdk_message(text: str, *, stop_reason: str = "end_turn", model: str = "claude-sonnet-5") -> Message:
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model=model,
        content=[TextBlock(type="text", text=text)],
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=123, output_tokens=7),
    )


def _status_error(cls, status: int, message: str):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status, request=req, json={"error": {"message": message}})
    return cls(message, response=resp, body={"error": {"message": message}})


def _client(monkeypatch, responses):
    client = AnthropicClient("sk-test", timeout=5, max_retries=0)
    calls = []

    def fake_call(kwargs):
        calls.append(json.loads(json.dumps(kwargs)))  # snapshot: the retry path edits the dict in place
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(client, "_call", fake_call)
    return client, calls


def _req(**over):
    base = dict(
        purpose="generate",
        model="claude-sonnet-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        schema=SCHEMA,
        max_tokens=64,
        effort="low",
    )
    base.update(over)
    return ModelRequest(**base)


def test_request_shape_and_structured_output_parsing(monkeypatch):
    client, calls = _client(monkeypatch, [_sdk_message('{"ok": true}')])
    resp = client.complete(_req())
    kw = calls[0]
    assert kw["model"] == "claude-sonnet-5" and kw["system"] == "sys" and kw["max_tokens"] == 64
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"}
    assert "temperature" not in kw
    assert resp.parsed == {"ok": True} and resp.text == '{"ok": true}'
    assert resp.input_tokens == 123 and resp.output_tokens == 7 and resp.model == "claude-sonnet-5"
    assert resp.stop_reason == "end_turn" and resp.latency_ms >= 0


def test_truncated_or_refused_output_is_not_parsed(monkeypatch):
    client, _ = _client(monkeypatch, [_sdk_message('{"ok": tr', stop_reason="max_tokens")])
    assert client.complete(_req()).parsed is None
    client, _ = _client(monkeypatch, [_sdk_message("I can't help with that", stop_reason="refusal")])
    assert client.complete(_req()).parsed is None


def test_no_schema_means_no_output_format(monkeypatch):
    client, calls = _client(monkeypatch, [_sdk_message("plain text")])
    resp = client.complete(_req(schema=None, effort=None))
    assert "output_config" not in calls[0] and resp.parsed is None and resp.text == "plain text"


def test_effort_rejected_by_an_older_model_is_dropped_once(monkeypatch):
    err = _status_error(
        anthropic.BadRequestError, 400, "output_config.effort: Extra inputs are not permitted"
    )
    client, calls = _client(monkeypatch, [err, _sdk_message('{"ok": true}')])
    resp = client.complete(_req())
    assert resp.parsed == {"ok": True}
    assert "effort" in calls[0]["output_config"] and "effort" not in calls[1]["output_config"]


def test_errors_map_to_retryable_or_not(monkeypatch):
    client, _ = _client(monkeypatch, [_status_error(anthropic.RateLimitError, 429, "slow down")])
    with pytest.raises(ModelError) as e:
        client.complete(_req())
    assert e.value.retryable
    client, _ = _client(monkeypatch, [_status_error(anthropic.BadRequestError, 400, "bad schema")])
    with pytest.raises(ModelError) as e:
        client.complete(_req())
    assert not e.value.retryable
    client, _ = _client(monkeypatch, [_status_error(anthropic.InternalServerError, 500, "oops")])
    with pytest.raises(ModelError) as e:
        client.complete(_req())
    assert e.value.retryable


def test_prompt_hash_is_stable_and_covers_the_whole_request():
    a, b = _req(), _req()
    assert a.prompt_hash("prompt:v1") == b.prompt_hash("prompt:v1")
    assert a.prompt_hash("prompt:v1") != a.prompt_hash("prompt:v2")
    assert a.prompt_hash("prompt:v1") != _req(system="sys2").prompt_hash("prompt:v1")
    assert json.loads(json.dumps(a.wire()))["schema"] == SCHEMA


def test_missing_key_is_a_model_error_not_a_crash():
    client = AnthropicClient("", timeout=5, max_retries=0)  # SDK raises TypeError before any request
    with pytest.raises(ModelError) as e:
        client.complete(_req())
    assert not e.value.retryable and "authentication" in str(e.value).lower()


def test_explicit_anthropic_without_a_key_runs_scripted_and_says_why():
    from acqbot.config import Settings
    from acqbot.llm.registry import get_model_client, missing_key_reason

    cfg = Settings(llm_provider="anthropic", anthropic_api_key="")
    assert "ACQBOT_ANTHROPIC_API_KEY is empty" in missing_key_reason(cfg)
    assert get_model_client(cfg) is None
    assert missing_key_reason(Settings(llm_provider="auto", anthropic_api_key="")) is None
    assert missing_key_reason(Settings(llm_provider="anthropic", anthropic_api_key="sk-x")) is None
