"""L3 skill library — inter-task learning.

When the same kind of task succeeds N≥3 times, distill the pattern as a
reusable "skill" — a markdown file with a parameterized prompt template,
the input schema, and a success heuristic. Next time a similar task
shows up, the agent picks the skill instead of constructing a fresh
prompt from scratch.

Why markdown not code:
  1. Alex can read + edit any skill in Obsidian
  2. Version-controllable (git)
  3. Prompts evolve more often than code; markdown keeps it cheap
  4. No DGM-grade safety machinery needed; the file IS the artifact

Storage: ~/.solo-founder-os/skills/<name>.md

File schema (YAML frontmatter + body sections):

    ---
    name: draft-yc-partner-email
    inputs: [investor_name, firm, thesis_hint]
    created_at: 2026-05-02T07:00:00Z
    n_examples: 3
    ---

    # draft-yc-partner-email

    You're writing to {investor_name} ({firm}). Reference their thesis:
    "{thesis_hint}". Lead with what we ship, not who we are. ≤6 words subject.

    ## Success heuristic
    - Open rate ≥ 40%
    - Reply within 7 days
    - No spam complaints

    ## Past examples
    - 2026-05-01: alice@vc.com / Sequoia → reply in 4d
    - 2026-04-30: bob@vc.com / Lightspeed → reply in 2d
    - 2026-04-29: carol@vc.com / USV → reply in 6d

This module exposes:
  - Skill dataclass + load/save helpers
  - render_prompt(skill, inputs) — substitute {placeholders} safely
  - distill_skill(name, examples) — one Haiku call given 3+ examples,
    extracts the generalized template + input schema + heuristic.
    Idempotent: re-distilling with more examples sharpens the template.
"""
from __future__ import annotations
import json
import os
import pathlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .anthropic_client import AnthropicClient, DEFAULT_HAIKU_MODEL


SKILLS_DIR = pathlib.Path.home() / ".solo-founder-os" / "skills"
SKILLS_USAGE_LOG = pathlib.Path.home() / ".solo-founder-os" / "usage.jsonl"


@dataclass
class Skill:
    name: str  # kebab-case identifier
    inputs: list[str]  # ordered list of {placeholder} keys the prompt expects
    prompt_template: str  # contains {input_key} substitutions
    success_heuristic: str = ""
    examples: list[dict] = field(default_factory=list)  # last N successful uses
    created_at: Optional[str] = None
    n_examples: int = 0


# ── Render ─────────────────────────────────────────────────────


_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _placeholders_in(template: str) -> list[str]:
    """Return ordered, deduplicated list of {placeholder} keys in template."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _PLACEHOLDER.finditer(template):
        key = m.group(1)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


class MissingInputError(KeyError):
    """Raised by render_prompt when a required input is absent."""


def render_prompt(skill: Skill, inputs: dict) -> str:
    """Substitute placeholders in skill.prompt_template with inputs.

    Hard-fails with MissingInputError if any required input is absent —
    never silently emits a literal `{foo}` in a prompt heading to Claude.
    """
    needed = _placeholders_in(skill.prompt_template)
    missing = [k for k in needed if k not in inputs]
    if missing:
        raise MissingInputError(
            f"skill '{skill.name}' missing inputs: {', '.join(missing)}")

    # string.Template doesn't fit (uses $foo); use safe regex sub.
    def _replace(m: re.Match) -> str:
        key = m.group(1)
        return str(inputs[key])

    return _PLACEHOLDER.sub(_replace, skill.prompt_template)


# ── Persistence ────────────────────────────────────────────────


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:60] or "skill"


def _frontmatter_str(skill: Skill) -> str:
    """Render YAML frontmatter for the skill file. Pure stdlib (no PyYAML)."""
    inputs_str = "[" + ", ".join(skill.inputs) + "]"
    return (
        "---\n"
        f"name: {skill.name}\n"
        f"inputs: {inputs_str}\n"
        f"created_at: {skill.created_at or datetime.now(timezone.utc).isoformat()}\n"
        f"n_examples: {skill.n_examples}\n"
        "---\n"
    )


def _render_skill_md(skill: Skill) -> str:
    parts = [
        _frontmatter_str(skill),
        "",
        f"# {skill.name}",
        "",
        skill.prompt_template.strip(),
        "",
    ]
    if skill.success_heuristic:
        parts += [
            "## Success heuristic",
            "",
            skill.success_heuristic.strip(),
            "",
        ]
    if skill.examples:
        parts += ["## Past examples", ""]
        for ex in skill.examples[-10:]:
            note = ex.get("note") or ""
            ts = ex.get("ts") or ""
            parts.append(f"- {ts}: {json.dumps(ex.get('inputs') or {}, ensure_ascii=False)} → {note}")
        parts.append("")
    return "\n".join(parts)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Tiny YAML frontmatter parser. Supports name / inputs (list-literal) /
    created_at / n_examples — the four fields skills actually use.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm_block = text[4:end]
    body = text[end + 5:]
    meta: dict = {}
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        # Inputs is rendered as `[a, b, c]`
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            meta[k] = [s.strip() for s in inner.split(",") if s.strip()] \
                       if inner else []
        elif v.isdigit():
            meta[k] = int(v)
        else:
            meta[k] = v
    return meta, body


def _parse_skill_md(text: str) -> Optional[Skill]:
    """Inverse of _render_skill_md. Returns None if the file is malformed.
    Examples section is parsed best-effort; missing examples are fine."""
    meta, body = _parse_frontmatter(text)
    if not meta.get("name"):
        return None
    # Body has structure:
    #   # name
    #   <prompt>
    #   ## Success heuristic
    #   <heuristic>
    #   ## Past examples
    #   - <ex1>
    #   - <ex2>
    lines = body.lstrip().splitlines()
    # Skip leading "# <name>" header
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    prompt_lines: list[str] = []
    heuristic_lines: list[str] = []
    examples_lines: list[str] = []
    section = "prompt"
    for line in lines:
        if line.strip() == "## Success heuristic":
            section = "heuristic"
            continue
        if line.strip() == "## Past examples":
            section = "examples"
            continue
        if section == "prompt":
            prompt_lines.append(line)
        elif section == "heuristic":
            heuristic_lines.append(line)
        elif section == "examples":
            examples_lines.append(line)
    return Skill(
        name=meta["name"],
        inputs=list(meta.get("inputs") or []),
        prompt_template="\n".join(prompt_lines).strip(),
        success_heuristic="\n".join(heuristic_lines).strip(),
        examples=[],  # discard old example list on load — new ones get appended fresh
        created_at=meta.get("created_at"),
        n_examples=int(meta.get("n_examples") or 0),
    )


def skills_dir(*, base: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Return the dir where skills live. Override `base` for tests."""
    return base if base is not None else SKILLS_DIR


def save_skill(skill: Skill, *, base: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Write a Skill to <base>/<name>.md. Creates parent dirs as needed.
    Overwrites existing file at the same name (re-distillation case)."""
    d = skills_dir(base=base)
    d.mkdir(parents=True, exist_ok=True)
    if not skill.created_at:
        skill.created_at = datetime.now(timezone.utc).isoformat()
    path = d / f"{_slug(skill.name)}.md"
    path.write_text(_render_skill_md(skill), encoding="utf-8")
    return path


def load_skill(name: str, *, base: Optional[pathlib.Path] = None) -> Optional[Skill]:
    """Load a single skill by kebab-case name, or None if missing."""
    path = skills_dir(base=base) / f"{_slug(name)}.md"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    return _parse_skill_md(text)


def list_skills(*, base: Optional[pathlib.Path] = None) -> list[Skill]:
    """Return every skill in the library, sorted by name."""
    d = skills_dir(base=base)
    if not d.exists():
        return []
    out: list[Skill] = []
    for path in sorted(d.glob("*.md")):
        try:
            s = _parse_skill_md(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if s is not None:
            out.append(s)
    return out


# ── Distillation ───────────────────────────────────────────────


DISTILL_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt_template": {"type": "string"},
        "inputs": {
            "type": "array",
            "items": {"type": "string"},
        },
        "success_heuristic": {"type": "string"},
    },
    "required": ["prompt_template", "inputs", "success_heuristic"],
    "additionalProperties": False,
}


def distill_skill(
    name: str,
    examples: list[dict],
    *,
    client: Optional[AnthropicClient] = None,
    description: str = "",
) -> Optional[Skill]:
    """Given ≥3 successful examples of {inputs → output}, use Haiku to
    distill a generalized prompt template + input schema + heuristic.

    Each example is a dict like:
        {"inputs": {"investor_name": "Alice", "firm": "Sequoia", ...},
         "output": "Subject: ...\\n\\nBody: ...",
         "note": "reply in 4d"}

    Returns None if Claude is unavailable or returns garbage.
    Idempotent: re-distilling with new examples produces a sharper template.
    """
    if len(examples) < 3:
        return None

    if client is None:
        client = AnthropicClient(usage_log_path=SKILLS_USAGE_LOG)
    if not client.configured:
        return None

    # Build the prompt
    parts = [
        f"You're distilling a reusable skill called '{name}'.",
    ]
    if description:
        parts.append(f"Description: {description}")
    parts.append("")
    parts.append("Below are successful past examples of this skill.")
    parts.append("Each shows the input fields used and the resulting output.")
    parts.append("")
    for i, ex in enumerate(examples[:10]):
        parts.append(f"## Example {i + 1}")
        parts.append(f"Inputs: {json.dumps(ex.get('inputs') or {}, ensure_ascii=False)}")
        out = (ex.get("output") or "").strip()
        parts.append(f"Output:\n{out[:1500]}")
        if ex.get("note"):
            parts.append(f"Outcome: {ex['note']}")
        parts.append("")
    parts += [
        "Produce a generalized prompt template and input schema:",
        "  - prompt_template: the prompt text with {placeholders} for each input.",
        "    The placeholders should match exactly the input keys you list.",
        "    Keep it concise. Don't include 'Output:' or any meta-text.",
        "  - inputs: the ordered list of input keys the template uses.",
        "    Use snake_case. Drop inputs that don't actually drive variation",
        "    in the output.",
        "  - success_heuristic: 1-3 short bullets describing what 'good' looks",
        "    like for this skill (e.g. 'open rate ≥ 40%', 'reply within 7 days').",
        "    Pull these from the example outcomes if visible.",
        "Output JSON conforming to the schema.",
    ]
    user = "\n".join(parts)

    obj, err = client.messages_create_json(
        schema=DISTILL_SCHEMA,
        model=DEFAULT_HAIKU_MODEL,
        max_tokens=900,
        messages=[{"role": "user", "content": user}],
    )
    if err is not None or obj is None:
        return None

    template = (obj.get("prompt_template") or "").strip()
    inputs = list(obj.get("inputs") or [])
    heuristic = (obj.get("success_heuristic") or "").strip()
    if not template or not inputs:
        return None

    # Cross-check: the template's actual placeholders should match `inputs`.
    # If not, prefer the placeholders that the template actually uses (so
    # render_prompt() never errors out for "missing input" on something
    # the model never templated for).
    actual = _placeholders_in(template)
    if actual:
        inputs = actual

    return Skill(
        name=name,
        inputs=inputs,
        prompt_template=template,
        success_heuristic=heuristic,
        examples=examples[-10:],  # keep last 10
        created_at=datetime.now(timezone.utc).isoformat(),
        n_examples=len(examples),
    )


def record_example(
    skill_name: str,
    inputs: dict,
    output: str,
    *,
    note: str = "",
    base: Optional[pathlib.Path] = None,
) -> None:
    """Append a successful example to <base>/examples/<skill>.jsonl.

    Used by agents to mark "this worked" without forcing immediate
    distillation. The supervisor or a separate distill cron decides when
    to actually call Haiku to re-derive the skill.
    """
    # Test-pollution guard — see reflection.log_outcome for context.
    if os.getenv("SFOS_TEST_MODE") == "1":
        return
    d = (base or pathlib.Path.home() / ".solo-founder-os") / "examples"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "inputs": inputs,
        "output": str(output)[:5000],
        "note": str(note)[:300],
    }
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_slug(skill_name)}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Best-effort; never block the agent's main loop
        pass


def load_examples(
    skill_name: str,
    *,
    base: Optional[pathlib.Path] = None,
    n: int = 50,
) -> list[dict]:
    """Read the last N example rows for a skill. Used by distill_skill."""
    d = (base or pathlib.Path.home() / ".solo-founder-os") / "examples"
    path = d / f"{_slug(skill_name)}.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-n:]
    except Exception:
        return []
    out: list[dict] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


__all__ = [
    "Skill", "MissingInputError",
    "render_prompt",
    "save_skill", "load_skill", "list_skills", "skills_dir",
    "distill_skill", "record_example", "load_examples",
]
