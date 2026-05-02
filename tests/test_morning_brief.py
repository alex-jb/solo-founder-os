"""Tests for solo_founder_os.morning_brief — homepage digest assembly."""
from __future__ import annotations
import json
import os
import pathlib
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.morning_brief import (
    MorningBrief,
    assemble_brief,
)


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ──────────────────────────── empty home ────────────────────────────


def test_assemble_brief_empty_home(tmp_path):
    """Clean install → 3 sections (overnight / hitl / anomalies), no
    cost section. Totals zero."""
    brief = assemble_brief(home=tmp_path)
    assert isinstance(brief, MorningBrief)
    assert brief.total_pending_hitl == 0
    assert brief.total_anomalies == 0
    titles = [s.title for s in brief.sections]
    assert "Overnight activity" in titles
    assert "What needs you today" in titles
    assert "Anomalies" in titles
    # No usage logs → no cost section
    assert "Anthropic cost (last 7d)" not in titles


# ──────────────────────────── overnight activity ────────────────────────────


def test_overnight_section_counts_recent_rows(tmp_path):
    recent = (_now() - timedelta(hours=2)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": recent, "task": "draft", "outcome": "OK",
         "verbatim_signal": "shipped"},
        {"ts": recent, "task": "draft", "outcome": "FAILED",
         "verbatim_signal": "rate limit"},
    ])
    brief = assemble_brief(home=tmp_path)
    overnight = next(s for s in brief.sections
                       if s.title == "Overnight activity")
    assert "2 reflexion rows" in overnight.summary
    assert any("vc-outreach-agent" in b for b in overnight.bullets)


def test_overnight_section_ignores_old_rows(tmp_path):
    """Rows outside the window are excluded from the count."""
    old = (_now() - timedelta(days=3)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": old, "task": "t", "outcome": "OK", "verbatim_signal": "x"},
    ])
    brief = assemble_brief(home=tmp_path, since_hours=24)
    overnight = next(s for s in brief.sections
                       if s.title == "Overnight activity")
    assert "No reflexion rows" in overnight.summary


# ──────────────────────────── HITL pending ────────────────────────────


def test_hitl_section_counts_pending_per_agent(tmp_path):
    pdir = tmp_path / ".vc-outreach-agent" / "queue" / "pending"
    pdir.mkdir(parents=True)
    (pdir / "a.md").write_text("x")
    (pdir / "b.md").write_text("y")
    pdir2 = tmp_path / ".bilingual-content-sync-agent" / "queue" / "pending"
    pdir2.mkdir(parents=True)
    (pdir2 / "c.md").write_text("z")
    brief = assemble_brief(home=tmp_path)
    assert brief.total_pending_hitl == 3
    hitl = next(s for s in brief.sections
                 if s.title == "What needs you today")
    assert "3 items pending" in hitl.summary
    assert any("vc-outreach-agent" in b and "2" in b for b in hitl.bullets)


def test_hitl_section_inbox_zero(tmp_path):
    brief = assemble_brief(home=tmp_path)
    hitl = next(s for s in brief.sections
                 if s.title == "What needs you today")
    assert "Inbox zero" in hitl.summary


def test_hitl_section_warn_severity_when_over_10(tmp_path):
    pdir = tmp_path / ".vc-outreach-agent" / "queue" / "pending"
    pdir.mkdir(parents=True)
    for i in range(15):
        (pdir / f"f{i}.md").write_text("x")
    brief = assemble_brief(home=tmp_path)
    hitl = next(s for s in brief.sections
                 if s.title == "What needs you today")
    assert hitl.severity == "warn"


# ──────────────────────────── anomalies ────────────────────────────


def test_anomalies_eval_drift_low_mean(tmp_path):
    """A skill with mean_overall < 2.0 should appear as a warning bullet."""
    evals_dir = tmp_path / ".solo-founder-os" / "evals"
    evals_dir.mkdir(parents=True)
    (evals_dir / "2026-05-02-translate.json").write_text(json.dumps({
        "skill": "translate-en-to-zh",
        "ts": "2026-05-02T08:00:00+00:00",
        "n_examples": 3,
        "scores": [],
        "mean_overall": 1.13,
        "p50_overall": 1.0,
        "p10_overall": 1.0,
        "rubric": "",
    }))
    brief = assemble_brief(home=tmp_path)
    anom = next(s for s in brief.sections if s.title == "Anomalies")
    assert anom.severity in ("warn", "alert")
    assert any("translate-en-to-zh" in b and "1.13" in b
                for b in anom.bullets)
    assert brief.total_anomalies >= 1


def test_anomalies_high_failed_rate(tmp_path):
    """Agent with >30% FAILED in window → flagged."""
    recent = (_now() - timedelta(hours=2)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": recent, "task": "t", "outcome": "FAILED",
         "verbatim_signal": "x"},
        {"ts": recent, "task": "t", "outcome": "FAILED",
         "verbatim_signal": "x"},
        {"ts": recent, "task": "t", "outcome": "FAILED",
         "verbatim_signal": "x"},
        {"ts": recent, "task": "t", "outcome": "OK",
         "verbatim_signal": "x"},
    ])
    brief = assemble_brief(home=tmp_path)
    anom = next(s for s in brief.sections if s.title == "Anomalies")
    assert any("vc-outreach-agent" in b and "FAILED" in b
                for b in anom.bullets)


def test_anomalies_too_few_rows_to_judge(tmp_path):
    """3 rows with all FAILED still doesn't trigger the rate flag —
    sample size too small to be a signal."""
    recent = (_now() - timedelta(hours=2)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": recent, "task": "t", "outcome": "FAILED",
         "verbatim_signal": "x"},
        {"ts": recent, "task": "t", "outcome": "FAILED",
         "verbatim_signal": "x"},
        {"ts": recent, "task": "t", "outcome": "FAILED",
         "verbatim_signal": "x"},
    ])
    brief = assemble_brief(home=tmp_path)
    anom = next(s for s in brief.sections if s.title == "Anomalies")
    # No FAILED-rate bullet because we require >= 4 rows
    assert not any("FAILED" in b and "/3" in b for b in anom.bullets)


def test_anomalies_no_signals_clean(tmp_path):
    brief = assemble_brief(home=tmp_path)
    anom = next(s for s in brief.sections if s.title == "Anomalies")
    assert "None" in anom.summary
    assert anom.severity == "info"


# ──────────────────────────── cost ────────────────────────────


def test_cost_section_appears_when_usage_exists(tmp_path):
    now = _now()
    last_week = (now - timedelta(days=2)).isoformat()
    prior = (now - timedelta(days=10)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "usage.jsonl", [
        {"ts": last_week, "model": "haiku", "input_tokens": 100,
         "output_tokens": 50, "cost_usd": 0.05},
        {"ts": prior, "model": "haiku", "input_tokens": 100,
         "output_tokens": 50, "cost_usd": 0.03},
    ])
    brief = assemble_brief(home=tmp_path)
    titles = [s.title for s in brief.sections]
    assert "Anthropic cost (last 7d)" in titles
    cost = next(s for s in brief.sections
                 if s.title == "Anthropic cost (last 7d)")
    assert any("0.05" in b for b in cost.bullets)
    assert any("0.03" in b for b in cost.bullets)
