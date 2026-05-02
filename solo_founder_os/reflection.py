"""Reflexion-style outcome logging — agents learn from their own failures.

Pattern (Shinn et al. 2023, "Reflexion"): every time an agent observes a
clear outcome signal for a task it just performed (failure / partial /
success), record it. On failures, generate a 1-line reflection via Haiku
summarizing what to do differently. Subsequent calls to that same task
type prepend the last N reflections to the system prompt.

Net effect: each agent's prompt gets sharper over weeks of real use without
any code change. New agents inherit the upgrade automatically because it
hooks through AnthropicClient.

Storage: ~/.<agent>/reflections.jsonl (one JSONL row per outcome, append-only).

Cost: ~$0.0008/reflection (1 Haiku call when outcome=FAILED|PARTIAL).
Skipped on outcome=OK so successes don't burn money for no signal.

Hard rule: reflections never exceed 280 chars and never reference the user
by name. Treat the file like a public log; don't write secrets.
"""
from __future__ import annotations
import json
import os
import pathlib
from datetime import datetime, timezone
from typing import Optional

from .anthropic_client import AnthropicClient, DEFAULT_HAIKU_MODEL


REFLECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reflection": {
            "type": "string",
            "description": (
                "One sentence (≤280 chars) describing what the agent should "
                "do differently next time on this task type. No proper nouns. "
                "No reference to specific user names or emails."
            ),
        },
    },
    "required": ["reflection"],
    "additionalProperties": False,
}


def _reflections_path(agent_dir: str) -> pathlib.Path:
    """`agent_dir` is e.g. '.vc-outreach-agent' (relative to ~)."""
    return pathlib.Path.home() / agent_dir / "reflections.jsonl"


def log_outcome(
    agent_dir: str,
    task: str,
    outcome: str,
    signal: str,
    *,
    client: Optional[AnthropicClient] = None,
    skip_reflection: bool = False,
) -> dict:
    """Record an outcome and (for FAILED/PARTIAL) generate a reflection.

    Args:
        agent_dir: e.g. ".vc-outreach-agent" — under user home
        task: short stable identifier for the task type ("draft_email",
              "scrape_subreddit", "translate_batch", etc.)
        outcome: "OK" | "PARTIAL" | "FAILED"
        signal: 1-line verbatim signal that triggered this log
                (e.g. "user moved file to rejected/", "exit code 2",
                 "BLOCK verdict from build-quality-agent").
        client: injectable for tests.
        skip_reflection: if True, log the row but don't burn a Haiku call.

    Returns the entry dict (for tests + introspection). Never raises.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task": str(task)[:80],
        "outcome": outcome,
        "verbatim_signal": str(signal)[:500],
        "reflection": "",
    }

    # Test-pollution guard: agents' test suites that don't isolate
    # pathlib.Path.home() were silently appending fixture rows into the
    # real ~/.<agent>/reflections.jsonl. Those rows then drove L4
    # evolver to propose fixes for non-bugs. Setting SFOS_LOG_OUTCOME_SKIP=1
    # in pytest config (via conftest or pyproject) opts the whole suite
    # out without per-test monkeypatching.
    if os.getenv("SFOS_LOG_OUTCOME_SKIP") == "1":
        return entry

    needs_reflection = (outcome in ("FAILED", "PARTIAL")
                         and not skip_reflection)

    if needs_reflection:
        if client is None:
            client = AnthropicClient(
                usage_log_path=_reflections_path(agent_dir).parent
                / "usage.jsonl",
            )
        if client.configured:
            obj, err = client.messages_create_json(
                schema=REFLECTION_SCHEMA,
                model=DEFAULT_HAIKU_MODEL,
                max_tokens=160,
                messages=[{
                    "role": "user",
                    "content": (
                        f"An agent just attempted task `{task}` and the outcome "
                        f"was '{outcome}'. The signal that revealed this was: "
                        f"{signal}\n\n"
                        "In ONE sentence (≤280 chars), what should the agent "
                        "do differently next time on this task type? Be "
                        "specific. No proper nouns. No mention of specific "
                        "users or emails."
                    ),
                }],
            )
            if obj is not None and err is None:
                entry["reflection"] = (obj.get("reflection") or "")[:280]

    path = _reflections_path(agent_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Best-effort. Logging must never break the agent's main loop.
        pass

    return entry


def recent_reflections(
    agent_dir: str,
    task: str,
    *,
    n: int = 5,
    scan_last: int = 200,
) -> list[str]:
    """Return the last N non-empty reflections for this task type.

    Reads only the tail of the file (`scan_last` rows) so the per-call
    cost stays bounded even after months of accumulation.
    """
    path = _reflections_path(agent_dir)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[str] = []
    for line in lines[-scan_last:]:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("task") != task:
            continue
        ref = (row.get("reflection") or "").strip()
        if ref:
            out.append(ref)
    return out[-n:]


def reflections_preamble(
    agent_dir: str,
    task: str,
    *,
    n: int = 5,
) -> str:
    """Render reflections as a system-prompt preamble.

    Returns "" if there are no usable reflections, so callers can do:
        system = reflections_preamble(...) + base_system_prompt
    without conditional logic.
    """
    refs = recent_reflections(agent_dir, task, n=n)
    if not refs:
        return ""
    bullets = "\n".join(f"- {r}" for r in refs)
    return (
        "Past reflections on this task type (apply these lessons):\n"
        f"{bullets}\n\n"
    )
