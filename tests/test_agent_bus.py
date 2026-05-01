"""Tests for solo_founder_os.agent_bus — cross-terminal coordination."""
from __future__ import annotations
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.agent_bus import (
    _parse_since,
    _slug,
    _terminal_id,
    main,
    post,
    read,
)


# ── Terminal id ──────────────────────────────────────────


def test_terminal_id_override(monkeypatch):
    monkeypatch.setenv("SFOS_BUS_ID", "my-laptop-main")
    assert _terminal_id() == "my-laptop-main"


def test_terminal_id_strips_special_chars(monkeypatch):
    monkeypatch.setenv("SFOS_BUS_ID", "Main Terminal!")
    assert "Main-Terminal" in _terminal_id()


def test_terminal_id_default_returns_string(monkeypatch):
    monkeypatch.delenv("SFOS_BUS_ID", raising=False)
    out = _terminal_id()
    assert isinstance(out, str)
    assert len(out) > 0


# ── Slug ─────────────────────────────────────────────────


def test_slug():
    assert _slug("hello world") == "hello-world"
    assert _slug("hello/world!") == "hello-world"
    assert _slug("") == ""


# ── post ─────────────────────────────────────────────────


def test_post_creates_markdown_file(tmp_path):
    path = post("working on customer-support", channel="general",
                 terminal="t1", base=tmp_path)
    assert path.exists()
    md = path.read_text()
    assert "channel: general" in md
    assert "terminal: t1" in md
    assert "working on customer-support" in md


def test_post_with_tags(tmp_path):
    path = post("something", channel="coord", terminal="t1",
                 tags=["claim", "brain"], base=tmp_path)
    md = path.read_text()
    assert "tags: [claim, brain]" in md


def test_post_truncates_long_body(tmp_path):
    long = "x" * 10_000
    path = post(long, channel="g", terminal="t1", base=tmp_path)
    md = path.read_text()
    # Body capped at 4000 chars
    body_section = md.split("---\n", 2)[2]
    assert len(body_section.strip()) <= 4001  # +newline


def test_post_collision_uses_microseconds(tmp_path, monkeypatch):
    """Two posts in the same second from same terminal → second one gets
    a microseconds suffix, not overwriting."""
    # Force same ts.strftime via monkey-patched datetime
    fake_now = datetime(2026, 5, 2, 12, 0, 0, 0, tzinfo=timezone.utc)
    fake_calls = {"i": 0}

    class FakeDT:
        @staticmethod
        def now(tz=None):
            fake_calls["i"] += 1
            return fake_now.replace(microsecond=fake_calls["i"] * 100)

        @staticmethod
        def fromisoformat(s):
            return datetime.fromisoformat(s)

    monkeypatch.setattr("solo_founder_os.agent_bus.datetime", FakeDT)
    p1 = post("a", channel="g", terminal="t1", base=tmp_path)
    p2 = post("b", channel="g", terminal="t1", base=tmp_path)
    assert p1 != p2
    assert p1.exists() and p2.exists()


def test_post_filesystem_error_doesnt_raise(tmp_path):
    """If base is a file, mkdir fails — should return placeholder, not raise."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    # Should not raise
    path = post("body", channel="x", base=blocker)
    assert path is not None


# ── read ─────────────────────────────────────────────────


def test_read_empty_returns_empty(tmp_path):
    assert read(base=tmp_path) == []


def test_read_filters_by_channel(tmp_path):
    post("a", channel="general", terminal="t1", base=tmp_path)
    post("b", channel="coord", terminal="t1", base=tmp_path)
    msgs = read(channel="general", base=tmp_path)
    assert len(msgs) == 1
    assert msgs[0].body == "a"


def test_read_filters_by_terminal(tmp_path):
    post("from t1", channel="g", terminal="t1", base=tmp_path)
    post("from t2", channel="g", terminal="t2", base=tmp_path)
    msgs = read(terminal="t1", base=tmp_path)
    assert len(msgs) == 1
    assert msgs[0].terminal == "t1"


def test_read_filters_by_since(tmp_path):
    """Messages older than `since` should be excluded."""
    post("old", channel="g", terminal="t1", base=tmp_path)
    # Synthetically write a future message via timestamps in the file —
    # easier: set since to the future and verify zero results
    msgs = read(since=datetime.now(timezone.utc) + timedelta(hours=1),
                 base=tmp_path)
    assert msgs == []


def test_read_returns_messages_in_time_order(tmp_path):
    """Newest last."""
    post("first", channel="g", terminal="t1", base=tmp_path)
    post("second", channel="g", terminal="t1", base=tmp_path)
    post("third", channel="g", terminal="t1", base=tmp_path)
    msgs = read(base=tmp_path)
    assert len(msgs) == 3
    assert msgs[0].body == "first"
    assert msgs[2].body == "third"


def test_read_caps_at_n(tmp_path):
    for i in range(20):
        post(f"msg-{i}", channel="g", terminal="t1", base=tmp_path)
    msgs = read(n=5, base=tmp_path)
    assert len(msgs) == 5
    assert msgs[0].body == "msg-15"
    assert msgs[-1].body == "msg-19"


def test_read_handles_corrupt_files(tmp_path):
    """Files that don't parse as valid bus messages get silently skipped."""
    chan = tmp_path / "general"
    chan.mkdir()
    (chan / "garbage.md").write_text("not a valid frontmatter")
    post("real msg", channel="general", terminal="t1", base=tmp_path)
    msgs = read(base=tmp_path)
    assert len(msgs) == 1
    assert msgs[0].body == "real msg"


def test_read_parses_tags(tmp_path):
    post("tagged", channel="g", terminal="t1",
          tags=["claim", "brain"], base=tmp_path)
    msgs = read(base=tmp_path)
    assert msgs[0].tags == ["claim", "brain"]


# ── _parse_since ─────────────────────────────────────────


def test_parse_since_minutes():
    out = _parse_since("30m")
    assert out is not None
    delta = datetime.now(timezone.utc) - out
    assert 29 * 60 <= delta.total_seconds() <= 31 * 60


def test_parse_since_hours():
    out = _parse_since("2h")
    delta = datetime.now(timezone.utc) - out
    assert 1.9 * 3600 <= delta.total_seconds() <= 2.1 * 3600


def test_parse_since_days():
    out = _parse_since("1d")
    delta = datetime.now(timezone.utc) - out
    assert 0.95 * 86400 <= delta.total_seconds() <= 1.05 * 86400


def test_parse_since_iso():
    out = _parse_since("2026-05-02T12:00:00+00:00")
    assert out is not None
    assert out.year == 2026


def test_parse_since_garbage_returns_none():
    assert _parse_since("not a duration") is None


# ── CLI ─────────────────────────────────────────────────


def test_main_skip_env(monkeypatch):
    monkeypatch.setenv("SFOS_BUS_SKIP", "1")
    rc = main(["post", "test"])
    assert rc == 0


def test_main_post_creates_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("SFOS_BUS_SKIP", raising=False)
    monkeypatch.setenv("SFOS_BUS_ID", "test-terminal")
    rc = main(["post", "hello world", "--channel", "general"])
    assert rc == 0
    files = list((tmp_path / ".solo-founder-os" / "bus" / "general")
                  .glob("*.md"))
    assert len(files) == 1
    assert "hello world" in files[0].read_text()


def test_main_tail_shows_messages(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("SFOS_BUS_SKIP", raising=False)
    monkeypatch.setenv("SFOS_BUS_ID", "test")
    main(["post", "msg from terminal 1"])
    main(["post", "msg from terminal 2"])
    capsys.readouterr()  # discard stderr from posts
    rc = main(["tail"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "msg from terminal 1" in out
    assert "msg from terminal 2" in out


def test_main_tail_empty_no_messages(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    rc = main(["tail"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "no messages" in err
