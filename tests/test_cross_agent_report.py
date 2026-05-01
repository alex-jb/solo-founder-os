"""Tests for solo_founder_os.cross_agent_report."""
from __future__ import annotations
import json
import pathlib
import sqlite3

import pytest

import solo_founder_os.cross_agent_report as m
from solo_founder_os.cross_agent_report import (
    _bucket_signal,
    _scan_bandit,
    _scan_preferences,
    _scan_reflections,
    _scan_skills,
    collect,
    render_markdown,
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Redirect HOME so scans operate against a clean tmp tree, never
    the developer's real ~/.solo-founder-os/ etc."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # The module captured pathlib.Path.home() at import time inside two
    # constants. Patch them to follow the new HOME for this test.
    monkeypatch.setattr(m, "SHARED_SKILLS_DIR",
                          tmp_path / ".solo-founder-os" / "skills")
    monkeypatch.setattr(m, "SHARED_BANDIT_DB",
                          tmp_path / ".solo-founder-os" / "bandit.sqlite")


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# ───────────────── _bucket_signal ─────────────────


def test_bucket_signal_hype_words():
    assert _bucket_signal("hype words: revolutionary") == "hype-words"


def test_bucket_signal_length():
    assert _bucket_signal("body too long: 312 exceeds 280 chars") == "length-overshoot"


def test_bucket_signal_unknown_falls_back_to_first_two_words():
    bucket = _bucket_signal("foobar baz quux unknown signal")
    assert bucket == "foobar-baz"


def test_bucket_signal_empty_returns_none():
    assert _bucket_signal("") is None
    assert _bucket_signal("   ") is None


# ───────────────── _scan_reflections ─────────────────


def test_scan_reflections_missing_file_returns_present_false(tmp_path):
    out = _scan_reflections(".not-a-real-agent", since_days=30)
    assert out == {"present": False}


def test_scan_reflections_counts_by_outcome(tmp_path):
    log = tmp_path / ".alpha" / "reflections.jsonl"
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    _write_jsonl(log, [
        {"ts": now, "outcome": "FAILED", "verbatim_signal": "hype words: revolutionary"},
        {"ts": now, "outcome": "FAILED", "verbatim_signal": "hashtag spam"},
        {"ts": now, "outcome": "PARTIAL", "verbatim_signal": "fine"},
        {"ts": now, "outcome": "SUCCESS", "verbatim_signal": "no issues"},
    ])
    out = _scan_reflections(".alpha", since_days=30)
    assert out["present"] is True
    assert out["by_outcome"] == {"FAILED": 2, "PARTIAL": 1, "SUCCESS": 1}
    bucket_names = [b for b, _ in out["top_failure_buckets"]]
    assert "hype-words" in bucket_names
    assert "hashtag-spam" in bucket_names


def test_scan_reflections_ignores_old_rows(tmp_path):
    log = tmp_path / ".alpha" / "reflections.jsonl"
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    _write_jsonl(log, [
        {"ts": old, "outcome": "FAILED", "verbatim_signal": "ancient"},
        {"ts": fresh, "outcome": "FAILED", "verbatim_signal": "recent"},
    ])
    out = _scan_reflections(".alpha", since_days=30)
    assert out["rows_in_window"] == 1


def test_scan_reflections_skips_corrupt_lines(tmp_path):
    log = tmp_path / ".alpha" / "reflections.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    log.write_text(
        "not json\n"
        + json.dumps({"ts": now, "outcome": "FAILED",
                          "verbatim_signal": "hype words"}) + "\n"
        + "{partial\n"
    )
    out = _scan_reflections(".alpha", since_days=30)
    assert out["rows_in_window"] == 1


# ───────────────── _scan_preferences ─────────────────


def test_scan_preferences_counts_by_task(tmp_path):
    log = tmp_path / ".alpha" / "preference-pairs.jsonl"
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    _write_jsonl(log, [
        {"ts": now, "task": "draft_x", "original": "a", "edited": "b"},
        {"ts": now, "task": "draft_x", "original": "c", "edited": "d"},
        {"ts": now, "task": "draft_email", "original": "e", "edited": "f"},
    ])
    out = _scan_preferences(".alpha", since_days=30)
    assert out["pairs_in_window"] == 3
    assert out["by_task"] == {"draft_x": 2, "draft_email": 1}


def test_scan_preferences_missing_file(tmp_path):
    assert _scan_preferences(".no-agent", since_days=30) == {"present": False}


# ───────────────── _scan_skills ─────────────────


def test_scan_skills_finds_files(tmp_path):
    skills_dir = tmp_path / ".solo-founder-os" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "alpha.md").write_text("# alpha")
    (skills_dir / "beta.md").write_text("# beta with more text")
    out = _scan_skills()
    names = {s["name"] for s in out}
    assert names == {"alpha", "beta"}


def test_scan_skills_empty_when_no_dir():
    assert _scan_skills() == []


# ───────────────── _scan_bandit ─────────────────


def test_scan_bandit_aggregates_winners(tmp_path):
    db = tmp_path / ".solo-founder-os" / "bandit.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE bandit_arm (
              agent TEXT, channel TEXT, variant_key TEXT,
              alpha REAL, beta REAL, n_pulls INTEGER, last_updated TEXT,
              PRIMARY KEY (agent, channel, variant_key)
            );
            INSERT INTO bandit_arm VALUES
              ('alpha-agent', 'x', 'emoji-led', 5.0, 1.0, 5, ''),
              ('alpha-agent', 'x', 'stat-led',  2.0, 4.0, 5, ''),
              ('beta-agent',  'email', 'short', 1.0, 1.0, 0, '');
        """)
    out = _scan_bandit()
    by_pair = {(b["agent"], b["channel"]): b for b in out}
    assert ("alpha-agent", "x") in by_pair
    alpha_x = by_pair[("alpha-agent", "x")]
    assert alpha_x["winner"] == "emoji-led"
    assert alpha_x["total_pulls"] == 10


def test_scan_bandit_missing_db():
    assert _scan_bandit() == []


# ───────────────── collect + render ─────────────────


def test_collect_returns_full_shape(tmp_path):
    out = collect(since_days=7)
    assert "generated_at" in out
    assert out["since_days"] == 7
    assert "per_agent" in out
    assert "skills" in out
    assert "bandit" in out


def test_render_markdown_smoke(tmp_path):
    # Plant some realistic data
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    _write_jsonl(tmp_path / ".orallexa-marketing-agent" / "reflections.jsonl", [
        {"ts": now, "outcome": "FAILED", "verbatim_signal": "hype words"},
        {"ts": now, "outcome": "SUCCESS", "verbatim_signal": "ok"},
    ])
    _write_jsonl(tmp_path / ".orallexa-marketing-agent" / "preference-pairs.jsonl", [
        {"ts": now, "task": "draft_x", "original": "a", "edited": "b"},
    ])
    skills = tmp_path / ".solo-founder-os" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    (skills / "x-emoji-led.md").write_text("# auto-promoted skill")

    report = collect(since_days=30)
    md = render_markdown(report)
    assert "# Solo Founder OS — cross-agent retro" in md
    assert "Stack-wide" in md
    assert ".orallexa-marketing-agent" in md
    assert "x-emoji-led" in md


def test_render_markdown_handles_zero_data():
    report = collect(since_days=30)
    md = render_markdown(report)
    assert "Stack-wide" in md
    # No bandit / skills sections when those are empty; that's fine.
    # Per-agent should still render with "no data yet" notes.
    assert "No SFOS-readable data yet" in md
