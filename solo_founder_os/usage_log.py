"""Anthropic usage / cost log — JSONL append-only with $ aggregation.

Why per-agent logs and not a single global log: each agent might run on a
different schedule or machine, but we want each agent's monthly bill
visible standalone. cost-audit-agent aggregates across all agent logs.

Each row: {ts, model, input_tokens, output_tokens, verdict, bytes}.
`verdict` and `bytes` are caller-defined (build-quality-agent uses them
for PASS/BLOCK + diff size; funnel-analytics doesn't set them). Extra
fields are allowed — readers should ignore unknown keys.
"""
from __future__ import annotations
import json
import os
import pathlib
from datetime import datetime, timezone


# Anthropic prices in $/MTok (input, output). As of 2026-04.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":  (1.0,  5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7":   (15.0, 75.0),
}


def log_usage(
    *,
    log_path: pathlib.Path,
    model: str,
    input_tokens: int,
    output_tokens: int,
    extra: dict | None = None,
    now: datetime | None = None,
) -> None:
    """Append one row. Best-effort — silently swallows I/O errors so a
    failed log never breaks the calling agent."""
    now = now or datetime.now(timezone.utc)
    row = {
        "ts": now.isoformat(),
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
    }
    if extra:
        row.update(extra)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def usage_report(log_path: pathlib.Path) -> str:
    """Aggregate the log into a human-readable summary."""
    if not log_path.exists():
        return "No usage logged yet."

    total = {"runs": 0, "in": 0, "out": 0, "cost": 0.0}
    by_model: dict[str, dict] = {}

    try:
        for line in log_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            total["runs"] += 1
            in_tok = row.get("input_tokens", 0)
            out_tok = row.get("output_tokens", 0)
            model = row.get("model", "unknown")
            in_p, out_p = PRICES.get(model, (1.0, 5.0))
            cost = (in_tok * in_p + out_tok * out_p) / 1_000_000
            total["in"] += in_tok
            total["out"] += out_tok
            total["cost"] += cost
            m = by_model.setdefault(model, {"runs": 0, "in": 0, "out": 0, "cost": 0.0})
            m["runs"] += 1
            m["in"] += in_tok
            m["out"] += out_tok
            m["cost"] += cost
    except Exception as e:
        return f"Could not read usage log: {e}"

    lines = [f"usage report — {log_path}",
             f"  {total['runs']} runs · {total['in']:,} in / {total['out']:,} out tokens",
             f"  ~${total['cost']:.4f} total"]
    for model, m in by_model.items():
        lines.append(f"  {model}: {m['runs']} runs · "
                     f"{m['in']:,} in / {m['out']:,} out · ~${m['cost']:.4f}")
    return "\n".join(lines)
