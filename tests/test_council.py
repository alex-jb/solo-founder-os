"""Tests for L5 council — multi-agent meeting / debate."""
from __future__ import annotations
import os
import pathlib
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.council import (
    BUG_TRIAGE_COUNCIL,
    COUNCIL_REGISTRY,
    Contribution,
    CouncilMember,
    CouncilOutput,
    LAUNCH_READINESS_COUNCIL,
    PRICING_DECISION_COUNCIL,
    auto_convene_from_drift,
    convene_drift_council,
    hold_meeting,
    main,
    render_meeting_md,
    write_meeting,
)


def _fake_client(*, configured: bool = True,
                  texts_per_call: list[str] | None = None,
                  err: str | None = None):
    """Fake AnthropicClient. Returns texts_per_call[0] then [1] then …
    on each successive messages_create call."""
    texts = list(texts_per_call or ["mock contribution"])
    c = MagicMock()
    c.configured = configured

    def _create(**kwargs):
        if err:
            return (None, err)
        text = texts.pop(0) if texts else "(empty)"
        # Build a fake resp with content[0].text
        block = MagicMock()
        block.type = "text"
        block.text = text
        resp = MagicMock()
        resp.content = [block]
        # AnthropicClient.extract_text uses resp.content[0].text
        return (resp, None)

    c.messages_create.side_effect = _create
    return c


def _members(n: int = 2) -> list[CouncilMember]:
    return [
        CouncilMember(
            agent_name=f".test-agent-{i}",
            role=f"role-{i}",
            system_prompt=f"perspective {i}",
            reflections_task=None,  # avoid filesystem reads in tests
        )
        for i in range(n)
    ]


# ── hold_meeting ────────────────────────────────────────────


def test_unconfigured_returns_degraded_output():
    out = hold_meeting(
        topic="test", question="why?", members=_members(2),
        client=_fake_client(configured=False),
    )
    assert "no ANTHROPIC_API_KEY" in out.synthesis
    assert len(out.contributions) == 2
    for c in out.contributions:
        assert "no ANTHROPIC_API_KEY" in c.body


def test_each_member_gets_one_call():
    fake = _fake_client(texts_per_call=[
        "vc says: signal pricing discipline",
        "funnel says: cost-audit data is clear",
        "synthesis: agreement on price floor"])
    out = hold_meeting(
        topic="pricing", question="what tier?",
        members=_members(2), client=fake,
    )
    # 2 members + 1 synthesis = 3 calls
    assert fake.messages_create.call_count == 3
    assert out.contributions[0].body == "vc says: signal pricing discipline"
    assert out.contributions[1].body == "funnel says: cost-audit data is clear"
    assert "synthesis" in out.synthesis


def test_member_call_failure_doesnt_break_meeting():
    """If one member's Claude call errors out, others + synthesis still run."""
    # First call errors, rest succeed
    c = MagicMock()
    c.configured = True
    call_count = {"i": 0}
    def _create(**kwargs):
        call_count["i"] += 1
        if call_count["i"] == 1:
            return (None, "rate limit")
        block = MagicMock()
        block.type = "text"
        block.text = f"call {call_count['i']}"
        resp = MagicMock()
        resp.content = [block]
        return (resp, None)
    c.messages_create.side_effect = _create

    out = hold_meeting(
        topic="t", question="q", members=_members(3), client=c,
    )
    # First contribution shows error, second + third work, synthesis works
    assert "unavailable" in out.contributions[0].body
    assert "call 2" in out.contributions[1].body
    assert "call 3" in out.contributions[2].body
    assert "call 4" in out.synthesis


def test_synthesis_failure_doesnt_break_meeting():
    """If synthesis call errors, contributions still surface."""
    # 2 successful member calls, then synthesis errors
    c = MagicMock()
    c.configured = True
    call_idx = {"i": 0}
    def _create(**kwargs):
        call_idx["i"] += 1
        if call_idx["i"] >= 3:  # 3rd call is synthesis
            return (None, "synth failed")
        block = MagicMock()
        block.type = "text"
        block.text = "ok"
        resp = MagicMock()
        resp.content = [block]
        return (resp, None)
    c.messages_create.side_effect = _create

    out = hold_meeting(topic="t", question="q",
                        members=_members(2), client=c)
    assert "synth failed" in out.synthesis or "unavailable" in out.synthesis


# ── render + write ─────────────────────────────────────────


def test_render_includes_all_sections():
    out = CouncilOutput(
        topic="pricing", question="$3 or $20?",
        members=_members(2),
        contributions=[
            Contribution(member=_members(2)[0], body="prefer $3"),
            Contribution(member=_members(2)[1], body="prefer $20"),
        ],
        synthesis="## Recommendation\nGo with $3 to test, escalate to $20.",
    )
    md = render_meeting_md(out)
    assert "topic: pricing" in md
    assert "$3 or $20?" in md
    assert "## Contributions" in md
    assert "prefer $3" in md
    assert "prefer $20" in md
    assert "## Synthesis" in md
    assert "$3 to test" in md


def test_write_meeting_creates_markdown(tmp_path):
    out = CouncilOutput(
        topic="launch readiness",
        question="any blockers?",
        members=[],
        contributions=[],
        synthesis="all green",
        generated_at="2026-05-02T12:00:00+00:00",
    )
    path = write_meeting(out, base=tmp_path)
    assert path.exists()
    md = path.read_text()
    assert "launch readiness" in md
    assert "all green" in md


def test_write_appends_suffix_on_collision(tmp_path):
    """Same topic twice in a day → second one gets a numeric suffix."""
    out = CouncilOutput(topic="t", question="q", members=[],
                         contributions=[], synthesis="x")
    p1 = write_meeting(out, base=tmp_path)
    p2 = write_meeting(out, base=tmp_path)
    assert p1 != p2
    assert "-2.md" in str(p2)


# ── Predefined councils ────────────────────────────────────


def test_launch_readiness_has_three_perspectives():
    assert len(LAUNCH_READINESS_COUNCIL) == 3
    names = [m.agent_name for m in LAUNCH_READINESS_COUNCIL]
    assert ".funnel-analytics-agent" in names
    assert ".orallexa-marketing-agent" in names
    assert ".build-quality-agent" in names


def test_pricing_decision_has_three_perspectives():
    assert len(PRICING_DECISION_COUNCIL) == 3
    names = [m.agent_name for m in PRICING_DECISION_COUNCIL]
    assert ".cost-audit-agent" in names
    assert ".vc-outreach-agent" in names


def test_bug_triage_has_three_perspectives():
    assert len(BUG_TRIAGE_COUNCIL) == 3


def test_council_registry_has_all_three():
    assert "launch-readiness" in COUNCIL_REGISTRY
    assert "pricing" in COUNCIL_REGISTRY
    assert "bug-triage" in COUNCIL_REGISTRY


# ── CLI ─────────────────────────────────────────────────────


def test_main_skip_env(monkeypatch):
    monkeypatch.setenv("COUNCIL_SKIP", "1")
    rc = main(["t", "q"])
    assert rc == 0


def test_main_dry_run_prints_no_file(monkeypatch, tmp_path, capsys):
    """--dry-run shouldn't touch disk."""
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.delenv("COUNCIL_SKIP", raising=False)

    import solo_founder_os.council as cm
    fake = _fake_client(texts_per_call=[
        "perspective 1", "perspective 2", "perspective 3", "synthesis"])
    monkeypatch.setattr(cm, "AnthropicClient", lambda **kw: fake)

    rc = main(["test topic", "test question", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "test topic" in out
    # Council-meetings dir should not exist
    assert not (tmp_path / ".solo-founder-os" / "council-meetings").exists()


def test_main_writes_file_when_not_dry_run(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.delenv("COUNCIL_SKIP", raising=False)
    import solo_founder_os.council as cm
    monkeypatch.setattr(
        cm, "AnthropicClient",
        lambda **kw: _fake_client(texts_per_call=[
            "p1", "p2", "p3", "synthesis"]))
    monkeypatch.setattr(cm, "COUNCIL_DIR",
                         tmp_path / ".solo-founder-os" / "council-meetings")
    rc = main(["t", "q"])
    assert rc == 0
    files = list((tmp_path / ".solo-founder-os" / "council-meetings")
                  .glob("*.md"))
    assert len(files) == 1


# ── L5↔L6 drift triggers ─────────────────────────────────────


def _plant_drift_evals(home: pathlib.Path, skill: str,
                          prev_mean: float, curr_mean: float) -> None:
    """Plant the example file + 2 eval reports for `skill` so detect_drift
    can see them. Mirrors the pattern in test_evolver._plant_eval_pair."""
    import json
    examples_dir = home / ".solo-founder-os" / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)
    (examples_dir / f"{skill}.jsonl").write_text(
        json.dumps({"ts": "2026-05-01T00:00:00+00:00",
                     "inputs": {}, "output": "x", "note": ""}) + "\n",
        encoding="utf-8",
    )
    evals_dir = home / ".solo-founder-os" / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)
    base_report = {
        "skill": skill, "n_examples": 5, "scores": [],
        "p50_overall": 0.0, "p10_overall": 0.0, "rubric": "",
    }
    older = {**base_report, "ts": "2026-05-01T00:00:00+00:00",
              "mean_overall": prev_mean, "p50_overall": prev_mean,
              "p10_overall": prev_mean}
    newer = {**base_report, "ts": "2026-05-02T00:00:00+00:00",
              "mean_overall": curr_mean, "p50_overall": curr_mean,
              "p10_overall": curr_mean}
    (evals_dir / f"2026-05-01-0900-{skill}.json").write_text(json.dumps(older))
    (evals_dir / f"2026-05-02-0900-{skill}.json").write_text(json.dumps(newer))


def test_convene_drift_council_returns_synthesized_output():
    """convene_drift_council should pass the drift dict through into a
    well-formed CouncilOutput with topic + question derived from drift."""
    fake = _fake_client(texts_per_call=["p1", "p2", "p3", "synthesis"])
    drift = {
        "skill": "draft-vc-email",
        "previous_mean": 4.5,
        "current_mean": 2.1,
        "delta": -2.4,
        "reports_compared": ["t1", "t2"],
    }
    out = convene_drift_council("draft-vc-email", drift, client=fake)
    assert "draft-vc-email" in out.topic
    assert "4.5" in out.question
    assert "2.1" in out.question
    assert "-2.40" in out.question
    assert out.synthesis == "synthesis"


def test_auto_convene_from_drift_skips_below_threshold(tmp_path, monkeypatch):
    """A drift of 0.3 should NOT fire a council when threshold=0.7."""
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    _plant_drift_evals(tmp_path, "draft-x",
                          prev_mean=4.0, curr_mean=3.7)  # delta -0.3
    import solo_founder_os.council as cm
    monkeypatch.setattr(
        cm, "AnthropicClient",
        lambda **kw: _fake_client(texts_per_call=[
            "p1", "p2", "p3", "synth"]))
    monkeypatch.setattr(cm, "COUNCIL_DIR",
                         tmp_path / ".solo-founder-os" / "council-meetings")
    out = auto_convene_from_drift(threshold=0.7)
    assert out == []


def test_auto_convene_from_drift_fires_on_severe_drop(tmp_path, monkeypatch):
    """A drift of 2.5 (4.5 → 2.0) SHOULD fire and write a meeting file."""
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    _plant_drift_evals(tmp_path, "draft-vc-email",
                          prev_mean=4.5, curr_mean=2.0)  # delta -2.5
    import solo_founder_os.council as cm
    monkeypatch.setattr(
        cm, "AnthropicClient",
        lambda **kw: _fake_client(texts_per_call=[
            "p1", "p2", "p3", "synth"]))
    monkeypatch.setattr(cm, "COUNCIL_DIR",
                         tmp_path / ".solo-founder-os" / "council-meetings")
    results = auto_convene_from_drift(threshold=0.7)
    assert len(results) == 1
    skill, meeting, path = results[0]
    assert skill == "draft-vc-email"
    assert meeting.synthesis == "synth"
    assert path is not None and path.exists()
    assert path.suffix == ".md"


def test_auto_convene_dry_run_skips_write(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    _plant_drift_evals(tmp_path, "draft-x",
                          prev_mean=4.5, curr_mean=2.0)
    import solo_founder_os.council as cm
    monkeypatch.setattr(
        cm, "AnthropicClient",
        lambda **kw: _fake_client(texts_per_call=[
            "p1", "p2", "p3", "synth"]))
    results = auto_convene_from_drift(threshold=0.7, write=False)
    assert len(results) == 1
    _, _, path = results[0]
    assert path is None


def test_main_auto_from_drift_with_no_drift_exits_clean(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("COUNCIL_SKIP", raising=False)
    rc = main(["--auto-from-drift"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "No drift signals" in err


def test_main_auto_from_drift_writes_meeting(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.delenv("COUNCIL_SKIP", raising=False)
    _plant_drift_evals(tmp_path, "draft-x", prev_mean=4.5, curr_mean=2.0)
    import solo_founder_os.council as cm
    monkeypatch.setattr(
        cm, "AnthropicClient",
        lambda **kw: _fake_client(texts_per_call=[
            "p1", "p2", "p3", "synth"]))
    monkeypatch.setattr(cm, "COUNCIL_DIR",
                         tmp_path / ".solo-founder-os" / "council-meetings")
    rc = main(["--auto-from-drift"])
    assert rc == 0
    files = list((tmp_path / ".solo-founder-os" / "council-meetings")
                  .glob("*.md"))
    assert len(files) == 1


def test_main_topic_required_when_not_auto_mode(
    monkeypatch, tmp_path, capsys,
):
    """Without --auto-from-drift, missing topic + question is a usage
    error, not a silent no-op."""
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("COUNCIL_SKIP", raising=False)
    import pytest
    with pytest.raises(SystemExit):
        main([])
