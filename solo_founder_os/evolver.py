"""L4 evolver — PR-gated self-improvement (DGM-lite).

Once a week, scan reflection logs across all agents. When the same
failure pattern repeats N≥3 times for the same task type, ask Haiku to
propose a concrete code change to fix the root cause. The proposal
becomes a GitHub PR against the relevant agent repo. Alex reviews and
merges (or rejects).

Why this is safe (vs. true DGM):
  1. Evolver never auto-merges. The PR is the gate.
  2. Hard whitelist of what evolver can touch:
       - prompt strings / system prompt constants
       - JSON Schemas
       - error messages
       - test cases (asserting on the new failure)
       - markdown / docstrings
     NOT touched: auth code, network code, anything calling subprocess,
     anything writing money-relevant data, AnthropicClient internals.
  3. Modifications are scoped to ONE file per PR. No multi-file rewrites.
  4. Proposals come with the failure pattern as evidence + a draft test
     case. Reviewer can approve/reject in seconds.

Run pattern (weekly cron):
    sfos-evolver --dry-run             # print proposals to stdout
    sfos-evolver --gh                  # open PRs via gh CLI (default)
    sfos-evolver --output-dir <path>   # save patches as .diff files instead

Cost: 1 Haiku call per detected pattern. Bounded at 5 proposals/run.
~$0.001/proposal × 5 = $0.005/week.

Status: scaffolded with stable API + tests. The actual `gh pr create`
hookup is gated behind a flag that defaults to `--dry-run`. Production
opening of PRs requires the user to explicitly pass `--gh` AND have
`gh auth status` passing.
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .anthropic_client import AnthropicClient, DEFAULT_HAIKU_MODEL
from .eval import detect_drift, list_skills_with_examples, load_recent_reports


EVOLVER_USAGE_LOG = pathlib.Path.home() / ".solo-founder-os" / "usage.jsonl"

DEFAULT_AGENT_REPOS: list[tuple[str, str]] = [
    # (agent home dir slug, local repo path absolute or under ~/Desktop)
    (".build-quality-agent", "Desktop/build-quality-agent"),
    (".customer-discovery-agent", "Desktop/customer-discovery-agent"),
    (".funnel-analytics-agent", "Desktop/funnel-analytics-agent"),
    (".vc-outreach-agent", "Desktop/vc-outreach-agent"),
    (".cost-audit-agent", "Desktop/cost-audit-agent"),
    (".bilingual-content-sync-agent", "Desktop/bilingual-content-sync-agent"),
    (".orallexa-marketing-agent", "Desktop/orallexa-marketing-agent"),
    (".customer-support-agent", "Desktop/customer-support-agent"),
    (".customer-outreach-agent", "Desktop/customer-outreach-agent"),
    (".payments-agent", "Desktop/payments-agent"),
]


# Files that look like prompt / schema / error-message surfaces. Other
# files are out of scope for evolver. Conservative on purpose.
_SAFE_NAME_PATTERNS = [
    "drafter.py", "translator.py", "cluster.py", "summarizer.py",
    "review_prompt.py", "system_prompt.py",
    "prompts.py", "templates.py",
]
_SAFE_DIR_PATTERNS = ["prompts/", "templates/"]
# Hard-block list — even if a file looks safe by name, never propose a
# change to anything matching these.
_BLOCKED_PATTERNS = [
    "auth", "secret", "credential", "smtp", "stripe", "billing",
    "anthropic_client",  # core LLM plumbing — leave it alone
    "/.git/", "/migrations/", "/.env",
]


def is_safe_path(path: str) -> bool:
    """Return True iff evolver may propose a change to this file path."""
    p = path.lower()
    if any(b in p for b in _BLOCKED_PATTERNS):
        return False
    if any(p.endswith(name) for name in _SAFE_NAME_PATTERNS):
        return True
    if any(d in p for d in _SAFE_DIR_PATTERNS):
        return True
    return False


@dataclass
class FailurePattern:
    """A recurring (agent, task, signal-bucket) tuple that fired N≥3 times."""
    agent: str
    task: str
    signal_bucket: str  # short summary of the failure shape
    count: int
    sample_signals: list[str] = field(default_factory=list)


@dataclass
class Proposal:
    """One concrete change proposal Haiku has produced."""
    pattern: FailurePattern
    target_file: str  # relative path inside the agent repo
    rationale: str
    diff: str  # unified diff content (may be empty if Haiku declined)
    test_case: str = ""  # optional draft test


# ── Pattern detection ───────────────────────────────────────────


def _bucket_signal(signal: str) -> str:
    """Lossy hash of a failure signal — strip volatile parts (numbers,
    quoted strings) so 'rate limit 429 attempt 3' and 'rate limit 429
    attempt 7' bucket together."""
    s = signal.lower()
    s = re.sub(r"\b\d+\b", "N", s)
    s = re.sub(r'"[^"]*"', "_QUOTED_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:120]


def find_recurring_patterns(
    *,
    home: Optional[pathlib.Path] = None,
    min_count: int = 3,
    agent_dirs: Optional[list[str]] = None,
) -> list[FailurePattern]:
    """Scan all reflection logs, group by (agent, task, signal_bucket),
    return groups with ≥ min_count occurrences."""
    home = home or pathlib.Path.home()
    agent_dirs = agent_dirs or [a for a, _ in DEFAULT_AGENT_REPOS]
    counter: Counter = Counter()
    samples: dict[tuple[str, str, str], list[str]] = {}

    for agent in agent_dirs:
        rfile = home / agent / "reflections.jsonl"
        if not rfile.exists():
            continue
        try:
            lines = rfile.read_text(encoding="utf-8").splitlines()[-500:]
        except Exception:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except Exception:
                continue
            outcome = row.get("outcome")
            if outcome not in ("FAILED", "PARTIAL"):
                continue
            task = row.get("task", "?")
            signal = row.get("verbatim_signal", "")
            bucket = _bucket_signal(signal)
            if not bucket:
                continue
            key = (agent, task, bucket)
            counter[key] += 1
            samples.setdefault(key, []).append(signal)

    out: list[FailurePattern] = []
    for (agent, task, bucket), n in counter.items():
        if n < min_count:
            continue
        out.append(FailurePattern(
            agent=agent,
            task=task,
            signal_bucket=bucket,
            count=n,
            sample_signals=samples[(agent, task, bucket)][:5],
        ))
    out.sort(key=lambda p: -p.count)
    return out


def find_drift_patterns(
    *,
    evals_base: Optional[pathlib.Path] = None,
    drift_threshold: float = 0.5,
) -> list[FailurePattern]:
    """L6 → L4 bridge. Read every skill's recent eval reports, flag any
    whose mean score dropped > threshold across the last two runs, and
    return them as FailurePattern entries the existing synthesize_proposal
    loop can consume.

    Why count = max(3, ceil(delta * 10)):
      - Reuses the >=3 floor that find_recurring_patterns enforces, so
        a single drift signal is enough to make it past the default
        --min-count gate without inflating reflexion-bucket counts.
      - Larger drops sort higher in the merged proposal queue.
    """
    skills = list_skills_with_examples()
    out: list[FailurePattern] = []
    for skill in skills:
        drift = detect_drift(skill, base=evals_base, threshold=drift_threshold)
        if not drift:
            continue
        # Pull worst-scoring rows from the latest report as failure
        # samples — these are the rubric notes Sonnet wrote, which is
        # exactly the kind of free-text "verbatim_signal" the proposal
        # prompt expects.
        reports = load_recent_reports(skill, base=evals_base, n=1)
        sample_signals: list[str] = []
        if reports:
            scores = sorted(reports[-1].scores, key=lambda s: s.overall)[:3]
            sample_signals = [
                f"overall={s.overall} ({s.notes})".strip()
                for s in scores if s.notes or s.overall
            ]
        delta_abs = abs(drift["delta"])
        # Map drift magnitude into the same count axis the reflexion
        # path uses so merged sort-by-count stays meaningful.
        count = max(3, int(delta_abs * 10))
        out.append(FailurePattern(
            agent=".solo-founder-os",  # examples live in shared SFOS dir
            task=skill,
            signal_bucket=f"quality-drift mean {drift['previous_mean']:.2f} "
                            f"→ {drift['current_mean']:.2f} "
                            f"(delta {drift['delta']:+.2f})",
            count=count,
            sample_signals=sample_signals or [
                f"mean dropped {delta_abs:.2f} between "
                f"{drift['reports_compared'][0][:10]} and "
                f"{drift['reports_compared'][1][:10]}",
            ],
        ))
    out.sort(key=lambda p: -p.count)
    return out


# ── Proposal synthesis (Haiku) ──────────────────────────────────


PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "target_file": {
            "type": "string",
            "description": (
                "Relative path inside the agent repo (e.g. "
                "'vc_outreach_agent/drafter.py'). Must match one of the "
                "safe files (drafter.py, translator.py, prompts.py, etc) "
                "OR explicitly null if no safe file is appropriate."
            ),
        },
        "rationale": {"type": "string", "maxLength": 500},
        "diff": {
            "type": "string",
            "description": (
                "Unified diff content fixing the root cause. EMPTY string "
                "if a code-level fix isn't appropriate (e.g. needs human "
                "judgment / config change)."
            ),
        },
        "test_case": {
            "type": "string",
            "description": (
                "Optional pytest test that asserts the new behavior. "
                "Empty string if no test fits."
            ),
        },
    },
    "required": ["target_file", "rationale", "diff", "test_case"],
    "additionalProperties": False,
}


def find_council_synthesis_for_skill(
    skill: str, *, base: Optional[pathlib.Path] = None,
) -> Optional[str]:
    """Look for the most recent sfos-council meeting on this skill and
    return its synthesis section. Used by synthesize_proposal to inject
    multi-perspective deliberation into the evolver's Haiku prompt
    when L5 has already weighed in on a drift signal.

    Matches by frontmatter `topic: drift on <skill>` (the canonical
    output of council.convene_drift_council). Returns the body text
    under "## Synthesis" (or None if no matching meeting).
    """
    base = base or pathlib.Path.home() / ".solo-founder-os" / "council-meetings"
    if not base.exists():
        return None
    candidates = sorted(base.glob("*.md"), reverse=True)  # newest first
    needle = f"drift on {skill}"
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Match against frontmatter topic line.
        if not text.startswith("---\n"):
            continue
        end = text.find("\n---\n", 4)
        if end < 0:
            continue
        fm = text[4:end]
        if f"topic: {needle}" not in fm:
            continue
        # Extract the "## Synthesis" section (everything after the
        # heading up to EOF or the next top-level heading).
        marker = "## Synthesis"
        i = text.find(marker)
        if i < 0:
            return None
        synth_block = text[i + len(marker):].lstrip()
        # Stop at the next "## " heading if present.
        nxt = synth_block.find("\n## ")
        if nxt > 0:
            synth_block = synth_block[:nxt]
        return synth_block.strip() or None
    return None


def synthesize_proposal(
    pattern: FailurePattern,
    *,
    client: Optional[AnthropicClient] = None,
    council_synthesis: Optional[str] = None,
) -> Optional[Proposal]:
    """One Haiku call — given the pattern, propose a concrete fix.

    `council_synthesis` is optional L5 deliberation output. When passed,
    it's injected into the user prompt as additional context so the
    Haiku patch reflects multi-perspective analysis instead of a
    one-shot guess. Used by main() automatically for quality-drift
    patterns whose skill has a recent council meeting on file.

    Returns None if the proposal is unsafe (target_file blocked) or
    empty (Haiku declined). Never raises."""
    if client is None:
        client = AnthropicClient(usage_log_path=EVOLVER_USAGE_LOG)
    if not client.configured:
        return None

    council_block = ""
    if council_synthesis:
        council_block = (
            "\nL5 council deliberation already concluded. Use this as\n"
            "context when proposing — the perspectives below have been\n"
            "synthesized; your job is to translate the conclusion into\n"
            "a concrete code change.\n"
            "\n"
            "--- Council synthesis ---\n"
            f"{council_synthesis[:2000]}\n"
            "--- End council ---\n"
        )

    user = (
        "You're proposing a code-level improvement to a Python agent.\n"
        "\n"
        f"Agent: {pattern.agent}\n"
        f"Task: {pattern.task}\n"
        f"Failure signature (bucket): {pattern.signal_bucket}\n"
        f"Occurrences: {pattern.count}\n"
        "\n"
        "Sample failure signals:\n"
        + "\n".join(f"  - {s[:200]}" for s in pattern.sample_signals)
        + council_block
        + "\n\n"
        "Hard rules — VIOLATING ANY OF THESE means output empty diff:\n"
        "  1. Target file MUST be one of: drafter.py, translator.py,\n"
        "     cluster.py, summarizer.py, prompts.py, templates.py.\n"
        "     Or a file inside prompts/ or templates/.\n"
        "  2. NEVER touch auth, secrets, credentials, smtp, stripe, billing,\n"
        "     anthropic_client.py, migrations, .env, .git.\n"
        "  3. The change should be focused: a prompt rewording, a JSON\n"
        "     schema tightening, an error-message clarification, or a\n"
        "     small validation guard. NOT a refactor, NOT new features.\n"
        "  4. The change must be expressible as a unified diff against\n"
        "     ONE file.\n"
        "\n"
        "Output JSON conforming to the schema. If no safe code fix exists\n"
        "for this pattern (e.g. it's a config issue, env var missing, etc),\n"
        "return diff=\"\" with rationale explaining why."
    )

    obj, err = client.messages_create_json(
        schema=PROPOSAL_SCHEMA,
        model=DEFAULT_HAIKU_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": user}],
    )
    if err is not None or obj is None:
        return None

    target = (obj.get("target_file") or "").strip()
    diff = (obj.get("diff") or "").strip()
    rationale = (obj.get("rationale") or "").strip()
    test_case = (obj.get("test_case") or "").strip()

    if not diff:
        # Empty diff: Haiku declined. Still return proposal with rationale
        # so reviewer can see what was considered + why.
        return Proposal(pattern=pattern, target_file=target,
                          rationale=rationale, diff="", test_case=test_case)

    # Safety gate: check target file is whitelisted, not blocked
    if not target or not is_safe_path(target):
        return None

    return Proposal(pattern=pattern, target_file=target,
                      rationale=rationale, diff=diff, test_case=test_case)


# ── PR opening (gated by --gh) ──────────────────────────────────


def _gh_available() -> bool:
    """Return True iff `gh` CLI is on PATH AND authenticated."""
    try:
        r = subprocess.run(["gh", "auth", "status"],
                            capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def write_proposal_artifact(
    proposal: Proposal,
    *,
    out_dir: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    """Write a markdown artifact describing the proposal — used for
    --dry-run + as the body of a future `gh pr create`."""
    out_dir = out_dir or (pathlib.Path.home() / ".solo-founder-os"
                           / "evolver-proposals")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    slug = re.sub(r"[^a-z0-9]+", "-", proposal.pattern.task.lower()).strip("-")
    path = out_dir / f"{ts}-{proposal.pattern.agent.lstrip('.')}-{slug}.md"
    parts = [
        "---",
        f"agent: {proposal.pattern.agent}",
        f"task: {proposal.pattern.task}",
        f"target_file: {proposal.target_file}",
        f"occurrences: {proposal.pattern.count}",
        f"generated_at: {datetime.now(timezone.utc).isoformat()}",
        "---",
        "",
        f"# Evolver proposal: {proposal.pattern.task}",
        "",
        "## Pattern",
        f"- **Agent:** {proposal.pattern.agent}",
        f"- **Task:** {proposal.pattern.task}",
        f"- **Signal bucket:** `{proposal.pattern.signal_bucket}`",
        f"- **Occurrences:** {proposal.pattern.count}",
        "",
        "Sample signals:",
    ]
    for s in proposal.pattern.sample_signals:
        parts.append(f"- {s[:200]}")
    parts += [
        "",
        "## Rationale",
        proposal.rationale or "(no rationale provided)",
        "",
    ]
    if proposal.diff:
        parts += [
            "## Proposed diff",
            "",
            "```diff",
            proposal.diff,
            "```",
            "",
        ]
    else:
        parts.append("## Proposed diff\n\n_(none — pattern is not amenable to an automatic fix)_\n")
    if proposal.test_case:
        parts += [
            "## Proposed test",
            "",
            "```python",
            proposal.test_case,
            "```",
            "",
        ]
    parts.append("## How to apply\n")
    parts.append("```bash")
    parts.append(f"cd ~/{DEFAULT_AGENT_REPOS_BY_AGENT.get(proposal.pattern.agent, 'Desktop/<repo>')}")
    parts.append("git checkout -b evolver/$(date +%Y-%m-%d)-fix")
    parts.append("# apply diff manually or save to .diff file and `git apply`")
    parts.append("# review, run tests")
    parts.append("git push -u origin HEAD && gh pr create")
    parts.append("```")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


DEFAULT_AGENT_REPOS_BY_AGENT = {a: r for a, r in DEFAULT_AGENT_REPOS}


# ── CLI ─────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="sfos-evolver",
        description="Detect recurring failure patterns and propose code "
                     "fixes (PR-gated, never auto-merged).",
    )
    p.add_argument("--dry-run", action="store_true", default=True,
                    help="(default) Print proposals to stdout, write "
                         "markdown artifacts but never run `gh`.")
    p.add_argument("--gh", action="store_true",
                    help="Open PRs via gh CLI. Requires `gh auth status` "
                         "to pass. Implies NOT --dry-run.")
    p.add_argument("--min-count", type=int, default=3,
                    help="Minimum recurrences to consider a pattern "
                         "(default 3).")
    p.add_argument("--max-proposals", type=int, default=5,
                    help="Maximum proposals to generate per run "
                         "(default 5).")
    p.add_argument("--drift-threshold", type=float, default=0.5,
                    help="Eval mean-score drop that counts as drift "
                         "(default 0.5; pass 0 to disable L6 input).")
    args = p.parse_args(argv)

    if os.getenv("EVOLVER_SKIP") == "1":
        return 0

    patterns = find_recurring_patterns(min_count=args.min_count)
    drift_patterns: list[FailurePattern] = []
    if args.drift_threshold > 0:
        try:
            drift_patterns = find_drift_patterns(
                drift_threshold=args.drift_threshold,
            )
        except Exception as e:
            # Drift is supplementary; never block the reflexion path.
            print(f"  (drift scan skipped: {e})", file=sys.stderr)
    if drift_patterns:
        print(f"  + {len(drift_patterns)} L6 quality-drift signal(s)",
              file=sys.stderr)
        patterns = drift_patterns + patterns
    if not patterns:
        print("No recurring failure patterns found "
              f"(reflexion threshold ≥{args.min_count}, "
              f"drift threshold {args.drift_threshold}).",
              file=sys.stderr)
        return 0

    print(f"Found {len(patterns)} recurring pattern(s):", file=sys.stderr)
    for pat in patterns[:args.max_proposals]:
        print(f"  - [{pat.count}×] {pat.agent} / {pat.task} :: "
              f"{pat.signal_bucket}", file=sys.stderr)

    proposals: list[Proposal] = []
    n_council_injected = 0
    for pat in patterns[:args.max_proposals]:
        # L5→L4 wire: if this is a quality-drift pattern AND the L5
        # council has weighed in (sfos-council --auto-from-drift wrote
        # a meeting note for the skill), inject the synthesis into the
        # Haiku prompt. Reflexion-driven patterns get plain synthesis.
        council_synth: Optional[str] = None
        if "quality-drift" in pat.signal_bucket:
            council_synth = find_council_synthesis_for_skill(pat.task)
            if council_synth:
                n_council_injected += 1
        proposal = synthesize_proposal(pat, council_synthesis=council_synth)
        if proposal is not None:
            proposals.append(proposal)
    if n_council_injected:
        print(f"  + {n_council_injected} proposal(s) used L5 council "
              "synthesis as context", file=sys.stderr)

    if not proposals:
        print("No safe proposals could be synthesized.", file=sys.stderr)
        return 0

    out_dir = pathlib.Path.home() / ".solo-founder-os" / "evolver-proposals"
    paths = []
    for proposal in proposals:
        paths.append(write_proposal_artifact(proposal, out_dir=out_dir))

    print(f"\n✓ {len(proposals)} proposal(s) written to {out_dir}/",
          file=sys.stderr)
    for path in paths:
        print(f"  - {path.name}", file=sys.stderr)

    if args.gh:
        if not _gh_available():
            print("\n⚠️  --gh passed but gh CLI not authenticated. "
                  "Run `gh auth login` and retry. "
                  "Proposals are saved as artifacts for now.",
                  file=sys.stderr)
            return 0
        # PR opening hookup: deliberately not auto-running here in v1.
        # The proposal markdown is the unit of review. Future enhancement:
        # apply diff in a fresh branch + gh pr create with the artifact
        # as PR body.
        print("\n⚠️  --gh PR auto-creation not yet implemented. "
              "Review the artifacts and create PRs manually for now.",
              file=sys.stderr)
    else:
        print("\nDry run mode — no PRs opened. Pass --gh once you've "
              "reviewed an artifact and want auto-PR support (coming "
              "in a follow-up).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
