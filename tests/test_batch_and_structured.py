"""Tests for batch.py + structured outputs (messages_create_json)."""
from __future__ import annotations
import os
import sys
from unittest.mock import MagicMock


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.anthropic_client import AnthropicClient
from solo_founder_os.batch import (
    batch_request,
    batch_submit,
    batch_status,
    batch_results,
    batch_wait,
)
from solo_founder_os.testing import fake_anthropic, fake_anthropic_raises


# ─── messages_create_json (structured outputs) ───────────────

def _client_pre_loaded(monkeypatch, sdk_client) -> AnthropicClient:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    c = AnthropicClient(usage_log_path=None)
    c._client = sdk_client
    return c


def test_structured_returns_parsed_dict(monkeypatch):
    fake = fake_anthropic('{"name": "Alex", "age": 99}')
    client = _client_pre_loaded(monkeypatch, fake)
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    result, err = client.messages_create_json(
        schema=schema, model="x", max_tokens=100,
        messages=[{"role": "user", "content": "hi"}],
    )
    assert err is None
    assert result == {"name": "Alex", "age": 99}


def test_structured_sets_beta_header(monkeypatch):
    """The beta header is always set even if caller passes their own."""
    captured: dict = {}

    def capture(**kwargs):
        captured.update(kwargs)
        block = MagicMock(); block.text = "{}"; block.type = "text"
        resp = MagicMock(); resp.content = [block]
        return resp

    fake = MagicMock()
    fake.messages.create.side_effect = capture
    client = _client_pre_loaded(monkeypatch, fake)
    client.messages_create_json(
        schema={"type": "object"}, model="x", max_tokens=10,
        messages=[{"role": "user", "content": "hi"}],
    )
    headers = captured.get("extra_headers", {})
    assert headers.get("anthropic-beta") == "structured-outputs-2025-11-13"
    # output_config was passed
    oc = captured.get("output_config")
    assert oc and oc.get("format", {}).get("type") == "json_schema"


def test_structured_empty_response_returns_error(monkeypatch):
    fake = fake_anthropic("")
    client = _client_pre_loaded(monkeypatch, fake)
    result, err = client.messages_create_json(
        schema={"type": "object"}, model="x", max_tokens=10, messages=[])
    assert result is None
    assert "empty" in err.lower()


def test_structured_unparseable_returns_error(monkeypatch):
    """If the API returns non-JSON despite the beta flag, we still surface
    an error rather than crashing."""
    fake = fake_anthropic("this is not json")
    client = _client_pre_loaded(monkeypatch, fake)
    result, err = client.messages_create_json(
        schema={"type": "object"}, model="x", max_tokens=10, messages=[])
    assert result is None
    assert "parse failed" in err


def test_structured_propagates_api_error(monkeypatch):
    fake = fake_anthropic_raises(Exception("rate limit"))
    client = _client_pre_loaded(monkeypatch, fake)
    result, err = client.messages_create_json(
        schema={"type": "object"}, model="x", max_tokens=10, messages=[])
    assert result is None
    assert "rate limit" in err


def test_structured_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = AnthropicClient()
    result, err = c.messages_create_json(
        schema={"type": "object"}, model="x", max_tokens=10, messages=[])
    assert result is None
    assert "missing env var" in err


# ─── batch.py ────────────────────────────────────────────────

def test_batch_request_shape():
    req = batch_request(
        custom_id="key-001",
        model="claude-haiku-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": "hi"}],
    )
    assert req["custom_id"] == "key-001"
    assert req["params"]["model"] == "claude-haiku-4-5"
    assert req["params"]["messages"] == [{"role": "user", "content": "hi"}]


def test_batch_request_includes_system():
    req = batch_request(
        custom_id="x", model="m", max_tokens=10,
        messages=[], system="be helpful",
    )
    assert req["params"]["system"] == "be helpful"


def test_batch_request_forwards_extra_kwargs():
    req = batch_request(
        custom_id="x", model="m", max_tokens=10,
        messages=[], output_config={"format": "json_schema"},
    )
    assert req["params"]["output_config"] == {"format": "json_schema"}


def test_batch_submit_empty_returns_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    c = AnthropicClient()
    bid, err = batch_submit(c, [])
    assert bid is None
    assert "empty" in err


def test_batch_submit_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = AnthropicClient()
    bid, err = batch_submit(c, [{"custom_id": "x", "params": {}}])
    assert bid is None
    assert "missing env var" in err


def test_batch_submit_success(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake_sdk = MagicMock()
    fake_batch = MagicMock(); fake_batch.id = "msgbatch_abc123"
    fake_sdk.messages.batches.create.return_value = fake_batch
    c = AnthropicClient()
    c._client = fake_sdk
    bid, err = batch_submit(c, [batch_request(
        custom_id="k0", model="m", max_tokens=10,
        messages=[{"role": "user", "content": "hi"}])])
    assert err is None
    assert bid == "msgbatch_abc123"


def test_batch_submit_propagates_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake_sdk = MagicMock()
    fake_sdk.messages.batches.create.side_effect = Exception("auth failed")
    c = AnthropicClient()
    c._client = fake_sdk
    bid, err = batch_submit(c, [batch_request(
        custom_id="k0", model="m", max_tokens=10, messages=[])])
    assert bid is None
    assert "auth failed" in err


def test_batch_status_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake_sdk = MagicMock()
    fake_batch = MagicMock()
    fake_batch.id = "b1"
    fake_batch.processing_status = "in_progress"
    fake_batch.request_counts = MagicMock(succeeded=0, errored=0)
    fake_batch.created_at = "2026-04-30T00:00:00Z"
    fake_batch.ended_at = None
    fake_batch.expires_at = "2026-05-30T00:00:00Z"
    fake_sdk.messages.batches.retrieve.return_value = fake_batch
    c = AnthropicClient()
    c._client = fake_sdk
    status, err = batch_status(c, "b1")
    assert err is None
    assert status["processing_status"] == "in_progress"
    assert status["id"] == "b1"


def test_batch_results_succeeded_entry(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake_sdk = MagicMock()
    # Mock the streaming results iterator
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="hello world")]
    msg.usage = MagicMock(input_tokens=50, output_tokens=10,
                            cache_read_input_tokens=0,
                            cache_creation_input_tokens=0)
    msg.stop_reason = "end_turn"
    result_obj = MagicMock(type="succeeded", message=msg)
    entry = MagicMock(custom_id="k0", result=result_obj)
    fake_sdk.messages.batches.results.return_value = iter([entry])
    c = AnthropicClient()
    c._client = fake_sdk
    out, err = batch_results(c, "b1")
    assert err is None
    assert "k0" in out
    assert out["k0"]["content"][0]["text"] == "hello world"
    assert out["k0"]["usage"]["input_tokens"] == 50


def test_batch_results_errored_entry(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake_sdk = MagicMock()
    err_obj = MagicMock(message="quota exceeded", type="quota_exceeded")
    result_obj = MagicMock(type="errored", error=err_obj, message=None)
    entry = MagicMock(custom_id="k1", result=result_obj)
    fake_sdk.messages.batches.results.return_value = iter([entry])
    c = AnthropicClient()
    c._client = fake_sdk
    out, err = batch_results(c, "b1")
    assert err is None
    assert out["k1"]["error_type"] == "errored"
    assert "quota" in out["k1"]["error_message"]


def test_batch_wait_polls_until_ended(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake_sdk = MagicMock()

    # First retrieve: in_progress; second retrieve: ended
    in_progress = MagicMock()
    in_progress.processing_status = "in_progress"
    in_progress.id = "b1"
    in_progress.request_counts = None
    in_progress.created_at = None
    in_progress.ended_at = None
    in_progress.expires_at = None

    ended = MagicMock()
    ended.processing_status = "ended"
    ended.id = "b1"
    ended.request_counts = MagicMock(succeeded=1, errored=0)
    ended.created_at = None
    ended.ended_at = None
    ended.expires_at = None

    fake_sdk.messages.batches.retrieve.side_effect = [in_progress, ended]

    msg = MagicMock()
    msg.content = [MagicMock(type="text", text="ok")]
    msg.usage = MagicMock(input_tokens=1, output_tokens=1,
                            cache_read_input_tokens=0,
                            cache_creation_input_tokens=0)
    msg.stop_reason = "end_turn"
    result_obj = MagicMock(type="succeeded", message=msg)
    entry = MagicMock(custom_id="k0", result=result_obj)
    fake_sdk.messages.batches.results.return_value = iter([entry])

    c = AnthropicClient()
    c._client = fake_sdk

    sleep_calls = []
    out, err = batch_wait(c, "b1", poll_interval_s=1.0,
                            sleep_fn=lambda s: sleep_calls.append(s))
    assert err is None
    assert "k0" in out
    # One sleep between retrieve#1 (in_progress) and retrieve#2 (ended)
    assert sleep_calls == [1.0]


def test_batch_wait_timeout(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake_sdk = MagicMock()
    in_progress = MagicMock()
    in_progress.processing_status = "in_progress"
    in_progress.id = "b1"
    in_progress.request_counts = None
    in_progress.created_at = None
    in_progress.ended_at = None
    in_progress.expires_at = None
    fake_sdk.messages.batches.retrieve.return_value = in_progress
    c = AnthropicClient()
    c._client = fake_sdk
    out, err = batch_wait(c, "b1", poll_interval_s=10.0, timeout_s=20.0,
                            sleep_fn=lambda s: None)
    assert out is None
    assert "did not end" in err
