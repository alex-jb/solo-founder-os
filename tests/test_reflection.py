"""Tests for solo_founder_os.reflection — Layer 1 of the auto-evolving agent plan.

The tricky bit: log_outcome() optionally fires a Haiku call to generate the
reflection text. Tests inject a fake AnthropicClient via the `client=` kwarg
so no real API is hit.
"""
from __future__ import annotations
import json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.reflection import (
    log_outcome,
    recent_reflections,
    reflections_preamble,
)


def _patch_home(monkeypatch, tmp_path):
    """Redirect Path.home() so the reflection log lands in tmp_path."""
    import pathlib
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)


def _fake_client(*, configured: bool = True,
                  reflection: str | None = "use a softer subject line"):
    """Build a fake AnthropicClient. messages_create_json returns the given
    reflection (or simulates an error if reflection=None)."""
    c = MagicMock()
    c.configured = configured
    if reflection is None:
        c.messages_create_json.return_value = (None, "fake error")
    else:
        c.messages_create_json.return_value = ({"reflection": reflection}, None)
    return c


def test_log_ok_outcome_writes_no_reflection(monkeypatch, tmp_path):
    """OK outcomes record the row but don't burn a Haiku call."""
    _patch_home(monkeypatch, tmp_path)
    fake = _fake_client()
    entry = log_outcome(".test-agent", task="t1", outcome="OK",
                         signal="all good", client=fake)
    assert entry["reflection"] == ""
    assert fake.messages_create_json.call_count == 0
    # Row is on disk
    log = tmp_path / ".test-agent" / "reflections.jsonl"
    assert log.exists()
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[0]["outcome"] == "OK"


def test_log_failed_outcome_generates_reflection(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    fake = _fake_client(reflection="lead with the verb, not the company")
    entry = log_outcome(".test-agent", task="draft_email", outcome="FAILED",
                         signal="user moved to rejected/", client=fake)
    assert entry["reflection"] == "lead with the verb, not the company"
    assert fake.messages_create_json.call_count == 1


def test_log_partial_also_generates_reflection(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    fake = _fake_client(reflection="check rate limits before retry")
    entry = log_outcome(".test-agent", task="forge", outcome="PARTIAL",
                         signal="429 on third call", client=fake)
    assert entry["reflection"] == "check rate limits before retry"


def test_log_outcome_skip_env_var_suppresses_write(monkeypatch, tmp_path):
    """SFOS_LOG_OUTCOME_SKIP=1 → no file write, no Haiku call.

    Test-pollution guard: agent test suites that don't isolate
    pathlib.Path.home() were silently writing to the real
    ~/.<agent>/reflections.jsonl. This env var lets a suite opt out
    in conftest without per-test monkeypatching.
    """
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("SFOS_LOG_OUTCOME_SKIP", "1")
    fake = _fake_client()
    entry = log_outcome(".test-agent", task="t1", outcome="FAILED",
                         signal="x", client=fake)
    # Returned entry shape is preserved (callers may inspect it)
    assert entry["task"] == "t1"
    assert entry["outcome"] == "FAILED"
    # But: no Haiku call, no file written
    assert fake.messages_create_json.call_count == 0
    rfile = tmp_path / ".test-agent" / "reflections.jsonl"
    assert not rfile.exists()


def test_log_outcome_skip_env_var_off_still_writes(monkeypatch, tmp_path):
    """When SFOS_LOG_OUTCOME_SKIP is anything other than '1', behavior
    is unchanged."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("SFOS_LOG_OUTCOME_SKIP", "0")
    fake = _fake_client()
    log_outcome(".test-agent", task="t1", outcome="OK",
                 signal="x", client=fake)
    rfile = tmp_path / ".test-agent" / "reflections.jsonl"
    assert rfile.exists()


def test_log_skip_reflection_flag(monkeypatch, tmp_path):
    """Even on FAILED, the skip_reflection flag suppresses the Haiku call."""
    _patch_home(monkeypatch, tmp_path)
    fake = _fake_client()
    entry = log_outcome(".test-agent", task="t1", outcome="FAILED",
                         signal="x", client=fake, skip_reflection=True)
    assert entry["reflection"] == ""
    assert fake.messages_create_json.call_count == 0


def test_log_unconfigured_client_is_silent(monkeypatch, tmp_path):
    """If Anthropic isn't configured, the row is logged with empty reflection;
    the agent's main loop continues unaffected."""
    _patch_home(monkeypatch, tmp_path)
    fake = _fake_client(configured=False)
    entry = log_outcome(".test-agent", task="t1", outcome="FAILED",
                         signal="x", client=fake)
    assert entry["reflection"] == ""
    assert fake.messages_create_json.call_count == 0


def test_log_anthropic_error_is_silent(monkeypatch, tmp_path):
    """Haiku call errors out; we still write the row but with empty reflection.
    Best-effort, never raises."""
    _patch_home(monkeypatch, tmp_path)
    fake = _fake_client(reflection=None)  # → returns (None, "fake error")
    entry = log_outcome(".test-agent", task="t1", outcome="FAILED",
                         signal="x", client=fake)
    assert entry["reflection"] == ""


def test_log_reflection_clamped_to_280(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    long = "x" * 500
    fake = _fake_client(reflection=long)
    entry = log_outcome(".test-agent", task="t1", outcome="FAILED",
                         signal="too long", client=fake)
    assert len(entry["reflection"]) == 280


def test_log_swallows_filesystem_errors(monkeypatch, tmp_path):
    """Permission denied or read-only filesystem: agent main loop must not
    raise. The function returns the entry even if writing fails."""
    _patch_home(monkeypatch, tmp_path)
    # Make the parent dir a file so mkdir + open both fail
    blocker = tmp_path / ".blocker-agent"
    blocker.write_text("not a dir")
    # Use the blocker as a fake agent dir
    fake = _fake_client(reflection="")
    # Should not raise
    entry = log_outcome(".blocker-agent", task="t1", outcome="OK",
                         signal="ok", client=fake)
    assert entry["task"] == "t1"


def test_recent_reflections_filters_by_task(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    log = tmp_path / ".test-agent" / "reflections.jsonl"
    log.parent.mkdir()
    log.write_text("\n".join([
        json.dumps({"ts": "t", "task": "draft_email",
                     "outcome": "FAILED", "verbatim_signal": "x",
                     "reflection": "soften tone"}),
        json.dumps({"ts": "t", "task": "scrape",
                     "outcome": "PARTIAL", "verbatim_signal": "x",
                     "reflection": "skip subreddit X"}),
        json.dumps({"ts": "t", "task": "draft_email",
                     "outcome": "FAILED", "verbatim_signal": "x",
                     "reflection": "shorten subject"}),
    ]))
    refs = recent_reflections(".test-agent", "draft_email")
    assert refs == ["soften tone", "shorten subject"]


def test_recent_reflections_skips_empty(monkeypatch, tmp_path):
    """Rows with empty reflection (i.e. OK outcomes) shouldn't pollute the
    preamble."""
    _patch_home(monkeypatch, tmp_path)
    log = tmp_path / ".test-agent" / "reflections.jsonl"
    log.parent.mkdir()
    log.write_text("\n".join([
        json.dumps({"task": "t1", "outcome": "OK", "reflection": ""}),
        json.dumps({"task": "t1", "outcome": "FAILED", "reflection": "real lesson"}),
    ]))
    assert recent_reflections(".test-agent", "t1") == ["real lesson"]


def test_recent_reflections_no_file_returns_empty(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    assert recent_reflections(".never-existed", "t1") == []


def test_recent_reflections_corrupt_lines_skipped(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    log = tmp_path / ".test-agent" / "reflections.jsonl"
    log.parent.mkdir()
    log.write_text("\n".join([
        "garbage{not json",
        json.dumps({"task": "t1", "outcome": "FAILED", "reflection": "ok"}),
        "more garbage",
    ]))
    assert recent_reflections(".test-agent", "t1") == ["ok"]


def test_recent_reflections_caps_at_n(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    log = tmp_path / ".test-agent" / "reflections.jsonl"
    log.parent.mkdir()
    log.write_text("\n".join(
        json.dumps({"task": "t", "outcome": "FAILED",
                     "reflection": f"r{i}"})
        for i in range(20)
    ))
    refs = recent_reflections(".test-agent", "t", n=5)
    # Last 5 of the 20 written
    assert refs == [f"r{i}" for i in range(15, 20)]


def test_preamble_empty_when_no_reflections(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    assert reflections_preamble(".never-existed", "t1") == ""


def test_preamble_renders_bullets(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    log = tmp_path / ".test-agent" / "reflections.jsonl"
    log.parent.mkdir()
    log.write_text("\n".join([
        json.dumps({"task": "t1", "outcome": "FAILED",
                     "reflection": "first lesson"}),
        json.dumps({"task": "t1", "outcome": "FAILED",
                     "reflection": "second lesson"}),
    ]))
    out = reflections_preamble(".test-agent", "t1")
    assert "first lesson" in out
    assert "second lesson" in out
    assert out.startswith("Past reflections on this task type")
    assert out.endswith("\n\n")


def test_full_loop_log_then_retrieve(monkeypatch, tmp_path):
    """End-to-end: log a failure, then retrieve via recent_reflections."""
    _patch_home(monkeypatch, tmp_path)
    fake = _fake_client(reflection="cap subject at 6 words")
    log_outcome(".test-agent", task="draft_email", outcome="FAILED",
                signal="bounced", client=fake)
    refs = recent_reflections(".test-agent", "draft_email")
    assert refs == ["cap subject at 6 words"]
