"""L6 evaluation harness — Claude-judge over record_example data.

Without evals, the stack accumulates examples but we never know if the
prompts are getting better, worse, or just different. This module
samples N recent examples per skill, runs Sonnet as a judge with a
1-5 rubric, persists per-skill scores over time so quality drift
becomes visible.

Pattern (Phoenix / Langfuse / DeepEval shape, simplified):

    1. For each skill in ~/.solo-founder-os/examples/, sample last N rows.
    2. For each row, ask Sonnet to score it 1-5 against a rubric.
       Default rubric: clarity, specificity, voice, accuracy, completeness.
    3. Persist {skill, ts, n_examples, score_per_row, mean, p50, p10}
       to ~/.solo-founder-os/evals/<YYYY-MM-DD>-<skill>.json.
    4. Compare against last K eval runs — if mean dropped > threshold,
       surface as input to sfos-evolver next run.

Cost: ~$0.005/skill (1 Sonnet call per sample row, default 5 rows × 5
skills = 25 calls/week ≈ $0.04/week). Sonnet over Haiku because judge
quality matters more than judge speed.

CLI:
    sfos-eval                          # eval all skills with examples
    sfos-eval --skill draft-vc-email   # one skill
    sfos-eval --n 10                   # sample 10 rows instead of 5
    sfos-eval --report                 # print latest eval summary
    sfos-eval --trend                  # mean score over last K runs

Status: ship the eval primitive + persistence + CLI. Hooking the drift
signal into sfos-evolver is a follow-up (it can read evals/*.json
directly).
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .anthropic_client import AnthropicClient, DEFAULT_SONNET_MODEL
from .skills import load_examples


EVALS_DIR = pathlib.Path.home() / ".solo-founder-os" / "evals"
EXAMPLES_DIR = pathlib.Path.home() / ".solo-founder-os" / "examples"
EVAL_USAGE_LOG = pathlib.Path.home() / ".solo-founder-os" / "usage.jsonl"


# Default rubric — overridable per call.
DEFAULT_RUBRIC = """Score the OUTPUT on each dimension 1-5:

  1. clarity         — would a reader understand without context?
  2. specificity     — concrete details vs generic platitudes
  3. voice           — does it sound like a real person, not a bot?
  4. accuracy        — does it reflect the inputs without inventing?
  5. completeness    — does it cover what's asked without padding?

5 = strong; 3 = adequate; 1 = poor. Average to 1 decimal."""


@dataclass
class ExampleScore:
    """One row's score from the judge."""
    example_index: int
    clarity: int
    specificity: int
    voice: int
    accuracy: int
    completeness: int
    overall: float
    notes: str = ""


@dataclass
class SkillEvalReport:
    skill: str
    ts: str
    n_examples: int
    scores: list[ExampleScore] = field(default_factory=list)
    mean_overall: float = 0.0
    p50_overall: float = 0.0
    p10_overall: float = 0.0
    rubric: str = ""


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "clarity": {"type": "integer"},
        "specificity": {"type": "integer"},
        "voice": {"type": "integer"},
        "accuracy": {"type": "integer"},
        "completeness": {"type": "integer"},
        "notes": {"type": "string"},
    },
    "required": ["clarity", "specificity", "voice", "accuracy",
                  "completeness", "notes"],
    "additionalProperties": False,
}


def _judge_one(
    inputs: dict,
    output: str,
    *,
    rubric: str,
    client: AnthropicClient,
    model: str,
) -> Optional[dict]:
    """One Sonnet call: score one (inputs, output) pair against the rubric.
    Returns the parsed dict or None on any failure."""
    user = (
        f"Rubric:\n{rubric}\n\n"
        f"INPUTS used to produce the output:\n"
        f"{json.dumps(inputs, indent=2, ensure_ascii=False)[:1500]}\n\n"
        f"OUTPUT to score:\n{output[:3000]}\n\n"
        "Output JSON conforming to the schema. notes: 1-2 sentences "
        "calling out the strongest and weakest dimension."
    )
    obj, err = client.messages_create_json(
        schema=JUDGE_SCHEMA,
        model=model,
        max_tokens=400,
        messages=[{"role": "user", "content": user}],
    )
    if err is not None or obj is None:
        return None
    return obj


def _clamp(v: int, lo: int = 1, hi: int = 5) -> int:
    """Clamp judge scores to the rubric range."""
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def evaluate_skill(
    skill_name: str,
    *,
    n: int = 5,
    rubric: Optional[str] = None,
    examples_base: Optional[pathlib.Path] = None,
    client: Optional[AnthropicClient] = None,
    model: str = DEFAULT_SONNET_MODEL,
) -> Optional[SkillEvalReport]:
    """Evaluate the most recent N examples for a skill. Returns None if
    no examples exist or Claude is unavailable.
    """
    rubric = rubric or DEFAULT_RUBRIC
    base = examples_base or pathlib.Path.home() / ".solo-founder-os"

    examples = load_examples(skill_name, base=base, n=n)
    if not examples:
        return None

    if client is None:
        client = AnthropicClient(usage_log_path=EVAL_USAGE_LOG)
    if not client.configured:
        return None

    scores: list[ExampleScore] = []
    for i, ex in enumerate(examples):
        inputs = ex.get("inputs") or {}
        output = ex.get("output") or ""
        if not output:
            continue
        judged = _judge_one(inputs, output, rubric=rubric,
                              client=client, model=model)
        if judged is None:
            continue
        clarity = _clamp(judged.get("clarity"))
        specificity = _clamp(judged.get("specificity"))
        voice = _clamp(judged.get("voice"))
        accuracy = _clamp(judged.get("accuracy"))
        completeness = _clamp(judged.get("completeness"))
        overall = round((clarity + specificity + voice + accuracy
                         + completeness) / 5, 1)
        scores.append(ExampleScore(
            example_index=i,
            clarity=clarity, specificity=specificity, voice=voice,
            accuracy=accuracy, completeness=completeness,
            overall=overall,
            notes=(judged.get("notes") or "")[:300],
        ))

    if not scores:
        return None

    overalls = sorted([s.overall for s in scores])
    mean_o = round(statistics.mean(overalls), 2)
    p50_o = overalls[len(overalls) // 2]
    p10_idx = max(0, int(len(overalls) * 0.1))
    p10_o = overalls[p10_idx]
    return SkillEvalReport(
        skill=skill_name,
        ts=datetime.now(timezone.utc).isoformat(),
        n_examples=len(scores),
        scores=scores,
        mean_overall=mean_o,
        p50_overall=p50_o,
        p10_overall=p10_o,
        rubric=rubric,
    )


def write_report(
    report: SkillEvalReport,
    *,
    base: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    """Persist report as JSON for trend analysis."""
    base = base or pathlib.Path.home() / ".solo-founder-os" / "evals"
    base.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    path = base / f"{today}-{report.skill}.json"
    if path.exists():
        # Same minute, same skill — tiebreak with seconds
        sec = datetime.now(timezone.utc).strftime("%S")
        path = base / f"{today}{sec}-{report.skill}.json"
    blob = asdict(report)
    path.write_text(json.dumps(blob, indent=2, ensure_ascii=False),
                     encoding="utf-8")
    return path


def list_skills_with_examples(
    *,
    base: Optional[pathlib.Path] = None,
) -> list[str]:
    """Return slugs of every skill that has at least one recorded example."""
    base = base or pathlib.Path.home() / ".solo-founder-os"
    examples_dir = base / "examples"
    if not examples_dir.exists():
        return []
    return sorted(p.stem for p in examples_dir.glob("*.jsonl"))


def load_recent_reports(
    skill_name: str,
    *,
    base: Optional[pathlib.Path] = None,
    n: int = 10,
) -> list[SkillEvalReport]:
    """Read the last N eval reports for a skill (newest last)."""
    base = base or pathlib.Path.home() / ".solo-founder-os" / "evals"
    if not base.exists():
        return []
    paths = sorted(base.glob(f"*-{skill_name}.json"))[-n:]
    out: list[SkillEvalReport] = []
    for p in paths:
        try:
            blob = json.loads(p.read_text())
        except Exception:
            continue
        scores = [ExampleScore(**s) for s in blob.get("scores") or []]
        out.append(SkillEvalReport(
            skill=blob.get("skill", "?"),
            ts=blob.get("ts", ""),
            n_examples=int(blob.get("n_examples") or 0),
            scores=scores,
            mean_overall=float(blob.get("mean_overall") or 0),
            p50_overall=float(blob.get("p50_overall") or 0),
            p10_overall=float(blob.get("p10_overall") or 0),
            rubric=blob.get("rubric", ""),
        ))
    return out


def detect_drift(
    skill_name: str,
    *,
    base: Optional[pathlib.Path] = None,
    threshold: float = 0.5,
) -> Optional[dict]:
    """Returns drift info if mean_overall dropped > threshold across
    last 2 reports. None if not enough data or no drift."""
    reports = load_recent_reports(skill_name, base=base, n=10)
    if len(reports) < 2:
        return None
    last = reports[-1]
    prev = reports[-2]
    delta = last.mean_overall - prev.mean_overall
    if delta < -threshold:
        return {
            "skill": skill_name,
            "previous_mean": prev.mean_overall,
            "current_mean": last.mean_overall,
            "delta": delta,
            "reports_compared": [prev.ts, last.ts],
        }
    return None


# ── CLI ──────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="sfos-eval",
        description="Claude-judge eval harness over record_example data.",
    )
    p.add_argument("--skill", default=None,
                    help="Eval one specific skill (default: all skills "
                         "with examples)")
    p.add_argument("--n", type=int, default=5,
                    help="Sample size per skill (default 5)")
    p.add_argument("--report", action="store_true",
                    help="Print the latest report for each skill instead "
                         "of running new evals.")
    p.add_argument("--trend", action="store_true",
                    help="Show mean score history (last 10 runs per skill).")
    args = p.parse_args(argv)

    if os.getenv("EVAL_SKIP") == "1":
        return 0

    skills = ([args.skill] if args.skill
              else list_skills_with_examples())
    if not skills:
        print("No skills with recorded examples yet. Agents need to call "
              "record_example() first.", file=sys.stderr)
        return 0

    if args.report or args.trend:
        for skill in skills:
            reports = load_recent_reports(skill)
            if not reports:
                print(f"  {skill}: no eval reports yet")
                continue
            if args.trend:
                line = " → ".join(f"{r.mean_overall:.1f}" for r in reports)
                print(f"  {skill}: {line}")
            else:
                last = reports[-1]
                print(f"  {skill}: mean {last.mean_overall:.2f} "
                      f"(n={last.n_examples}, p50={last.p50_overall}, "
                      f"p10={last.p10_overall}, ts={last.ts[:10]})")
        return 0

    print(f"Evaluating {len(skills)} skill(s) with sample size {args.n}",
          file=sys.stderr)
    for skill in skills:
        report = evaluate_skill(skill, n=args.n)
        if report is None:
            print(f"  {skill}: no examples or Claude unavailable",
                  file=sys.stderr)
            continue
        path = write_report(report)
        print(f"  ✓ {skill}: mean {report.mean_overall} "
              f"(n={report.n_examples}) → {path.name}",
              file=sys.stderr)
        # Drift detection
        drift = detect_drift(skill)
        if drift:
            print(f"    ⚠️  DRIFT: {drift['previous_mean']:.2f} → "
                  f"{drift['current_mean']:.2f} "
                  f"(Δ {drift['delta']:+.2f})",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
