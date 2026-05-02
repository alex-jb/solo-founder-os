"""Tests for solo_founder_os.ui — data loaders + CLI entry.

The Streamlit rendering itself isn't tested (Streamlit is hard to test
end-to-end and brittle). The data layer is pure-functional and covers
the surface area that actually matters: do scans correctly walk the
home dir, swallow malformed rows, and apply the right freshness badges?
"""
from __future__ import annotations
import json
import os
import pathlib
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.ui import (
    KNOWN_AGENT_DIRS,
    main,
    scan_cron_logs,
    scan_evals,
    scan_pending_queues,
    scan_proposals,
    scan_reflexions,
    stack_status,
)


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


# ──────────────────────────── scan_reflexions ────────────────────────────


def test_scan_reflexions_walks_all_known_agent_dirs(tmp_path):
    _write_jsonl(tmp_path / ".orallexa-marketing-agent" / "reflections.jsonl", [
        {"ts": "2026-05-02T10:00:00+00:00", "task": "draft", "outcome": "OK",
         "verbatim_signal": "shipped"},
    ])
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": "2026-05-02T11:00:00+00:00", "task": "email", "outcome": "FAILED",
         "verbatim_signal": "rate limit"},
    ])
    rows = scan_reflexions(home=tmp_path)
    assert len(rows) == 2
    agents = {r.agent for r in rows}
    assert ".orallexa-marketing-agent" in agents
    assert ".vc-outreach-agent" in agents
    # Sorted oldest-first
    assert rows[0].ts < rows[1].ts


def test_scan_reflexions_missing_files_silent(tmp_path):
    """No agent dirs exist → empty list, no crash."""
    assert scan_reflexions(home=tmp_path) == []


def test_scan_reflexions_swallows_corrupt_lines(tmp_path):
    p = tmp_path / ".orallexa-marketing-agent" / "reflections.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        "not json\n"
        + json.dumps({"ts": "2026-05-02T10:00:00", "task": "t",
                          "outcome": "OK", "verbatim_signal": "x"}) + "\n"
        + "{half\n"
    )
    rows = scan_reflexions(home=tmp_path)
    assert len(rows) == 1


def test_scan_reflexions_truncates_summary(tmp_path):
    long_signal = "x" * 500
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": "2026-05-02T10:00:00", "task": "t", "outcome": "FAILED",
         "verbatim_signal": long_signal},
    ])
    rows = scan_reflexions(home=tmp_path)
    assert len(rows[0].summary) == 200


# ──────────────────────────── scan_evals ────────────────────────────


def test_scan_evals_reads_eval_dir(tmp_path):
    base = tmp_path / ".solo-founder-os" / "evals"
    base.mkdir(parents=True)
    (base / "2026-05-02-foo.json").write_text(json.dumps({
        "skill": "foo", "ts": "2026-05-02", "n_examples": 5,
        "scores": [], "mean_overall": 4.2, "p50_overall": 4.0,
        "p10_overall": 3.0, "rubric": "",
    }))
    (base / "2026-05-01-foo.json").write_text(json.dumps({
        "skill": "foo", "ts": "2026-05-01", "n_examples": 3,
        "scores": [], "mean_overall": 3.5, "p50_overall": 3.5,
        "p10_overall": 3.0, "rubric": "",
    }))
    out = scan_evals(home=tmp_path)
    # Sorted oldest-first by filename
    assert [e["mean_overall"] for e in out] == [3.5, 4.2]


def test_scan_evals_missing_dir_returns_empty(tmp_path):
    assert scan_evals(home=tmp_path) == []


def test_scan_evals_skips_corrupt_json(tmp_path):
    base = tmp_path / ".solo-founder-os" / "evals"
    base.mkdir(parents=True)
    (base / "good.json").write_text(json.dumps({
        "skill": "x", "ts": "2026-05-02", "n_examples": 1,
        "scores": [], "mean_overall": 4.0, "p50_overall": 4.0,
        "p10_overall": 4.0, "rubric": "",
    }))
    (base / "bad.json").write_text("{not json")
    out = scan_evals(home=tmp_path)
    assert len(out) == 1


# ──────────────────────────── scan_proposals ────────────────────────────


def test_scan_proposals_parses_frontmatter(tmp_path):
    base = tmp_path / ".solo-founder-os" / "evolver-proposals"
    base.mkdir(parents=True)
    (base / "2026-05-02-1640-vc-draft.md").write_text(
        "---\n"
        "agent: .vc-outreach-agent\n"
        "task: draft_email\n"
        "target_file: vc_outreach_agent/drafter.py\n"
        "occurrences: 5\n"
        "generated_at: 2026-05-02T16:40:00+00:00\n"
        "---\n"
        "\n# body\n"
    )
    out = scan_proposals(home=tmp_path)
    assert len(out) == 1
    assert out[0]["agent"] == ".vc-outreach-agent"
    assert out[0]["task"] == "draft_email"
    assert out[0]["occurrences"] == "5"


def test_scan_proposals_handles_missing_frontmatter(tmp_path):
    """Plain markdown without --- frontmatter still returns a row with
    just filename + path metadata, doesn't crash."""
    base = tmp_path / ".solo-founder-os" / "evolver-proposals"
    base.mkdir(parents=True)
    (base / "weird.md").write_text("# just a header\n\nbody")
    out = scan_proposals(home=tmp_path)
    assert len(out) == 1
    assert out[0]["filename"] == "weird.md"


def test_scan_proposals_empty_dir(tmp_path):
    assert scan_proposals(home=tmp_path) == []


# ──────────────────────────── scan_pending_queues ────────────────────────────


def test_scan_pending_finds_standard_layout(tmp_path):
    pdir = tmp_path / ".vc-outreach-agent" / "queue" / "pending"
    pdir.mkdir(parents=True)
    (pdir / "draft1.md").write_text("x")
    (pdir / "draft2.md").write_text("y")
    out = scan_pending_queues(home=tmp_path)
    assert ".vc-outreach-agent" in out
    assert sorted(out[".vc-outreach-agent"]) == ["draft1.md", "draft2.md"]


def test_scan_pending_finds_nested_marketing_layout(tmp_path):
    """marketing-agent uses queue/<sub>/pending/ in some configs."""
    pdir = tmp_path / ".orallexa-marketing-agent" / "queue" / "x" / "pending"
    pdir.mkdir(parents=True)
    (pdir / "post.md").write_text("x")
    out = scan_pending_queues(home=tmp_path)
    assert ".orallexa-marketing-agent" in out
    assert "post.md" in out[".orallexa-marketing-agent"]


def test_scan_pending_skips_agents_with_no_pending(tmp_path):
    out = scan_pending_queues(home=tmp_path)
    assert out == {}


# ──────────────────────────── scan_cron_logs ────────────────────────────


def test_scan_cron_logs_tails_files(tmp_path):
    base = tmp_path / ".solo-founder-os" / "cron-logs"
    base.mkdir(parents=True)
    (base / "eval.out.log").write_text(
        "\n".join(f"line {i}" for i in range(100))
    )
    out = scan_cron_logs(home=tmp_path, tail_lines=10)
    assert "eval.out.log" in out
    assert len(out["eval.out.log"]) == 10
    assert out["eval.out.log"][-1] == "line 99"


def test_scan_cron_logs_missing_dir(tmp_path):
    assert scan_cron_logs(home=tmp_path) == {}


# ──────────────────────────── stack_status ────────────────────────────


def test_stack_status_returns_row_per_known_agent(tmp_path):
    rows = stack_status(home=tmp_path)
    assert len(rows) == len(KNOWN_AGENT_DIRS)
    # All red since no files exist
    for r in rows:
        assert r["badge"] == "🔴 never"
        assert r["age_hours"] is None


def test_stack_status_active_badge_for_recent_activity(tmp_path):
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": recent, "task": "t", "outcome": "OK",
         "verbatim_signal": "x"},
    ])
    rows = stack_status(home=tmp_path)
    by_agent = {r["agent"]: r for r in rows}
    vc = by_agent[".vc-outreach-agent"]
    assert vc["badge"] == "✅ active"
    assert vc["age_hours"] is not None
    assert vc["age_hours"] < 1


def test_stack_status_idle_badge_for_day_old(tmp_path):
    from datetime import datetime, timezone, timedelta
    yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": yesterday, "task": "t", "outcome": "OK",
         "verbatim_signal": "x"},
    ])
    rows = stack_status(home=tmp_path)
    vc = next(r for r in rows if r["agent"] == ".vc-outreach-agent")
    assert vc["badge"] == "🟡 idle"


def test_stack_status_stale_for_two_weeks_ago(tmp_path):
    from datetime import datetime, timezone, timedelta
    long_ago = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    _write_jsonl(tmp_path / ".vc-outreach-agent" / "reflections.jsonl", [
        {"ts": long_ago, "task": "t", "outcome": "OK",
         "verbatim_signal": "x"},
    ])
    rows = stack_status(home=tmp_path)
    vc = next(r for r in rows if r["agent"] == ".vc-outreach-agent")
    assert vc["badge"] == "🔴 stale"


# ──────────────────────────── CLI ────────────────────────────


def test_main_returns_2_when_streamlit_missing(monkeypatch, capsys):
    """Without streamlit installed, main should print install hint and
    return 2 — never crash with ImportError."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "streamlit" or name.startswith("streamlit."):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=blocking_import):
        rc = main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Streamlit" in err
    assert "solo-founder-os[ui]" in err


def test_main_invokes_streamlit_run(monkeypatch):
    """When streamlit is importable, main spawns `streamlit run` on the
    ui module via subprocess.call. Mock subprocess to capture argv."""
    captured: dict = {}

    def fake_call(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return 0

    # Ensure `import streamlit` succeeds in the test env
    pytest.importorskip("streamlit", reason="streamlit not installed")

    monkeypatch.setattr("subprocess.call", fake_call)
    rc = main(["--port", "9999", "--no-browser"])
    assert rc == 0
    cmd = captured["cmd"]
    assert "streamlit" in cmd
    assert "run" in cmd
    assert "9999" in cmd
    assert "--server.headless" in cmd
