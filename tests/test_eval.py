"""Tests for sfos-eval — Claude-judge over record_example data."""
from __future__ import annotations
import json
import os
import pathlib
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.eval import (
    DEFAULT_RUBRIC,
    ExampleScore,
    SkillEvalReport,
    _clamp,
    detect_drift,
    evaluate_skill,
    list_skills_with_examples,
    load_recent_reports,
    main,
    write_report,
)


def _patch_home(monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)


def _plant_examples(home: pathlib.Path, skill: str,
                      examples: list[dict]) -> pathlib.Path:
    d = home / ".solo-founder-os" / "examples"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{skill}.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in examples) + "\n")
    return p


def _fake_client(*, configured: bool = True,
                  scores_per_call: list[dict] | None = None,
                  err: str | None = None):
    """messages_create_json returns the next score dict from the queue."""
    queue = list(scores_per_call or [
        {"clarity": 4, "specificity": 4, "voice": 4, "accuracy": 4,
         "completeness": 4, "notes": "solid"},
    ])
    c = MagicMock()
    c.configured = configured

    def _create(**kwargs):
        if err:
            return (None, err)
        if not queue:
            return ({"clarity": 3, "specificity": 3, "voice": 3,
                      "accuracy": 3, "completeness": 3,
                      "notes": "mock"}, None)
        return (queue.pop(0), None)

    c.messages_create_json.side_effect = _create
    return c


# ── _clamp ───────────────────────────────────────────────


def test_clamp_within_range():
    assert _clamp(3) == 3


def test_clamp_low():
    assert _clamp(0) == 1
    assert _clamp(-5) == 1


def test_clamp_high():
    assert _clamp(10) == 5


def test_clamp_garbage():
    assert _clamp("not a number") == 1


# ── evaluate_skill ───────────────────────────────────────


def test_no_examples_returns_none(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    out = evaluate_skill("never-seen", client=_fake_client())
    assert out is None


def test_unconfigured_client_returns_none(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    _plant_examples(tmp_path, "x", [{
        "inputs": {}, "output": "real output", "ts": "t"}])
    out = evaluate_skill("x", client=_fake_client(configured=False))
    assert out is None


def test_eval_returns_report_with_mean(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    _plant_examples(tmp_path, "draft-vc-email", [
        {"inputs": {"name": "Alice"}, "output": "Subject: ..."},
        {"inputs": {"name": "Bob"}, "output": "Subject: ..."},
        {"inputs": {"name": "Carol"}, "output": "Subject: ..."},
    ])
    fake = _fake_client(scores_per_call=[
        {"clarity": 5, "specificity": 4, "voice": 5, "accuracy": 5,
         "completeness": 4, "notes": "great"},
        {"clarity": 4, "specificity": 4, "voice": 4, "accuracy": 4,
         "completeness": 4, "notes": "solid"},
        {"clarity": 3, "specificity": 3, "voice": 3, "accuracy": 3,
         "completeness": 3, "notes": "ok"},
    ])
    report = evaluate_skill("draft-vc-email", n=3, client=fake)
    assert report is not None
    assert report.skill == "draft-vc-email"
    assert report.n_examples == 3
    assert len(report.scores) == 3
    # mean of (4.6 + 4.0 + 3.0) / 3 ≈ 3.87
    assert 3.5 < report.mean_overall < 4.0


def test_eval_skips_examples_with_empty_output(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    _plant_examples(tmp_path, "x", [
        {"inputs": {}, "output": "good"},
        {"inputs": {}, "output": ""},  # skipped
        {"inputs": {}, "output": "also good"},
    ])
    fake = _fake_client()
    report = evaluate_skill("x", client=fake)
    assert report.n_examples == 2


def test_eval_handles_anthropic_error_per_row(monkeypatch, tmp_path):
    """If the judge call errors on one row, others still succeed."""
    _patch_home(monkeypatch, tmp_path)
    _plant_examples(tmp_path, "x", [
        {"inputs": {}, "output": "row1"},
        {"inputs": {}, "output": "row2"},
    ])
    c = MagicMock()
    c.configured = True
    call_idx = {"i": 0}

    def _create(**kwargs):
        call_idx["i"] += 1
        if call_idx["i"] == 1:
            return (None, "rate limit")
        return ({"clarity": 4, "specificity": 4, "voice": 4,
                  "accuracy": 4, "completeness": 4, "notes": "ok"}, None)

    c.messages_create_json.side_effect = _create
    report = evaluate_skill("x", client=c)
    # 2 examples but only 1 successfully scored
    assert report is not None
    assert report.n_examples == 1


def test_eval_clamps_judge_scores(monkeypatch, tmp_path):
    """Judge returns values out of range — they get clamped."""
    _patch_home(monkeypatch, tmp_path)
    _plant_examples(tmp_path, "x", [{"inputs": {}, "output": "row"}])
    fake = _fake_client(scores_per_call=[
        {"clarity": 99, "specificity": -3, "voice": 4,
         "accuracy": 0, "completeness": 5, "notes": "weird judge"}
    ])
    report = evaluate_skill("x", client=fake)
    s = report.scores[0]
    assert s.clarity == 5  # clamped from 99
    assert s.specificity == 1  # clamped from -3
    assert s.accuracy == 1  # clamped from 0


# ── write_report / load_recent ──────────────────────────


def test_write_report_creates_json(tmp_path):
    report = SkillEvalReport(
        skill="x", ts="2026-05-02T12:00:00+00:00", n_examples=2,
        scores=[
            ExampleScore(0, 4, 4, 4, 4, 4, 4.0, "ok"),
            ExampleScore(1, 5, 5, 5, 5, 5, 5.0, "great"),
        ],
        mean_overall=4.5, p50_overall=4.5, p10_overall=4.0,
        rubric=DEFAULT_RUBRIC,
    )
    path = write_report(report, base=tmp_path)
    assert path.exists()
    blob = json.loads(path.read_text())
    assert blob["skill"] == "x"
    assert blob["mean_overall"] == 4.5


def test_load_recent_reports_sorted(tmp_path):
    """Newest last."""
    for ts in ["2026-04-30-1200", "2026-05-01-1200", "2026-05-02-1200"]:
        path = tmp_path / f"{ts}-x.json"
        path.write_text(json.dumps({
            "skill": "x", "ts": ts, "n_examples": 1, "scores": [],
            "mean_overall": 4.0, "p50_overall": 4.0, "p10_overall": 4.0,
        }))
    reports = load_recent_reports("x", base=tmp_path)
    assert len(reports) == 3
    assert reports[0].ts < reports[-1].ts


def test_load_recent_reports_caps_n(tmp_path):
    for i in range(15):
        path = tmp_path / f"2026-05-0{i % 9 + 1}-{i:04d}-x.json"
        path.write_text(json.dumps({
            "skill": "x", "ts": str(i), "n_examples": 1, "scores": [],
            "mean_overall": float(i), "p50_overall": float(i),
            "p10_overall": float(i),
        }))
    reports = load_recent_reports("x", base=tmp_path, n=5)
    assert len(reports) == 5


def test_load_recent_reports_no_dir(tmp_path):
    assert load_recent_reports("x", base=tmp_path / "missing") == []


# ── detect_drift ───────────────────────────────────────


def _make_eval_file(base: pathlib.Path, skill: str, ts: str, mean: float):
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{ts}-{skill}.json"
    path.write_text(json.dumps({
        "skill": skill, "ts": ts, "n_examples": 1, "scores": [],
        "mean_overall": mean, "p50_overall": mean, "p10_overall": mean,
    }))


def test_drift_detected_on_drop(tmp_path):
    _make_eval_file(tmp_path, "x", "2026-05-01-1200", 4.5)
    _make_eval_file(tmp_path, "x", "2026-05-02-1200", 3.5)  # dropped 1.0
    drift = detect_drift("x", base=tmp_path, threshold=0.5)
    assert drift is not None
    assert drift["previous_mean"] == 4.5
    assert drift["current_mean"] == 3.5
    assert drift["delta"] == -1.0


def test_drift_below_threshold_returns_none(tmp_path):
    _make_eval_file(tmp_path, "x", "2026-05-01-1200", 4.5)
    _make_eval_file(tmp_path, "x", "2026-05-02-1200", 4.3)  # only 0.2 drop
    drift = detect_drift("x", base=tmp_path, threshold=0.5)
    assert drift is None


def test_drift_improvement_returns_none(tmp_path):
    """We only care about quality DROPS."""
    _make_eval_file(tmp_path, "x", "2026-05-01-1200", 3.0)
    _make_eval_file(tmp_path, "x", "2026-05-02-1200", 4.5)
    drift = detect_drift("x", base=tmp_path, threshold=0.5)
    assert drift is None


def test_drift_needs_two_reports(tmp_path):
    _make_eval_file(tmp_path, "x", "2026-05-01-1200", 4.5)
    drift = detect_drift("x", base=tmp_path)
    assert drift is None


# ── list_skills_with_examples ─────────────────────────


def test_list_skills_empty(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    assert list_skills_with_examples() == []


def test_list_skills_finds_all(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    _plant_examples(tmp_path, "skill-a", [{"inputs": {}, "output": "x"}])
    _plant_examples(tmp_path, "skill-b", [{"inputs": {}, "output": "y"}])
    assert sorted(list_skills_with_examples()) == ["skill-a", "skill-b"]


# ── CLI ───────────────────────────────────────────────


def test_main_skip_env(monkeypatch):
    monkeypatch.setenv("EVAL_SKIP", "1")
    rc = main([])
    assert rc == 0


def test_main_no_skills_returns_zero(monkeypatch, tmp_path, capsys):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("EVAL_SKIP", raising=False)
    rc = main([])
    assert rc == 0
    err = capsys.readouterr().err
    assert "No skills" in err


def test_main_reports_mode(monkeypatch, tmp_path, capsys):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("EVAL_SKIP", raising=False)
    _plant_examples(tmp_path, "x", [{"inputs": {}, "output": "row"}])
    _make_eval_file(tmp_path / ".solo-founder-os" / "evals",
                     "x", "2026-05-02-1200", 4.2)
    rc = main(["--report"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "x:" in out
    assert "4.2" in out


def test_main_evaluate_writes_files(monkeypatch, tmp_path, capsys):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("EVAL_SKIP", raising=False)
    _plant_examples(tmp_path, "draft-x", [
        {"inputs": {"name": "Alice"}, "output": "subject one"},
    ])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    import solo_founder_os.eval as ev

    class FakeClient:
        configured = True
        def messages_create_json(self, **kw):
            return ({"clarity": 4, "specificity": 4, "voice": 4,
                      "accuracy": 4, "completeness": 4, "notes": "ok"}, None)

    monkeypatch.setattr(ev, "AnthropicClient", lambda **kw: FakeClient())
    rc = main([])
    assert rc == 0
    files = list((tmp_path / ".solo-founder-os" / "evals").glob("*.json"))
    assert len(files) == 1
