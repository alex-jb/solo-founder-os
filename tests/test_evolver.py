"""Tests for L4 evolver — PR-gated self-improvement.

Critical surface: safety gates. The evolver must NEVER propose changes
to auth/secret/billing/anthropic_client paths. Tests assert the gate
holds even when Haiku tries to suggest unsafe targets.
"""
from __future__ import annotations
import json
import os
import pathlib
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.evolver import (
    DEFAULT_AGENT_REPOS,
    FailurePattern,
    Proposal,
    _bucket_signal,
    find_drift_patterns,
    find_recurring_patterns,
    is_safe_path,
    main,
    synthesize_proposal,
    write_proposal_artifact,
)


# ── is_safe_path: the safety gate ─────────────────────────────


def test_safe_paths_allowed():
    assert is_safe_path("vc_outreach_agent/drafter.py")
    assert is_safe_path("bilingual_sync/translator.py")
    assert is_safe_path("customer_discovery_agent/cluster.py")
    assert is_safe_path("funnel_analytics_agent/summarizer.py")
    assert is_safe_path("agent/prompts/system.py")
    assert is_safe_path("templates/draft_template.py")


def test_blocked_paths_rejected():
    """Even if name pattern matches, blocked substrings always lose."""
    assert not is_safe_path("vc_outreach_agent/auth_helper.py")
    assert not is_safe_path("agent/secret_loader.py")
    assert not is_safe_path("agent/credential_store.py")
    assert not is_safe_path("agent/smtp_sender.py")
    assert not is_safe_path("agent/stripe_webhook.py")
    assert not is_safe_path("agent/billing.py")
    assert not is_safe_path("solo_founder_os/anthropic_client.py")
    assert not is_safe_path("agent/.git/config")
    assert not is_safe_path("agent/migrations/0001_initial.sql")
    assert not is_safe_path("agent/.env")


def test_unrecognized_paths_rejected():
    """Files that don't match either whitelist OR blocklist are rejected
    by default — only explicitly safe files pass."""
    assert not is_safe_path("agent/random_file.py")
    assert not is_safe_path("agent/main.py")
    assert not is_safe_path("agent/utils.py")


def test_safe_path_case_insensitive():
    """Path matching shouldn't depend on case."""
    assert is_safe_path("Agent/DRAFTER.py")
    assert not is_safe_path("Agent/AUTH.py")


# ── _bucket_signal: lossy hash for grouping ─────────────────


def test_bucket_signal_strips_numbers():
    """'rate limit 429 attempt 3' and 'rate limit 502 attempt 7' should
    bucket together so we recognize them as the same pattern."""
    assert (_bucket_signal("rate limit 429 attempt 3")
            == _bucket_signal("rate limit 502 attempt 7"))


def test_bucket_signal_strips_quoted_strings():
    """Quoted email addresses / paths shouldn't break grouping."""
    a = _bucket_signal('moved file "alice@vc.com.md" to rejected')
    b = _bucket_signal('moved file "bob@vc.com.md" to rejected')
    assert a == b


def test_bucket_signal_normalizes_whitespace():
    a = _bucket_signal("foo  bar\nbaz")
    b = _bucket_signal("foo bar baz")
    assert a == b


def test_bucket_signal_empty_input():
    assert _bucket_signal("") == ""


# ── find_recurring_patterns ─────────────────────────────────


def _write_reflections(home: pathlib.Path, agent: str, rows: list[dict]):
    p = home / agent / "reflections.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_find_patterns_detects_3_occurrences(tmp_path):
    rows = [
        {"task": "draft_email", "outcome": "FAILED",
         "verbatim_signal": "rate limit 429 retry 1"},
        {"task": "draft_email", "outcome": "FAILED",
         "verbatim_signal": "rate limit 429 retry 2"},
        {"task": "draft_email", "outcome": "FAILED",
         "verbatim_signal": "rate limit 502 retry 5"},
    ]
    _write_reflections(tmp_path, ".vc-outreach-agent", rows)
    patterns = find_recurring_patterns(home=tmp_path, min_count=3)
    assert len(patterns) == 1
    assert patterns[0].count == 3
    assert patterns[0].agent == ".vc-outreach-agent"
    assert patterns[0].task == "draft_email"
    assert "rate limit" in patterns[0].signal_bucket


def test_find_patterns_below_threshold_excluded(tmp_path):
    rows = [
        {"task": "draft_email", "outcome": "FAILED",
         "verbatim_signal": "rate limit 429"},
        {"task": "draft_email", "outcome": "FAILED",
         "verbatim_signal": "rate limit 502"},
    ]
    _write_reflections(tmp_path, ".vc-outreach-agent", rows)
    assert find_recurring_patterns(home=tmp_path, min_count=3) == []


def test_find_patterns_only_failed_partial_count(tmp_path):
    """OK outcomes should not contribute to recurrence count."""
    rows = [
        {"task": "draft_email", "outcome": "FAILED",
         "verbatim_signal": "x"},
        {"task": "draft_email", "outcome": "OK",
         "verbatim_signal": "x"},
        {"task": "draft_email", "outcome": "OK",
         "verbatim_signal": "x"},
    ]
    _write_reflections(tmp_path, ".vc-outreach-agent", rows)
    assert find_recurring_patterns(home=tmp_path, min_count=2) == []


def test_find_patterns_groups_by_bucket(tmp_path):
    """Different verbatim signals that bucket the same should group."""
    rows = [
        {"task": "draft_email", "outcome": "FAILED",
         "verbatim_signal": "rate limit 429 attempt 1"},
        {"task": "draft_email", "outcome": "FAILED",
         "verbatim_signal": "rate limit 429 attempt 2"},
        {"task": "draft_email", "outcome": "FAILED",
         "verbatim_signal": "rate limit 502 attempt 9"},
    ]
    _write_reflections(tmp_path, ".vc-outreach-agent", rows)
    patterns = find_recurring_patterns(home=tmp_path, min_count=3)
    # All 3 different verbatim bucket to 'rate limit n attempt n'
    assert len(patterns) == 1
    assert patterns[0].count == 3


def test_find_patterns_no_files_returns_empty(tmp_path):
    assert find_recurring_patterns(home=tmp_path) == []


def test_find_patterns_corrupt_lines_skipped(tmp_path):
    """Garbage JSONL rows shouldn't crash the scan."""
    p = tmp_path / ".vc-outreach-agent" / "reflections.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        "not json\n"
        + json.dumps({"task": "t", "outcome": "FAILED",
                       "verbatim_signal": "x"}) + "\n"
        + "more garbage\n"
    )
    # Single failure is below threshold of 3, but we shouldn't crash
    patterns = find_recurring_patterns(home=tmp_path, min_count=1)
    assert len(patterns) == 1


def test_find_patterns_sorted_by_count(tmp_path):
    """Most-frequent patterns first."""
    rows_a = [{"task": "t1", "outcome": "FAILED",
               "verbatim_signal": "small failure"} for _ in range(3)]
    rows_b = [{"task": "t2", "outcome": "FAILED",
               "verbatim_signal": "big failure"} for _ in range(10)]
    _write_reflections(tmp_path, ".agent-1", rows_a)
    _write_reflections(tmp_path, ".agent-2", rows_b)
    patterns = find_recurring_patterns(
        home=tmp_path, min_count=3,
        agent_dirs=[".agent-1", ".agent-2"])
    assert len(patterns) == 2
    assert patterns[0].count == 10
    assert patterns[1].count == 3


# ── synthesize_proposal: safety + Haiku I/O ─────────────────


def _fake_client(*, configured: bool = True, target: str = "vc_outreach_agent/drafter.py",
                  diff: str = "+ new line\n",
                  rationale: str = "fix the recurring rate-limit issue",
                  test_case: str = "",
                  err: str | None = None):
    c = MagicMock()
    c.configured = configured
    if err:
        c.messages_create_json.return_value = (None, err)
    else:
        c.messages_create_json.return_value = ({
            "target_file": target,
            "rationale": rationale,
            "diff": diff,
            "test_case": test_case,
        }, None)
    return c


def _pat() -> FailurePattern:
    return FailurePattern(
        agent=".vc-outreach-agent",
        task="draft_email",
        signal_bucket="rate limit n",
        count=5,
        sample_signals=["rate limit 429", "rate limit 502"],
    )


def test_synthesize_unconfigured_returns_none():
    out = synthesize_proposal(_pat(), client=_fake_client(configured=False))
    assert out is None


def test_synthesize_anthropic_error_returns_none():
    out = synthesize_proposal(_pat(), client=_fake_client(err="rate limit"))
    assert out is None


def test_synthesize_safe_target_accepted():
    fake = _fake_client(target="vc_outreach_agent/drafter.py")
    out = synthesize_proposal(_pat(), client=fake)
    assert out is not None
    assert out.target_file == "vc_outreach_agent/drafter.py"
    assert "+ new line" in out.diff


def test_synthesize_unsafe_target_rejected():
    """If Haiku tries to modify auth/secret/billing, we reject the proposal."""
    fake = _fake_client(target="vc_outreach_agent/auth_helper.py")
    out = synthesize_proposal(_pat(), client=fake)
    assert out is None  # Hard reject — never propose unsafe paths


def test_synthesize_anthropic_client_modification_rejected():
    """Even more critically, NEVER propose changes to AnthropicClient itself."""
    fake = _fake_client(target="solo_founder_os/anthropic_client.py")
    out = synthesize_proposal(_pat(), client=fake)
    assert out is None


def test_synthesize_empty_diff_returns_proposal_with_rationale():
    """Haiku decides no fix fits — we still return the proposal so reviewer
    sees what was considered + the rationale."""
    fake = _fake_client(diff="")
    out = synthesize_proposal(_pat(), client=fake)
    assert out is not None
    assert out.diff == ""
    assert out.rationale  # non-empty


def test_synthesize_unrecognized_target_rejected():
    """Random files outside the safe list are rejected."""
    fake = _fake_client(target="vc_outreach_agent/main.py")
    out = synthesize_proposal(_pat(), client=fake)
    assert out is None


# ── write_proposal_artifact ─────────────────────────────────


def test_artifact_includes_metadata(tmp_path):
    proposal = Proposal(
        pattern=_pat(),
        target_file="vc_outreach_agent/drafter.py",
        rationale="add retry-with-backoff on 429",
        diff="+ retries: int = 3\n",
        test_case="def test_drafter_retries_on_429(): pass",
    )
    path = write_proposal_artifact(proposal, out_dir=tmp_path)
    assert path.exists()
    md = path.read_text()
    assert "vc_outreach_agent/drafter.py" in md
    assert "retry-with-backoff" in md
    assert "+ retries: int = 3" in md
    assert "test_drafter_retries_on_429" in md
    assert "## Pattern" in md
    assert "## Proposed diff" in md
    assert "## How to apply" in md


def test_artifact_handles_empty_diff(tmp_path):
    proposal = Proposal(
        pattern=_pat(),
        target_file="(none)",
        rationale="config issue, not a code fix",
        diff="",
        test_case="",
    )
    path = write_proposal_artifact(proposal, out_dir=tmp_path)
    md = path.read_text()
    assert "(none — pattern is not amenable to an automatic fix)" in md


# ── main() CLI ──────────────────────────────────────────────


def test_main_skip_env(monkeypatch):
    monkeypatch.setenv("EVOLVER_SKIP", "1")
    rc = main([])
    assert rc == 0


def test_main_no_patterns_returns_zero(monkeypatch, tmp_path, capsys):
    """Empty stack → no patterns → exit 0 with informational message."""
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("EVOLVER_SKIP", raising=False)
    rc = main([])
    assert rc == 0
    err = capsys.readouterr().err
    assert "No recurring failure patterns" in err


def test_main_with_patterns_writes_artifacts(monkeypatch, tmp_path, capsys):
    """End-to-end: plant 3 failures → main runs → artifact files appear."""
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    rows = [{"task": "draft_email", "outcome": "FAILED",
             "verbatim_signal": f"rate limit 429 attempt {i}"}
            for i in range(3)]
    _write_reflections(tmp_path, ".vc-outreach-agent", rows)

    import solo_founder_os.evolver as ev
    class FakeClient:
        configured = True
        def messages_create_json(self, **kw):
            return ({
                "target_file": "vc_outreach_agent/drafter.py",
                "rationale": "add backoff",
                "diff": "+ retries=3\n",
                "test_case": "",
            }, None)
    monkeypatch.setattr(ev, "AnthropicClient",
                         lambda **kw: FakeClient())

    rc = main([])
    assert rc == 0
    artifacts = list((tmp_path / ".solo-founder-os" / "evolver-proposals")
                      .glob("*.md"))
    assert len(artifacts) == 1
    md = artifacts[0].read_text()
    assert "draft_email" in md


def test_default_agent_repos_registry():
    """Sanity: registry has all 7 agent slugs."""
    assert len(DEFAULT_AGENT_REPOS) == 7
    slugs = [a for a, _ in DEFAULT_AGENT_REPOS]
    assert ".vc-outreach-agent" in slugs
    assert ".funnel-analytics-agent" in slugs


# ── L4↔L6 wire-up: find_drift_patterns ─────────────────────────


def _plant_eval_pair(home: pathlib.Path, skill: str,
                       prev_mean: float, curr_mean: float) -> None:
    """Plant 2 eval reports for the skill; older first so detect_drift
    picks them up in chronological order."""
    examples_dir = home / ".solo-founder-os" / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)
    (examples_dir / f"{skill}.jsonl").write_text(
        json.dumps({"ts": "2026-05-01T00:00:00+00:00",
                     "inputs": {}, "output": "x", "note": ""}) + "\n",
        encoding="utf-8",
    )
    evals_dir = home / ".solo-founder-os" / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)
    older = {
        "skill": skill,
        "ts": "2026-05-01T00:00:00+00:00",
        "n_examples": 5,
        "scores": [],
        "mean_overall": prev_mean,
        "p50_overall": prev_mean,
        "p10_overall": prev_mean,
        "rubric": "",
    }
    newer = {
        "skill": skill,
        "ts": "2026-05-02T00:00:00+00:00",
        "n_examples": 5,
        "scores": [
            {"example_index": 0, "clarity": 2, "specificity": 2,
              "voice": 2, "accuracy": 2, "completeness": 2,
              "overall": curr_mean,
              "notes": "weak voice; generic phrasing"},
        ],
        "mean_overall": curr_mean,
        "p50_overall": curr_mean,
        "p10_overall": curr_mean,
        "rubric": "",
    }
    # Filenames must sort chronologically — load_recent_reports uses
    # sorted glob order and takes the last N.
    (evals_dir / f"2026-05-01-0900-{skill}.json").write_text(
        json.dumps(older), encoding="utf-8")
    (evals_dir / f"2026-05-02-0900-{skill}.json").write_text(
        json.dumps(newer), encoding="utf-8")


def test_find_drift_patterns_detects_drop(monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    _plant_eval_pair(tmp_path, "draft-vc-email",
                       prev_mean=4.5, curr_mean=3.2)  # delta -1.3 > 0.5
    pats = find_drift_patterns(drift_threshold=0.5)
    assert len(pats) == 1
    p = pats[0]
    assert p.task == "draft-vc-email"
    assert p.agent == ".solo-founder-os"
    assert "quality-drift" in p.signal_bucket
    assert p.count >= 3
    # Sample signals should include the rubric notes from the worst row
    assert any("weak voice" in s for s in p.sample_signals)


def test_find_drift_patterns_ignores_small_drop(monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    _plant_eval_pair(tmp_path, "draft-vc-email",
                       prev_mean=4.0, curr_mean=3.7)  # delta -0.3 < 0.5
    pats = find_drift_patterns(drift_threshold=0.5)
    assert pats == []


def test_find_drift_patterns_no_evals_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    pats = find_drift_patterns(drift_threshold=0.5)
    assert pats == []


def test_find_drift_patterns_count_scales_with_magnitude(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    _plant_eval_pair(tmp_path, "draft-a", prev_mean=4.5, curr_mean=4.0)
    _plant_eval_pair(tmp_path, "draft-b", prev_mean=4.5, curr_mean=2.0)
    pats = find_drift_patterns(drift_threshold=0.4)
    by_skill = {p.task: p for p in pats}
    # bigger drop sorts first via -count
    assert pats[0].task == "draft-b"
    assert by_skill["draft-b"].count > by_skill["draft-a"].count


def test_main_picks_up_drift_patterns(monkeypatch, tmp_path, capsys):
    """End-to-end: only L6 drift signal (no reflexion rows) → main runs
    → an artifact gets written tagged as quality-drift."""
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("EVOLVER_SKIP", raising=False)
    _plant_eval_pair(tmp_path, "draft-vc-email",
                       prev_mean=4.5, curr_mean=3.0)

    import solo_founder_os.evolver as ev
    class FakeClient:
        configured = True
        def messages_create_json(self, **kw):
            return ({
                "target_file": "vc_outreach_agent/drafter.py",
                "rationale": "tighten voice spec",
                "diff": "+ # drift fix\n",
                "test_case": "",
            }, None)
    monkeypatch.setattr(ev, "AnthropicClient", lambda **kw: FakeClient())

    rc = main([])
    assert rc == 0
    err = capsys.readouterr().err
    assert "L6 quality-drift signal" in err
    artifacts = list((tmp_path / ".solo-founder-os" / "evolver-proposals")
                      .glob("*.md"))
    assert len(artifacts) == 1
    md = artifacts[0].read_text()
    assert "draft-vc-email" in md
    assert "quality-drift" in md


def test_main_drift_disabled_with_zero_threshold(
    monkeypatch, tmp_path, capsys,
):
    """--drift-threshold 0 turns off the L6 path entirely (escape hatch)."""
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("EVOLVER_SKIP", raising=False)
    _plant_eval_pair(tmp_path, "draft-vc-email",
                       prev_mean=4.5, curr_mean=3.0)
    rc = main(["--drift-threshold", "0"])
    assert rc == 0
    err = capsys.readouterr().err
    # No drift line, no artifacts (no reflexion rows planted either)
    assert "L6 quality-drift signal" not in err
    artifacts = list((tmp_path / ".solo-founder-os" / "evolver-proposals")
                      .glob("*.md")) if (tmp_path / ".solo-founder-os"
                      / "evolver-proposals").exists() else []
    assert artifacts == []
