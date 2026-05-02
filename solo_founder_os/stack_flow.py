"""Stack flow timeline — cross-agent file-handoff events.

Research recommendation (rejected the chat-bubble metaphor): SFOS agents
communicate ASYNCHRONOUSLY via files (reflexion logs, eval reports,
evolver proposals, HITL queues, sfos-bus). Chat bubbles would lie about
the data shape. Vertical timeline of file events grouped by hour
captures the real topology.

Each event is one row:
    StackEvent(ts, source_agent, kind, artifact, summary, severity)

Sources combined:
  - reflexion writes (per-agent reflections.jsonl)
  - eval reports landing (~/.solo-founder-os/evals/*.json)
  - evolver proposals (~/.solo-founder-os/evolver-proposals/*.md)
  - HITL queue files (pending/approved/rejected/sent — file mtimes)
  - sfos-bus broadcasts (~/.solo-founder-os/bus/*.md)

This is genuinely cross-agent — the closest thing to "agent-to-agent
chat view" SFOS can show without misrepresenting its own architecture.
"""
from __future__ import annotations
import json
import pathlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .cross_agent_report import KNOWN_AGENT_DIRS


@dataclass
class StackEvent:
    """One row of the stack flow timeline."""
    ts: str
    agent: str
    kind: str            # "reflexion" | "eval" | "proposal" | "hitl" | "bus"
    summary: str
    severity: str = "info"  # info | warn | alert


def _safe_jsonl(path: pathlib.Path, limit: int = 300) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _gather_reflexions(
    home: pathlib.Path, since: datetime,
) -> list[StackEvent]:
    out: list[StackEvent] = []
    cutoff = since.isoformat()
    for slug in KNOWN_AGENT_DIRS:
        rows = _safe_jsonl(home / slug / "reflections.jsonl")
        for r in rows:
            ts = str(r.get("ts", ""))
            if not ts or ts < cutoff:
                continue
            outcome = str(r.get("outcome", "?"))
            sev = ({"FAILED": "alert", "PARTIAL": "warn"}
                       .get(outcome, "info"))
            out.append(StackEvent(
                ts=ts,
                agent=slug,
                kind="reflexion",
                summary=f"{outcome} `{r.get('task', '?')}` · "
                        f"{str(r.get('verbatim_signal', ''))[:120]}",
                severity=sev,
            ))
    return out


def _gather_evals(home: pathlib.Path, since: datetime) -> list[StackEvent]:
    out: list[StackEvent] = []
    cutoff = since.isoformat()
    base = home / ".solo-founder-os" / "evals"
    if not base.exists():
        return []
    for p in sorted(base.glob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ts = str(obj.get("ts", ""))
        if not ts or ts < cutoff:
            continue
        skill = obj.get("skill", "?")
        mean = obj.get("mean_overall", 0)
        n = obj.get("n_examples", 0)
        sev = "warn" if (mean and mean < 2.5) else "info"
        out.append(StackEvent(
            ts=ts,
            agent=".solo-founder-os",
            kind="eval",
            summary=f"judged `{skill}` — mean {mean:.2f}/5 (n={n})",
            severity=sev,
        ))
    return out


def _gather_proposals(
    home: pathlib.Path, since: datetime,
) -> list[StackEvent]:
    out: list[StackEvent] = []
    cutoff = since.isoformat()
    base = home / ".solo-founder-os" / "evolver-proposals"
    if not base.exists():
        return []
    for p in sorted(base.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        meta: dict = {}
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end > 0:
                for line in text[4:end].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
        ts = meta.get("generated_at", "")
        if not ts or ts < cutoff:
            continue
        agent = meta.get("agent", "?")
        task = meta.get("task", "?")
        target = meta.get("target_file", "?")
        out.append(StackEvent(
            ts=ts,
            agent=".solo-founder-os",
            kind="proposal",
            summary=f"evolver proposed change to `{target}` for "
                    f"`{agent}/{task}`",
            severity="warn",
        ))
    return out


def _gather_hitl(home: pathlib.Path, since: datetime) -> list[StackEvent]:
    """File mtime in queue/<status>/ becomes the event timestamp.
    For pending: 'X drafted by agent'. For approved: 'X approved'. Etc."""
    out: list[StackEvent] = []
    cutoff_dt = since
    for slug in KNOWN_AGENT_DIRS:
        agent_root = home / slug
        if not agent_root.exists():
            continue
        for status in ("pending", "approved", "rejected", "sent"):
            # Standard + nested layouts.
            dirs = list(agent_root.glob(f"queue/{status}"))
            dirs += list(agent_root.glob(f"queue/*/{status}"))
            for d in dirs:
                if not d.is_dir():
                    continue
                for f in d.glob("*.md"):
                    try:
                        mtime = datetime.fromtimestamp(
                            f.stat().st_mtime, tz=timezone.utc,
                        )
                    except OSError:
                        continue
                    if mtime < cutoff_dt:
                        continue
                    out.append(StackEvent(
                        ts=mtime.isoformat(),
                        agent=slug,
                        kind="hitl",
                        summary=f"{status}: {f.name}",
                        severity="info",
                    ))
    return out


def _gather_bus(home: pathlib.Path, since: datetime) -> list[StackEvent]:
    """sfos-bus markdown broadcasts."""
    out: list[StackEvent] = []
    cutoff = since.isoformat()
    base = home / ".solo-founder-os" / "bus"
    if not base.exists():
        return []
    for p in sorted(base.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        # Frontmatter parser — same shape as evolver proposals.
        meta: dict = {}
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end > 0:
                for line in text[4:end].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
        ts = meta.get("ts", "") or meta.get("posted_at", "")
        if not ts or ts < cutoff:
            continue
        sender = meta.get("from", meta.get("terminal", "?"))
        out.append(StackEvent(
            ts=ts,
            agent=".solo-founder-os",
            kind="bus",
            summary=f"bus from `{sender}`: {p.name}",
            severity="info",
        ))
    return out


def assemble_timeline(
    *,
    home: Optional[pathlib.Path] = None,
    since_hours: int = 168,
    limit: int = 200,
) -> list[StackEvent]:
    """Collect all event sources, sort newest-first, cap at `limit`."""
    home = home or pathlib.Path.home()
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    events: list[StackEvent] = []
    events.extend(_gather_reflexions(home, since))
    events.extend(_gather_evals(home, since))
    events.extend(_gather_proposals(home, since))
    events.extend(_gather_hitl(home, since))
    events.extend(_gather_bus(home, since))

    events.sort(key=lambda e: e.ts, reverse=True)
    return events[:limit]


def group_by_hour(events: list[StackEvent]) -> dict[str, list[StackEvent]]:
    """Group events by `YYYY-MM-DD HH:00` for the timeline render.
    Hours preserve the input ordering (newest hour first)."""
    out: dict[str, list[StackEvent]] = {}
    for e in events:
        # Truncate ts to the hour: '2026-05-02T16:39:...' → '2026-05-02 16:00'
        ts = e.ts
        key = ts[:13].replace("T", " ") + ":00" if len(ts) >= 13 else ts
        out.setdefault(key, []).append(e)
    return out
