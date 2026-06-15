"""SFOS row → Splunk HEC event translation.

Three streams, three sourcetypes:
    sfos:reflection — from ~/.<agent>/reflections.jsonl (Reflexion outcomes)
    sfos:eval       — from ~/.solo-founder-os/evals/<date>-<skill>.json
    sfos:bandit     — from marketing-agent history.db (Thompson arm pulls)

These are pure functions: row in, dict + sourcetype + epoch out. No I/O.
Keep it that way so the emit_loop can swap in a fake HECClient for tests
and we never accidentally couple the schema to the transport.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Iterable


def _parse_iso(ts: str) -> float:
    """Parse '2026-06-11T18:42:00+00:00' → epoch float. Returns now on fail."""
    if not ts:
        return datetime.now(timezone.utc).timestamp()
    try:
        # Python <3.11 doesn't accept trailing 'Z' in fromisoformat
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).timestamp()


def translate_reflection(row: dict, *, agent_dir: str) -> tuple[dict, str, float]:
    """Reflexion log row → HEC event.

    Input shape (from `reflection.log_outcome`):
      {ts, task, outcome, verbatim_signal, reflection}

    The `agent_dir` is the directory name like '.vc-outreach-agent' — we
    keep it on the event so Splunk panels can group by agent without
    having to parse the source field.
    """
    event = {
        "agent_dir": agent_dir,
        "task": row.get("task", ""),
        "outcome": row.get("outcome", "UNKNOWN"),
        "verbatim_signal": row.get("verbatim_signal", ""),
        "reflection_text": row.get("reflection", ""),
    }
    return event, "sfos:reflection", _parse_iso(row.get("ts", ""))


def translate_eval(row: dict) -> tuple[dict, str, float]:
    """Daily eval row → HEC event.

    Accepts two schemas:
      (legacy) {skill, ts, n_examples, mean, p50, p10}
      (current) {skill, ts, n_examples, scores: [{overall: float, ...}, ...]}
    """
    mean = row.get("mean")
    p50 = row.get("p50")
    p10 = row.get("p10")
    if mean is None and isinstance(row.get("scores"), list):
        overalls = sorted(
            float(s.get("overall", 0.0)) for s in row["scores"]
            if isinstance(s, dict) and s.get("overall") is not None
        )
        if overalls:
            mean = sum(overalls) / len(overalls)
            p50 = overalls[len(overalls) // 2]
            p10 = overalls[max(0, int(len(overalls) * 0.1) - 1)] if len(overalls) >= 10 else overalls[0]
    event = {
        "skill": row.get("skill", ""),
        "score": float(mean or 0.0),
        "n_samples": int(row.get("n_examples", 0)),
        "p50": float(p50 or 0.0),
        "p10": float(p10 or 0.0),
    }
    return event, "sfos:eval", _parse_iso(row.get("ts", ""))


def translate_bandit(row: dict) -> tuple[dict, str, float]:
    """Marketing-agent bandit pull row → HEC event.

    Input shape (from marketing-agent history.db `pulls` table):
      {ts, arm_slug, predicted_reward, actual_reward, regret}

    `regret` may be None on pulls where the actual reward hasn't been
    observed yet — emit it as 0 so the dashboard timechart doesn't gap.
    """
    event = {
        "arm_slug": row.get("arm_slug", ""),
        "predicted_reward": float(row.get("predicted_reward", 0.0)),
        "actual_reward": float(row.get("actual_reward") or 0.0),
        "regret": float(row.get("regret") or 0.0),
    }
    return event, "sfos:bandit", _parse_iso(row.get("ts", ""))


def translate_many(
    rows: Iterable[dict],
    *,
    kind: str,
    agent_dir: str = "",
) -> list[tuple[dict, str, float]]:
    """Vectorize the right per-row translator.

    `kind` is one of "reflection" | "eval" | "bandit". This is the only
    place that knows the mapping — callers stay shape-agnostic.
    """
    if kind == "reflection":
        return [translate_reflection(r, agent_dir=agent_dir) for r in rows]
    if kind == "eval":
        return [translate_eval(r) for r in rows]
    if kind == "bandit":
        return [translate_bandit(r) for r in rows]
    raise ValueError(f"unknown kind: {kind!r}")
