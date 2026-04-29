"""Tests for hitl_queue + cli skeleton."""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.hitl_queue import (
    HitlQueue,
    sanitize_filename_part,
    make_basename,
    parse_frontmatter,
    render_frontmatter,
    PENDING, APPROVED, REJECTED, SENT,
)
from solo_founder_os.cli import (
    add_common_args, check_skip, resolve_notify_targets,
)


# ─── sanitize / make_basename ────────────────────────────────

def test_sanitize_strips_unsafe_chars():
    assert sanitize_filename_part("Hello, World!") == "hello-world"
    assert sanitize_filename_part("a/b\\c:d") == "a-b-c-d"
    assert sanitize_filename_part("---weird---") == "weird"


def test_sanitize_returns_x_for_empty():
    assert sanitize_filename_part("") == "x"
    assert sanitize_filename_part("///") == "x"


def test_make_basename_format():
    ts = datetime(2026, 4, 30, 12, 34, 56, tzinfo=timezone.utc)
    name = make_basename(["orallexa", "garry-tan"], ts=ts)
    assert name == "20260430T123456-orallexa-garry-tan.md"


def test_make_basename_skips_empty_parts():
    ts = datetime(2026, 4, 30, 0, 0, 0, tzinfo=timezone.utc)
    name = make_basename(["a", "", "b"], ts=ts)
    assert name == "20260430T000000-a-b.md"


def test_make_basename_sanitizes():
    ts = datetime(2026, 4, 30, 0, 0, 0, tzinfo=timezone.utc)
    name = make_basename(["First/Name", "weird::email@x.com"], ts=ts)
    assert "/" not in name
    assert ":" not in name
    assert "@" not in name


# ─── frontmatter ─────────────────────────────────────────────

def test_parse_frontmatter_returns_dict():
    md = "---\nfoo: bar\nbaz: qux\n---\nbody"
    assert parse_frontmatter(md) == {"foo": "bar", "baz": "qux"}


def test_parse_frontmatter_missing_returns_empty():
    assert parse_frontmatter("just body") == {}


def test_render_frontmatter_round_trip():
    text = render_frontmatter({"a": "x", "b": "y"}) + "body\n"
    assert parse_frontmatter(text) == {"a": "x", "b": "y"}


def test_render_frontmatter_format():
    rendered = render_frontmatter({"k1": "v1", "k2": "v2"})
    assert rendered == "---\nk1: v1\nk2: v2\n---\n"


# ─── HitlQueue ───────────────────────────────────────────────

def test_status_constants():
    assert HitlQueue.PENDING == "pending"
    assert HitlQueue.APPROVED == "approved"
    assert HitlQueue.REJECTED == "rejected"
    assert HitlQueue.SENT == "sent"


def test_write_creates_dir_and_file(tmp_path):
    q = HitlQueue(tmp_path / "queue")
    path = q.write("test.md", "hello world")
    assert path.exists()
    assert path.read_text() == "hello world"
    assert "pending" in str(path)


def test_write_to_specific_status(tmp_path):
    q = HitlQueue(tmp_path / "queue")
    path = q.write("test.md", "x", status=APPROVED)
    assert "approved" in str(path)


def test_write_invalid_status_raises(tmp_path):
    q = HitlQueue(tmp_path / "queue")
    with pytest.raises(ValueError, match="unknown status"):
        q.write("x.md", "y", status="garbage")


def test_list_returns_sorted(tmp_path):
    q = HitlQueue(tmp_path / "queue")
    q.write("z-second.md", "x")
    q.write("a-first.md", "y")
    paths = q.list()
    # Sorted by name → a-first.md before z-second.md
    assert [p.name for p in paths] == ["a-first.md", "z-second.md"]


def test_list_empty_returns_empty(tmp_path):
    q = HitlQueue(tmp_path / "queue")
    assert q.list() == []


def test_list_per_status(tmp_path):
    q = HitlQueue(tmp_path / "queue")
    q.write("a.md", "x", status=PENDING)
    q.write("b.md", "y", status=APPROVED)
    assert len(q.list(status=PENDING)) == 1
    assert len(q.list(status=APPROVED)) == 1
    assert len(q.list(status=REJECTED)) == 0


def test_move_with_timestamp_prefix(tmp_path):
    q = HitlQueue(tmp_path / "queue")
    p = q.write("orig.md", "x")
    new_path = q.move(p, to=APPROVED)
    assert "approved" in str(new_path)
    assert "orig.md" in new_path.name
    # Timestamp prefix added
    assert len(new_path.name) > len("orig.md")
    # Original gone
    assert not p.exists()


def test_move_without_timestamp_prefix(tmp_path):
    q = HitlQueue(tmp_path / "queue")
    p = q.write("orig.md", "x")
    new_path = q.move(p, to=SENT, prefix_ts=False)
    assert new_path.name == "orig.md"


def test_from_env_uses_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_AGENT_QUEUE", str(tmp_path / "custom"))
    q = HitlQueue.from_env("MY_AGENT_QUEUE", default=tmp_path / "default")
    q.write("x.md", "y")
    assert (tmp_path / "custom" / "pending" / "x.md").exists()


def test_from_env_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_AGENT_QUEUE", raising=False)
    q = HitlQueue.from_env("MY_AGENT_QUEUE", default=tmp_path / "default")
    q.write("x.md", "y")
    assert (tmp_path / "default" / "pending" / "x.md").exists()


# ─── cli skeleton ────────────────────────────────────────────

def test_add_common_args_adds_all_flags():
    p = argparse.ArgumentParser()
    add_common_args(p)
    args = p.parse_args(["--quiet", "--dry-run", "--no-baseline",
                         "--notify", "ntfy,slack"])
    assert args.quiet is True
    assert args.dry_run is True
    assert args.no_baseline is True
    assert args.notify == "ntfy,slack"


def test_add_common_args_omit_some():
    p = argparse.ArgumentParser()
    add_common_args(p, omit=("--no-baseline", "--notify"))
    args = p.parse_args(["--quiet"])
    assert args.quiet is True
    # Omitted flags raise on unknown arg
    with pytest.raises(SystemExit):
        p.parse_args(["--no-baseline"])


def test_check_skip(monkeypatch):
    monkeypatch.delenv("FOO_SKIP", raising=False)
    assert check_skip("FOO_SKIP") is False
    monkeypatch.setenv("FOO_SKIP", "1")
    assert check_skip("FOO_SKIP") is True
    monkeypatch.setenv("FOO_SKIP", "true")  # only "1" counts
    assert check_skip("FOO_SKIP") is False


def test_resolve_notify_targets_from_arg():
    assert resolve_notify_targets("ntfy,slack") == ["ntfy", "slack"]


def test_resolve_notify_targets_strips_whitespace():
    assert resolve_notify_targets(" ntfy , slack ") == ["ntfy", "slack"]


def test_resolve_notify_targets_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("NOTIFIER_DEFAULT", "ntfy")
    assert resolve_notify_targets(None) == ["ntfy"]


def test_resolve_notify_targets_empty(monkeypatch):
    monkeypatch.delenv("NOTIFIER_DEFAULT", raising=False)
    assert resolve_notify_targets(None) == []
    assert resolve_notify_targets("") == []


def test_resolve_notify_targets_custom_env(monkeypatch):
    monkeypatch.setenv("MY_DEFAULT", "telegram")
    assert resolve_notify_targets(None, "MY_DEFAULT") == ["telegram"]
