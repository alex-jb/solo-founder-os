"""Tests for solo_founder_os.stack_flow — cross-agent timeline events."""
from __future__ import annotations
import json
import os
import pathlib
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.stack_flow import (
    StackEvent,
    assemble_timeline,
    group_by_hour,
)


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ──────────────────────────── empty ────────────────────────────


def test_assemble_empty(tmp_path):
    assert assemble_timeline(home=tmp_path) == []


# ──────────────────────────── reflexions ────────────────────────────


def test_reflexion_event_severity_mapping(tmp_path):
    recent = (_now() - timedelta(hours=1)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": recent, "task": "draft", "outcome": "OK",
         "verbatim_signal": "fine"},
        {"ts": recent, "task": "draft", "outcome": "FAILED",
         "verbatim_signal": "rate limit"},
        {"ts": recent, "task": "draft", "outcome": "PARTIAL",
         "verbatim_signal": "missing keys"},
    ])
    events = assemble_timeline(home=tmp_path)
    assert len(events) == 3
    sev_by_outcome = {e.severity for e in events}
    assert "info" in sev_by_outcome    # OK
    assert "alert" in sev_by_outcome   # FAILED
    assert "warn" in sev_by_outcome    # PARTIAL


def test_reflexion_outside_window_excluded(tmp_path):
    old = (_now() - timedelta(days=10)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": old, "task": "t", "outcome": "OK", "verbatim_signal": "x"},
    ])
    events = assemble_timeline(home=tmp_path, since_hours=24)
    assert events == []


# ──────────────────────────── evals ────────────────────────────


def test_eval_event_low_score_warn(tmp_path):
    recent = (_now() - timedelta(hours=1)).isoformat()
    base = tmp_path / ".solo-founder-os" / "evals"
    base.mkdir(parents=True)
    (base / "2026-05-02-translate.json").write_text(json.dumps({
        "skill": "translate-en-to-zh",
        "ts": recent,
        "n_examples": 3,
        "scores": [],
        "mean_overall": 1.13,
        "p50_overall": 1.0,
        "p10_overall": 1.0,
        "rubric": "",
    }))
    events = assemble_timeline(home=tmp_path)
    assert len(events) == 1
    assert events[0].kind == "eval"
    assert events[0].severity == "warn"
    assert "translate-en-to-zh" in events[0].summary


def test_eval_event_high_score_info(tmp_path):
    recent = (_now() - timedelta(hours=1)).isoformat()
    base = tmp_path / ".solo-founder-os" / "evals"
    base.mkdir(parents=True)
    (base / "2026-05-02-x.json").write_text(json.dumps({
        "skill": "x", "ts": recent, "n_examples": 5, "scores": [],
        "mean_overall": 4.5, "p50_overall": 4.5, "p10_overall": 4.0,
        "rubric": "",
    }))
    events = assemble_timeline(home=tmp_path)
    assert events[0].severity == "info"


# ──────────────────────────── proposals ────────────────────────────


def test_proposal_event_warn(tmp_path):
    recent = (_now() - timedelta(hours=1)).isoformat()
    base = tmp_path / ".solo-founder-os" / "evolver-proposals"
    base.mkdir(parents=True)
    (base / "p1.md").write_text(
        "---\n"
        ".vc-outreach-agent\n"  # malformed line — should be skipped
        "agent: .vc-outreach-agent\n"
        "task: draft_email\n"
        "target_file: drafter.py\n"
        "occurrences: 5\n"
        f"generated_at: {recent}\n"
        "---\n\n# body\n"
    )
    events = assemble_timeline(home=tmp_path)
    assert len(events) == 1
    assert events[0].kind == "proposal"
    assert events[0].severity == "warn"
    assert "drafter.py" in events[0].summary


# ──────────────────────────── HITL ────────────────────────────


def test_hitl_event_uses_file_mtime(tmp_path):
    pdir = tmp_path / ".vc-outreach-agent" / "queue" / "pending"
    pdir.mkdir(parents=True)
    f = pdir / "draft.md"
    f.write_text("x")
    # Force mtime to ~now so the cutoff lets it through
    now_ts = time.time()
    os.utime(f, (now_ts, now_ts))
    events = assemble_timeline(home=tmp_path)
    hitl_events = [e for e in events if e.kind == "hitl"]
    assert len(hitl_events) == 1
    assert hitl_events[0].agent == ".vc-outreach-agent"
    assert "pending: draft.md" in hitl_events[0].summary


def test_hitl_event_outside_window_excluded(tmp_path):
    pdir = tmp_path / ".vc-outreach-agent" / "queue" / "pending"
    pdir.mkdir(parents=True)
    f = pdir / "old.md"
    f.write_text("x")
    # Backdate mtime 10 days
    old = time.time() - (10 * 86400)
    os.utime(f, (old, old))
    events = assemble_timeline(home=tmp_path, since_hours=24)
    assert all(e.kind != "hitl" for e in events)


# ──────────────────────────── group_by_hour ────────────────────────────


def test_group_by_hour_buckets_correctly():
    e1 = StackEvent(ts="2026-05-02T16:39:12+00:00", agent="a",
                      kind="reflexion", summary="x")
    e2 = StackEvent(ts="2026-05-02T16:55:01+00:00", agent="a",
                      kind="reflexion", summary="y")
    e3 = StackEvent(ts="2026-05-02T17:01:00+00:00", agent="b",
                      kind="eval", summary="z")
    grouped = group_by_hour([e1, e2, e3])
    keys = list(grouped.keys())
    assert "2026-05-02 16:00" in keys
    assert "2026-05-02 17:00" in keys
    assert len(grouped["2026-05-02 16:00"]) == 2
    assert len(grouped["2026-05-02 17:00"]) == 1


def test_assemble_sorts_newest_first(tmp_path):
    ts_old = (_now() - timedelta(hours=5)).isoformat()
    ts_new = (_now() - timedelta(hours=1)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": ts_old, "task": "t", "outcome": "OK",
         "verbatim_signal": "old"},
        {"ts": ts_new, "task": "t", "outcome": "OK",
         "verbatim_signal": "new"},
    ])
    events = assemble_timeline(home=tmp_path)
    assert events[0].ts > events[-1].ts
    assert "new" in events[0].summary
