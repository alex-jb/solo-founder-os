"""pytest helpers — every agent's test suite needs these.

Re-exports common patterns from one place so a future agent's first test
file is 5 lines of imports + actual test logic.

Usage:
    from solo_founder_os.testing import (
        fake_urlopen, fake_anthropic, tmp_baseline_path,
    )
"""
from __future__ import annotations
import json
import pathlib
from datetime import datetime, timezone
from unittest.mock import MagicMock

from .source import MetricSample, SourceReport


# ─── HTTP mocking ────────────────────────────────────────────

def fake_urlopen_ok(payload: dict | str | bytes = b"", *, status: int = 200):
    """Build a context-manager-compatible mock for urllib.request.urlopen
    that returns the given payload."""
    if isinstance(payload, dict):
        body = json.dumps(payload).encode()
    elif isinstance(payload, str):
        body = payload.encode()
    else:
        body = payload
    fake = MagicMock()
    fake.status = status
    fake.read.return_value = body
    fake.__enter__ = lambda s: s
    fake.__exit__ = lambda *a: None
    return fake


def fake_urlopen_http_error(code: int = 500, msg: str = "server error"):
    """Build a side_effect that raises urllib.error.HTTPError with the
    given code. Use as `patch("urllib.request.urlopen", new=...)`."""
    import urllib.error
    return MagicMock(side_effect=urllib.error.HTTPError(
        url="x", code=code, msg=msg, hdrs=None, fp=None))


# ─── Anthropic mocking ───────────────────────────────────────

def fake_anthropic(text: str, *, in_tokens: int = 100, out_tokens: int = 20):
    """Build a MagicMock that mimics anthropic.Anthropic with a fixed text
    response. Use as `patch("anthropic.Anthropic", return_value=...)` OR
    pass to AnthropicClient by setting `client._client = fake_anthropic(...)`.

    The returned object has .messages.create() returning a Message-shaped
    MagicMock with .content[0].text == text and .usage.{input,output}_tokens.
    """
    block = MagicMock()
    block.text = text
    block.type = "text"
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = in_tokens
    resp.usage.output_tokens = out_tokens
    client = MagicMock()
    client.messages.create.return_value = resp
    return client


def fake_anthropic_raises(exc: Exception):
    """Like fake_anthropic but messages.create() raises the given exception."""
    client = MagicMock()
    client.messages.create.side_effect = exc
    return client


# ─── Source / report helpers ─────────────────────────────────

def make_metric(name: str = "x", value: float = 1.0, *,
                 severity: str = "info", note: str = "",
                 baseline: float | None = None,
                 delta_pct: float | None = None) -> MetricSample:
    """Build a MetricSample without all the keyword arg ceremony."""
    return MetricSample(
        name=name, value=value, severity=severity, note=note,
        baseline=baseline, delta_pct=delta_pct,
    )


def make_report(source: str = "test",
                metrics: list[MetricSample] | None = None,
                error: str | None = None) -> SourceReport:
    """Build a SourceReport for tests."""
    return SourceReport(
        source=source,
        fetched_at=datetime.now(timezone.utc),
        metrics=metrics or [],
        error=error,
    )


# ─── Path helpers (use as fixtures) ──────────────────────────

def tmp_baseline_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return tmp_path / 'baseline.jsonl'. Idiomatic via pytest.fixture."""
    return tmp_path / "baseline.jsonl"


def tmp_usage_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return tmp_path / 'usage.jsonl'."""
    return tmp_path / "usage.jsonl"
