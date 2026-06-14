"""Export ~/.solo-founder-os/evals/*.json → public JSONL for HuggingFace.

Reads every eval JSON written by sfos-eval, flattens to one row per
(skill, ts, example_index), sanitizes any PII in the judge `notes`
field, and writes newline-delimited JSON to:

    ~/.solo-founder-os/public-eval.jsonl

Output row schema (HuggingFace-friendly, all flat scalars):

    skill           str   skill slug (draft-x, review-diff-pass, ...)
    ts              str   ISO 8601 UTC timestamp of the eval run
    eval_run_id     str   filename stem of source JSON (unique per run)
    example_index   int   index within the run's sampled examples
    clarity         int   1-5
    specificity     int   1-5
    voice           int   1-5
    accuracy        int   1-5
    completeness    int   1-5
    overall         float per-row 1-decimal average
    notes           str   1-2 judge sentences (PII-sanitized)
    run_mean        float run-level mean_overall
    run_p50         float run-level p50_overall
    run_p10         float run-level p10_overall
    run_n_examples  int   how many rows landed in this run
    judge_model     str   claude-sonnet-4-6 (constant for this dataset)
    agent_name      str   downstream agent that owns this skill (best-effort)
    schema_version  str   "v1" — flat row format

Drops `rubric` (constant across the dataset; surfaced in the
methodology MD instead).

PII heuristics (applied to the `notes` field only — all other fields
are integer/float/enum):

  - email regex
  - sk-ant- / sk_live_ / sk_test_ prefixes
  - 24+ char base64-shaped runs that look like tokens
  - explicit Alex / xji1 / xixiaichiyu name strings

If any pattern matches we mark the row `pii_flag = True` and replace
notes with "[redacted-PII]". A summary count is printed at the end so
Alex can decide whether to ship the sanitized version or hand-review.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any

EVALS_DIR = pathlib.Path.home() / ".solo-founder-os" / "evals"
OUTPUT_PATH = pathlib.Path.home() / ".solo-founder-os" / "public-eval.jsonl"

# Judge model is a constant in eval.py (anthropic_client.DEFAULT_SONNET_MODEL).
# Hard-coding here instead of importing so the script runs without the
# solo_founder_os package installed.
JUDGE_MODEL = "claude-sonnet-4-6"

# Best-effort skill → agent mapping. New skills default to "unknown".
# Source: ~/Desktop/Interview-Prep/Projects/alex-brain/index.md Active Agents.
SKILL_TO_AGENT = {
    "draft-x": "marketing-agent",
    "draft-customer-outreach": "vc-outreach-agent",
    "draft-vc-email": "vc-outreach-agent",
    "cluster-pain-points": "customer-discovery-agent",
    "summarize-morning-brief": "solo-founder-os",
    "translate-en-to-zh": "bilingual-content-sync-agent",
    "review-diff-pass": "build-quality-agent",
}

# ── PII heuristics ──────────────────────────────────────────────

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
API_KEY_PREFIXES = ("sk-ant-", "sk_live_", "sk_test_", "sk-proj-")
LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
PERSONAL_NAMES = ("alex ji", "alexji", "xji1", "xixiaichiyu", "alex jb")


def has_pii(text: str) -> bool:
    """Return True if obvious PII / secrets are detected in `text`."""
    if not text:
        return False
    lowered = text.lower()
    if EMAIL_RE.search(text):
        return True
    for prefix in API_KEY_PREFIXES:
        if prefix in text:
            return True
    if LONG_TOKEN_RE.search(text):
        return True
    for name in PERSONAL_NAMES:
        if name in lowered:
            return True
    return False


def load_eval_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  skip {path.name}: {exc}", file=sys.stderr)
        return None


def flatten(blob: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    """Flatten one eval JSON into per-example rows."""
    skill = blob.get("skill") or "unknown"
    ts = blob.get("ts") or ""
    run_n = int(blob.get("n_examples") or 0)
    run_mean = float(blob.get("mean_overall") or 0.0)
    run_p50 = float(blob.get("p50_overall") or 0.0)
    run_p10 = float(blob.get("p10_overall") or 0.0)
    agent = SKILL_TO_AGENT.get(skill, "unknown")

    rows: list[dict[str, Any]] = []
    for s in blob.get("scores") or []:
        notes = (s.get("notes") or "").strip()
        pii_flag = has_pii(notes)
        if pii_flag:
            notes = "[redacted-PII]"
        rows.append({
            "skill": skill,
            "ts": ts,
            "eval_run_id": run_id,
            "example_index": int(s.get("example_index") or 0),
            "clarity": int(s.get("clarity") or 0),
            "specificity": int(s.get("specificity") or 0),
            "voice": int(s.get("voice") or 0),
            "accuracy": int(s.get("accuracy") or 0),
            "completeness": int(s.get("completeness") or 0),
            "overall": float(s.get("overall") or 0.0),
            "notes": notes,
            "run_mean": run_mean,
            "run_p50": run_p50,
            "run_p10": run_p10,
            "run_n_examples": run_n,
            "judge_model": JUDGE_MODEL,
            "agent_name": agent,
            "schema_version": "v1",
            "pii_flag": pii_flag,
        })
    return rows


def main() -> int:
    if not EVALS_DIR.exists():
        print(f"No evals dir at {EVALS_DIR}", file=sys.stderr)
        return 1

    paths = sorted(EVALS_DIR.glob("*.json"))
    if not paths:
        print(f"No eval JSONs found in {EVALS_DIR}", file=sys.stderr)
        return 1

    all_rows: list[dict[str, Any]] = []
    pii_dropped = 0
    skills_seen: set[str] = set()

    for p in paths:
        blob = load_eval_json(p)
        if blob is None:
            continue
        run_id = p.stem
        for row in flatten(blob, run_id):
            if row["pii_flag"]:
                pii_dropped += 1
            all_rows.append(row)
            skills_seen.add(row["skill"])

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        for row in all_rows:
            fh.write(json.dumps(row, ensure_ascii=False))
            fh.write("\n")

    print(
        f"wrote {len(all_rows)} rows across {len(skills_seen)} skills, "
        f"{pii_dropped} flagged for PII (notes redacted, row preserved)",
        file=sys.stderr,
    )
    print(f"  source files: {len(paths)}", file=sys.stderr)
    print(f"  skills: {sorted(skills_seen)}", file=sys.stderr)
    print(f"  output:  {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
