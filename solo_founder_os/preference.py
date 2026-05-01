"""ICPL — In-Context Preference Learning from human edits.

Lifted from marketing-agent (which proved this in production) into the
shared library so any agent can learn from "human took my draft and
edited it before sending."

Per Q1 2026 research: ICPL is the cheapest path to "learn from edits"
at sub-500-pair regimes. No LoRA, no DPO, no training. Just:

  1. Log every (original, edited, optional context) tuple
  2. At generation time, inject the most recent N pairs as few-shot
     exemplars into the LLM prompt

Migration path: at ~500+ pairs, swap to DPO via Together.ai (~$3/1M
tokens). Until then, ICPL captures most of the value at zero training cost.

Storage: ~/.<agent>/preference-pairs.jsonl (one row per pair, append-only).

Agents call:
  log_edit(agent_dir=".vc-outreach-agent",
           task="draft_email",
           original="<draft>",
           edited="<final>",
           context={"investor": "Alice"})

Then at generation time:
  preamble = preference_preamble(".vc-outreach-agent", "draft_email")
  client.messages_create(system=base_prompt + preamble, ...)

Cost: $0 — pure JSONL append. Logs grow slowly (one per HITL
approval), so the tail-of-file scan stays cheap forever.

Privacy note: edit pairs may contain investor names, customer info, etc.
The file lives under ~/.<agent>/, never sent anywhere unless the user
opts in. Treat it like a private notebook.
"""
from __future__ import annotations
import json
import pathlib
from datetime import datetime, timezone
from typing import Optional


def _path(agent_dir: str) -> pathlib.Path:
    """`agent_dir` is like '.vc-outreach-agent' (relative to ~)."""
    return pathlib.Path.home() / agent_dir / "preference-pairs.jsonl"


def log_edit(
    agent_dir: str,
    task: str,
    original: str,
    edited: str,
    *,
    context: Optional[dict] = None,
    note: str = "",
) -> dict:
    """Append a (task, original, edited, context, note) tuple to the
    agent's preference log. Best-effort — never raises.

    `task` is the same identifier used by reflection.log_outcome() and
    skills.record_example() so all three logs join cleanly.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task": str(task)[:80],
        "original": str(original)[:5000],
        "edited": str(edited)[:5000],
        "context": context or {},
        "note": str(note)[:300],
    }
    path = _path(agent_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return entry


def recent_edits(
    agent_dir: str,
    task: str,
    *,
    n: int = 5,
    scan_last: int = 200,
) -> list[dict]:
    """Return the last N preference pairs for this task type. Used by
    preference_preamble. Tail-of-file scan keeps cost bounded."""
    path = _path(agent_dir)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict] = []
    for line in lines[-scan_last:]:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("task") != task:
            continue
        if row.get("original") and row.get("edited"):
            out.append(row)
    return out[-n:]


def preference_preamble(
    agent_dir: str,
    task: str,
    *,
    n: int = 3,
    truncate_each: int = 600,
) -> str:
    """Render recent edit pairs as a few-shot exemplar block for the LLM.

    Returns "" if no pairs exist, so callers can do:
        system = base + preference_preamble(...)
    without conditional logic.

    `truncate_each` caps each exemplar to keep the preamble bounded.
    With n=3 and truncate_each=600, the preamble stays under ~4k chars.
    """
    pairs = recent_edits(agent_dir, task, n=n)
    if not pairs:
        return ""
    parts = [
        "Past human edits to drafts of this task type. Treat each pair "
        "as a strong preference signal — emulate the 'edited' style and "
        "avoid the 'original' style:",
        "",
    ]
    for pair in pairs:
        original = pair.get("original", "")[:truncate_each]
        edited = pair.get("edited", "")[:truncate_each]
        parts.append("--- exemplar ---")
        parts.append("[ORIGINAL DRAFT]")
        parts.append(original)
        parts.append("")
        parts.append("[HUMAN-EDITED FINAL]")
        parts.append(edited)
        parts.append("")
    parts.append("--- end exemplars ---")
    parts.append("")
    return "\n".join(parts)
