"""Tests for L3 skill library."""
from __future__ import annotations
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.skills import (
    MissingInputError,
    Skill,
    _placeholders_in,
    distill_skill,
    list_skills,
    load_examples,
    load_skill,
    record_example,
    render_prompt,
    save_skill,
)


# ── render_prompt ────────────────────────────────────────────


def test_placeholders_extracts_in_order():
    t = "Hi {name}, {firm} — saw your post on {topic}, again {name}."
    assert _placeholders_in(t) == ["name", "firm", "topic"]


def test_render_basic_substitution():
    s = Skill(name="t", inputs=["name"], prompt_template="Hi {name}")
    assert render_prompt(s, {"name": "Alice"}) == "Hi Alice"


def test_render_multiple_inputs():
    s = Skill(name="t", inputs=["a", "b"],
              prompt_template="{a} and {b} and {a} again")
    out = render_prompt(s, {"a": "X", "b": "Y"})
    assert out == "X and Y and X again"


def test_render_missing_input_raises():
    s = Skill(name="t", inputs=["a"], prompt_template="Hi {a}, ref {b}")
    with pytest.raises(MissingInputError):
        render_prompt(s, {"a": "X"})


def test_render_extra_inputs_ignored():
    """Extra inputs not in template don't crash render."""
    s = Skill(name="t", inputs=["a"], prompt_template="Hi {a}")
    assert render_prompt(s, {"a": "X", "extra": "Y"}) == "Hi X"


def test_render_handles_non_string_values():
    s = Skill(name="t", inputs=["count"], prompt_template="N={count}")
    assert render_prompt(s, {"count": 42}) == "N=42"


# ── save_skill / load_skill / list_skills ────────────────────


def test_save_and_load_roundtrip(tmp_path):
    s = Skill(
        name="draft-vc-email",
        inputs=["investor_name", "firm"],
        prompt_template="Hi {investor_name} at {firm}, ...",
        success_heuristic="reply within 7 days",
        examples=[
            {"ts": "2026-05-01T00:00:00+00:00",
             "inputs": {"investor_name": "Alice", "firm": "Sequoia"},
             "note": "reply 4d"},
        ],
    )
    path = save_skill(s, base=tmp_path)
    assert path.exists()
    loaded = load_skill("draft-vc-email", base=tmp_path)
    assert loaded is not None
    assert loaded.name == "draft-vc-email"
    assert loaded.inputs == ["investor_name", "firm"]
    assert "Hi {investor_name} at {firm}" in loaded.prompt_template
    assert "reply within 7 days" in loaded.success_heuristic


def test_save_overwrites_same_name(tmp_path):
    """Re-distillation should produce fewer files, not append numbered ones."""
    s1 = Skill(name="t", inputs=["a"], prompt_template="v1: {a}")
    s2 = Skill(name="t", inputs=["a"], prompt_template="v2: {a}")
    save_skill(s1, base=tmp_path)
    save_skill(s2, base=tmp_path)
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    loaded = load_skill("t", base=tmp_path)
    assert "v2" in loaded.prompt_template


def test_load_missing_returns_none(tmp_path):
    assert load_skill("never-existed", base=tmp_path) is None


def test_list_skills_sorted(tmp_path):
    save_skill(Skill(name="zebra", inputs=[], prompt_template="z"),
               base=tmp_path)
    save_skill(Skill(name="alpha", inputs=[], prompt_template="a"),
               base=tmp_path)
    save_skill(Skill(name="middle", inputs=[], prompt_template="m"),
               base=tmp_path)
    skills = list_skills(base=tmp_path)
    assert [s.name for s in skills] == ["alpha", "middle", "zebra"]


def test_list_skills_empty_dir(tmp_path):
    """No skills dir → empty list, no error."""
    assert list_skills(base=tmp_path / "nonexistent") == []


def test_save_kebab_case_filename(tmp_path):
    """Skill names with spaces / capitals get slugified into the filename."""
    s = Skill(name="Draft VC Partner Email!", inputs=[],
              prompt_template="x")
    path = save_skill(s, base=tmp_path)
    assert path.name == "draft-vc-partner-email.md"


def test_load_corrupt_file_returns_none(tmp_path):
    """A garbage skill file should not crash list_skills."""
    (tmp_path / "broken.md").write_text("not valid frontmatter\nat all")
    assert load_skill("broken", base=tmp_path) is None


def test_save_load_preserves_multiline_template(tmp_path):
    template = """You're writing to {investor_name}.

Lead with what we ship.

Subject: ≤6 words.
"""
    s = Skill(name="multiline", inputs=["investor_name"],
              prompt_template=template)
    save_skill(s, base=tmp_path)
    loaded = load_skill("multiline", base=tmp_path)
    assert "Lead with what we ship" in loaded.prompt_template
    assert "Subject: ≤6 words" in loaded.prompt_template


# ── distill_skill ────────────────────────────────────────────


def _fake_client(*, configured: bool = True,
                  template: str = "Hi {investor_name} at {firm}",
                  inputs: list[str] | None = None,
                  heuristic: str = "reply within 7 days",
                  err: str | None = None):
    c = MagicMock()
    c.configured = configured
    if err:
        c.messages_create_json.return_value = (None, err)
    else:
        c.messages_create_json.return_value = ({
            "prompt_template": template,
            "inputs": inputs if inputs is not None else
                      ["investor_name", "firm"],
            "success_heuristic": heuristic,
        }, None)
    return c


def _examples():
    return [
        {"inputs": {"investor_name": "Alice", "firm": "Sequoia"},
         "output": "Subject: ...", "note": "reply in 4d"},
        {"inputs": {"investor_name": "Bob", "firm": "Lightspeed"},
         "output": "Subject: ...", "note": "reply in 2d"},
        {"inputs": {"investor_name": "Carol", "firm": "USV"},
         "output": "Subject: ...", "note": "reply in 6d"},
    ]


def test_distill_needs_at_least_3_examples():
    out = distill_skill("x", [{"inputs": {}, "output": ""}],
                         client=_fake_client())
    assert out is None


def test_distill_unconfigured_returns_none():
    out = distill_skill("x", _examples(), client=_fake_client(configured=False))
    assert out is None


def test_distill_anthropic_error_returns_none():
    out = distill_skill("x", _examples(), client=_fake_client(err="rate limit"))
    assert out is None


def test_distill_returns_skill_with_template_and_heuristic():
    out = distill_skill("draft-vc-email", _examples(),
                         client=_fake_client())
    assert out is not None
    assert out.name == "draft-vc-email"
    assert out.inputs == ["investor_name", "firm"]
    assert "{investor_name}" in out.prompt_template
    assert out.success_heuristic == "reply within 7 days"
    # n_examples reflects the input count
    assert out.n_examples == 3


def test_distill_inputs_aligned_with_actual_placeholders():
    """If Claude returns inputs that don't match the template, prefer the
    template's actual placeholders. Otherwise render_prompt() will error."""
    fake = _fake_client(
        template="Hi {investor_name}",  # only uses 1 placeholder
        inputs=["investor_name", "firm", "thesis_hint"],  # claims 3
    )
    out = distill_skill("x", _examples(), client=fake)
    assert out is not None
    assert out.inputs == ["investor_name"]  # corrected to template's actual


def test_distill_keeps_last_10_examples_only():
    many = [
        {"inputs": {"a": str(i)}, "output": "x", "note": "ok"}
        for i in range(20)
    ]
    out = distill_skill("x", many, client=_fake_client(
        template="{a}", inputs=["a"]))
    assert out is not None
    assert len(out.examples) == 10
    # Most recent 10 — the "a" inputs should be 10..19
    assert out.examples[0]["inputs"]["a"] == "10"


def test_distill_round_trip_through_save(tmp_path):
    """Distill → save → load → render. End-to-end sanity."""
    fake = _fake_client(template="Hi {investor_name} at {firm}",
                          inputs=["investor_name", "firm"])
    skill = distill_skill("draft-vc-email", _examples(), client=fake)
    save_skill(skill, base=tmp_path)
    loaded = load_skill("draft-vc-email", base=tmp_path)
    assert loaded is not None
    rendered = render_prompt(loaded, {"investor_name": "Dave",
                                        "firm": "Founders Fund"})
    assert "Hi Dave at Founders Fund" in rendered


# ── record_example / load_examples ───────────────────────────


def test_record_and_load_examples(tmp_path):
    record_example("draft-vc-email",
                    {"investor_name": "Alice", "firm": "Sequoia"},
                    "Subject: ...\n\nBody...",
                    note="reply in 4d",
                    base=tmp_path)
    record_example("draft-vc-email",
                    {"investor_name": "Bob", "firm": "Lightspeed"},
                    "Subject: ...\n\nBody...",
                    note="reply in 2d",
                    base=tmp_path)
    loaded = load_examples("draft-vc-email", base=tmp_path)
    assert len(loaded) == 2
    assert loaded[0]["inputs"]["investor_name"] == "Alice"
    assert loaded[1]["note"] == "reply in 2d"


def test_record_example_test_mode_skips_write(monkeypatch, tmp_path):
    """SFOS_TEST_MODE=1 → record_example must NOT write to disk.
    Symmetric guard to log_outcome's so agent test suites can opt out
    via one env var in conftest."""
    monkeypatch.setenv("SFOS_TEST_MODE", "1")
    record_example("x", {"a": "b"}, "out", base=tmp_path)
    assert not (tmp_path / "examples" / "x.jsonl").exists()


def test_record_example_swallows_filesystem_errors(tmp_path):
    """Read-only filesystem etc. should not crash the calling agent."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    # Calling record_example with the blocker as base should not raise
    # (the function uses 'examples' subdir under base; that mkdir will fail)
    record_example("x", {"a": "b"}, "out", base=blocker)
    # No assertion — just verifying no exception


def test_load_examples_no_file(tmp_path):
    assert load_examples("never-existed", base=tmp_path) == []


def test_load_examples_corrupt_lines_skipped(tmp_path):
    d = tmp_path / "examples"
    d.mkdir()
    (d / "x.jsonl").write_text(
        "garbage\n"
        + json.dumps({"ts": "t", "inputs": {}, "output": "ok"})
        + "\nmore garbage\n"
    )
    out = load_examples("x", base=tmp_path)
    assert len(out) == 1
    assert out[0]["output"] == "ok"


def test_load_examples_caps_at_n(tmp_path):
    for i in range(20):
        record_example("x", {"i": i}, str(i), base=tmp_path)
    out = load_examples("x", base=tmp_path, n=5)
    assert len(out) == 5
    # Most recent 5
    assert [e["inputs"]["i"] for e in out] == [15, 16, 17, 18, 19]


# ── Full pipeline (record N → distill → save → render) ───────


def test_full_pipeline(tmp_path):
    """Simulate: agent records 3 successful examples, distill_skill turns
    them into a Skill, save_skill persists it, load+render produce a
    usable prompt for a new investor."""
    skill_dir = tmp_path / "skills"
    examples_base = tmp_path

    for inv in [
        ("Alice", "Sequoia", "reply 4d"),
        ("Bob", "Lightspeed", "reply 2d"),
        ("Carol", "USV", "reply 6d"),
    ]:
        record_example("draft-vc-email",
                        {"investor_name": inv[0], "firm": inv[1]},
                        f"Subject: vibex for {inv[0]}",
                        note=inv[2], base=examples_base)

    examples = load_examples("draft-vc-email", base=examples_base)
    assert len(examples) == 3

    fake = _fake_client(template="Hi {investor_name}, want to chat about VibeXForge?",
                          inputs=["investor_name"])
    skill = distill_skill("draft-vc-email", examples, client=fake)
    assert skill is not None

    save_skill(skill, base=skill_dir)
    loaded = load_skill("draft-vc-email", base=skill_dir)
    rendered = render_prompt(loaded, {"investor_name": "Dave"})
    assert "Hi Dave" in rendered
