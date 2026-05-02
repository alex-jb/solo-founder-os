"""Tests for solo_founder_os.preference — ICPL helper."""
from __future__ import annotations
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.preference import (
    log_edit,
    preference_preamble,
    recent_edits,
)


def _patch_home(monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)


def test_log_edit_writes_jsonl(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    entry = log_edit(".test-agent", "draft_email",
                      "Hi Alice, want to chat?",
                      "Hi Alice — saw your post on agent infra...",
                      context={"investor": "Alice"})
    assert entry["task"] == "draft_email"
    log = tmp_path / ".test-agent" / "preference-pairs.jsonl"
    assert log.exists()
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[0]["original"] == "Hi Alice, want to chat?"
    assert "agent infra" in rows[0]["edited"]
    assert rows[0]["context"]["investor"] == "Alice"


def test_log_edit_truncates_long_strings(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    long = "x" * 10_000
    entry = log_edit(".test-agent", "t", long, long)
    assert len(entry["original"]) == 5000
    assert len(entry["edited"]) == 5000


def test_log_edit_test_mode_skips_write(monkeypatch, tmp_path):
    """SFOS_TEST_MODE=1 → log_edit must NOT write to disk."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("SFOS_TEST_MODE", "1")
    entry = log_edit(".test-agent", "draft_email", "a", "b")
    assert entry["task"] == "draft_email"
    log = tmp_path / ".test-agent" / "preference-pairs.jsonl"
    assert not log.exists()


def test_log_edit_swallows_filesystem_errors(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    # Make the agent dir a file so mkdir + open both fail
    blocker = tmp_path / ".blocker-agent"
    blocker.write_text("not a dir")
    # Should not raise
    log_edit(".blocker-agent", "t", "orig", "edit")


def test_recent_edits_filters_by_task(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    log_edit(".test-agent", "draft_email", "o1", "e1")
    log_edit(".test-agent", "scrape", "o2", "e2")
    log_edit(".test-agent", "draft_email", "o3", "e3")
    pairs = recent_edits(".test-agent", "draft_email")
    assert len(pairs) == 2
    assert pairs[0]["original"] == "o1"
    assert pairs[1]["original"] == "o3"


def test_recent_edits_skips_pairs_with_missing_field(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    log = tmp_path / ".test-agent" / "preference-pairs.jsonl"
    log.parent.mkdir()
    log.write_text("\n".join([
        json.dumps({"task": "t", "original": "", "edited": "e"}),
        json.dumps({"task": "t", "original": "o", "edited": ""}),
        json.dumps({"task": "t", "original": "o", "edited": "e"}),
    ]))
    pairs = recent_edits(".test-agent", "t")
    assert len(pairs) == 1
    assert pairs[0]["original"] == "o"


def test_recent_edits_no_file_returns_empty(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    assert recent_edits(".never-existed", "t") == []


def test_recent_edits_corrupt_lines_skipped(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    log = tmp_path / ".test-agent" / "preference-pairs.jsonl"
    log.parent.mkdir()
    log.write_text("garbage\n"
                    + json.dumps({"task": "t", "original": "o",
                                   "edited": "e"})
                    + "\n")
    pairs = recent_edits(".test-agent", "t")
    assert len(pairs) == 1


def test_recent_edits_caps_at_n(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    for i in range(20):
        log_edit(".test-agent", "t", f"o{i}", f"e{i}")
    pairs = recent_edits(".test-agent", "t", n=5)
    assert len(pairs) == 5
    # Most recent 5
    assert pairs[0]["original"] == "o15"
    assert pairs[-1]["original"] == "o19"


def test_preamble_empty_when_no_pairs(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    assert preference_preamble(".never-existed", "t") == ""


def test_preamble_renders_exemplars(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    log_edit(".test-agent", "draft_email",
              "raw draft 1", "polished version 1")
    log_edit(".test-agent", "draft_email",
              "raw draft 2", "polished version 2")
    out = preference_preamble(".test-agent", "draft_email")
    assert "ORIGINAL DRAFT" in out
    assert "HUMAN-EDITED FINAL" in out
    assert "raw draft 1" in out
    assert "polished version 1" in out
    assert "raw draft 2" in out


def test_preamble_truncates_each_exemplar(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    long_orig = "o" * 2000
    long_edit = "e" * 2000
    log_edit(".test-agent", "t", long_orig, long_edit)
    out = preference_preamble(".test-agent", "t", truncate_each=200)
    # Each exemplar capped at 200 chars
    assert out.count("o") <= 250  # plus surrounding text
    assert out.count("e") <= 250


def test_full_loop_log_then_render(monkeypatch, tmp_path):
    """End-to-end: log → preamble shows exemplars."""
    _patch_home(monkeypatch, tmp_path)
    log_edit(".vc-outreach", "draft_email",
              "Hi Alice, want to chat?",
              "Hi Alice — saw your thesis post...")
    out = preference_preamble(".vc-outreach", "draft_email")
    assert "Hi Alice, want to chat?" in out
    assert "thesis post" in out
    assert "preference signal" in out
