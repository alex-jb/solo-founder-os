"""Tests for usage_log + anthropic_client."""
from __future__ import annotations
import json
import os
import pathlib
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.usage_log import log_usage, usage_report, PRICES
from solo_founder_os.anthropic_client import AnthropicClient


# ─── usage_log ───────────────────────────────────────────

def test_log_usage_appends(tmp_path):
    log = tmp_path / "usage.jsonl"
    log_usage(log_path=log, model="claude-haiku-4-5",
              input_tokens=100, output_tokens=20)
    log_usage(log_path=log, model="claude-sonnet-4-6",
              input_tokens=200, output_tokens=40)
    rows = log.read_text().strip().splitlines()
    assert len(rows) == 2
    assert json.loads(rows[0])["model"] == "claude-haiku-4-5"
    assert json.loads(rows[1])["model"] == "claude-sonnet-4-6"


def test_log_usage_extra_fields(tmp_path):
    log = tmp_path / "usage.jsonl"
    log_usage(log_path=log, model="claude-haiku-4-5",
              input_tokens=10, output_tokens=5,
              extra={"verdict": "PASS", "bytes": 500})
    row = json.loads(log.read_text().strip())
    assert row["verdict"] == "PASS"
    assert row["bytes"] == 500


def test_log_usage_swallows_io_errors(tmp_path):
    """Best-effort logging — failure does not raise."""
    bad_path = tmp_path / "nonexistent" / "deep" / "dir" / "u.jsonl"
    # mkdir parents=True creates intermediate dirs, so this works. But we
    # want to test the swallow path: pass a path whose parent IS a file.
    block = tmp_path / "blocker"
    block.write_text("i am a file not a dir")
    nested = block / "u.jsonl"
    log_usage(log_path=nested, model="x", input_tokens=1, output_tokens=1)
    # No exception — that's the whole point


def test_usage_report_empty(tmp_path):
    log = tmp_path / "absent.jsonl"
    assert "No usage logged" in usage_report(log)


def test_usage_report_aggregates(tmp_path):
    log = tmp_path / "u.jsonl"
    log_usage(log_path=log, model="claude-haiku-4-5",
              input_tokens=1000, output_tokens=200)
    log_usage(log_path=log, model="claude-haiku-4-5",
              input_tokens=2000, output_tokens=400)
    out = usage_report(log)
    assert "2 runs" in out
    assert "claude-haiku-4-5" in out
    assert "3,000 in" in out  # 1000 + 2000


def test_prices_known_models():
    """Lock the known prices — if Anthropic changes them we want a test failure
    so we update the constants."""
    assert PRICES["claude-haiku-4-5"] == (1.0, 5.0)
    assert PRICES["claude-sonnet-4-6"] == (3.0, 15.0)
    assert PRICES["claude-opus-4-7"] == (15.0, 75.0)


# ─── AnthropicClient ─────────────────────────────────────

def _fake_anthropic(text: str, in_tok: int = 100, out_tok: int = 20):
    block = MagicMock(); block.text = text; block.type = "text"
    resp = MagicMock(); resp.content = [block]
    resp.usage.input_tokens = in_tok
    resp.usage.output_tokens = out_tok
    client = MagicMock()
    client.messages.create.return_value = resp
    return client


def test_client_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = AnthropicClient()
    assert c.configured is False
    resp, err = c.messages_create(model="x", max_tokens=1, messages=[])
    assert resp is None
    assert "missing env var" in err


def test_client_custom_env_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MY_AGENT_KEY", "sk-test")
    c = AnthropicClient(env_key="MY_AGENT_KEY")
    assert c.configured is True


def test_client_success_path_returns_response(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    log = tmp_path / "u.jsonl"
    fake = _fake_anthropic("hello", in_tok=100, out_tok=20)
    with patch("anthropic.Anthropic", return_value=fake):
        c = AnthropicClient(usage_log_path=log)
        resp, err = c.messages_create(model="claude-haiku-4-5",
                                       max_tokens=10, messages=[])
    assert err is None
    assert resp is not None
    # usage was logged
    assert log.exists()
    row = json.loads(log.read_text().strip())
    assert row["model"] == "claude-haiku-4-5"
    assert row["input_tokens"] == 100


def test_client_exception_returns_error_string(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = MagicMock()
    fake.messages.create.side_effect = Exception("rate limit")
    with patch("anthropic.Anthropic", return_value=fake):
        c = AnthropicClient()
        resp, err = c.messages_create(model="x", max_tokens=1, messages=[])
    assert resp is None
    assert "rate limit" in err


def test_client_caches_underlying_client(monkeypatch):
    """Constructing anthropic.Anthropic() is non-trivial; cache after first call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = _fake_anthropic("ok")
    construct_count = [0]
    def factory(*a, **kw):
        construct_count[0] += 1
        return fake
    with patch("anthropic.Anthropic", side_effect=factory):
        c = AnthropicClient()
        c.messages_create(model="x", max_tokens=1, messages=[])
        c.messages_create(model="x", max_tokens=1, messages=[])
    assert construct_count[0] == 1


def test_extract_text_handles_none():
    assert AnthropicClient.extract_text(None) == ""


def test_extract_text_concats_blocks():
    a = MagicMock(); a.text = "hello "; a.type = "text"
    b = MagicMock(); b.text = "world"; b.type = "text"
    skip = MagicMock(); skip.text = "img"; skip.type = "image"  # filtered
    resp = MagicMock(); resp.content = [a, b, skip]
    assert AnthropicClient.extract_text(resp) == "hello world"


# ─── prompt caching (v0.4) ────────────────────────────

from solo_founder_os.anthropic_client import _wrap_system_with_cache


def test_wrap_system_string_to_cached_blocks():
    out = _wrap_system_with_cache("Hello world prompt")
    assert isinstance(out, list)
    assert out == [{
        "type": "text",
        "text": "Hello world prompt",
        "cache_control": {"type": "ephemeral"},
    }]


def test_wrap_system_string_with_1h_ttl():
    out = _wrap_system_with_cache("system prompt", ttl="1h")
    assert out[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_wrap_system_existing_list_gets_cache_on_last_block():
    inp = [
        {"type": "text", "text": "stable instructions"},
        {"type": "text", "text": "more stable content"},
    ]
    out = _wrap_system_with_cache(inp)
    assert "cache_control" not in out[0]  # first block clean
    assert out[1]["cache_control"] == {"type": "ephemeral"}


def test_wrap_system_does_not_mutate_input():
    inp = [{"type": "text", "text": "prompt"}]
    out = _wrap_system_with_cache(inp)
    assert "cache_control" not in inp[0]
    assert "cache_control" in out[0]


def test_wrap_system_empty_passthrough():
    assert _wrap_system_with_cache("") == ""
    assert _wrap_system_with_cache(None) is None


def test_wrap_system_unknown_shape_passthrough():
    assert _wrap_system_with_cache(42) == 42


def test_client_caches_system_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured: dict = {}

    def capture(**kwargs):
        captured.update(kwargs)
        block = MagicMock(); block.text = "ok"; block.type = "text"
        resp = MagicMock(); resp.content = [block]
        resp.usage.input_tokens = 100
        resp.usage.output_tokens = 20
        resp.usage.cache_read_input_tokens = 0
        resp.usage.cache_creation_input_tokens = 100
        return resp

    fake = MagicMock()
    fake.messages.create.side_effect = capture
    with patch("anthropic.Anthropic", return_value=fake):
        c = AnthropicClient()
        c.messages_create(model="x", max_tokens=1, system="my prompt", messages=[])
    assert isinstance(captured["system"], list)
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_client_skips_caching_when_disabled(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured: dict = {}

    def capture(**kwargs):
        captured.update(kwargs)
        block = MagicMock(); block.text = "ok"; block.type = "text"
        resp = MagicMock(); resp.content = [block]
        return resp

    fake = MagicMock()
    fake.messages.create.side_effect = capture
    with patch("anthropic.Anthropic", return_value=fake):
        c = AnthropicClient(cache_system=False)
        c.messages_create(model="x", max_tokens=1, system="my prompt", messages=[])
    assert captured["system"] == "my prompt"


def test_client_per_call_cache_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured: dict = {}

    def capture(**kwargs):
        captured.update(kwargs)
        block = MagicMock(); block.text = "ok"; block.type = "text"
        resp = MagicMock(); resp.content = [block]
        return resp

    fake = MagicMock()
    fake.messages.create.side_effect = capture
    with patch("anthropic.Anthropic", return_value=fake):
        c = AnthropicClient(cache_system=True)
        c.messages_create(model="x", max_tokens=1, system="my prompt", messages=[],
                           cache_system=False)
    assert captured["system"] == "my prompt"
    assert "cache_system" not in captured  # not forwarded to API


def test_client_logs_cache_token_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    log = tmp_path / "u.jsonl"
    fake = MagicMock()
    block = MagicMock(); block.text = "ok"; block.type = "text"
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = 50
    resp.usage.output_tokens = 20
    resp.usage.cache_read_input_tokens = 800
    resp.usage.cache_creation_input_tokens = 0
    fake.messages.create.return_value = resp
    with patch("anthropic.Anthropic", return_value=fake):
        c = AnthropicClient(usage_log_path=log)
        c.messages_create(model="claude-haiku-4-5", max_tokens=1,
                          system="cached system prompt", messages=[])
    import json as _json
    row = _json.loads(log.read_text().strip())
    assert row["cache_read_input_tokens"] == 800
    assert row["cache_creation_input_tokens"] == 0
