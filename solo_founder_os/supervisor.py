"""L2 supervisor — auto-find work across the agent stack.

Pattern (Monitor Agent → Trigger → Spawn): every day, the supervisor
reads the live state of every agent (usage logs, reflection logs, HITL
queue depth) and asks Claude "what's the highest-leverage task Alex
should consider right now?" Up to 3 proposed tasks land in
~/.solo-founder-os/proposed-tasks/pending/<slug>.md as markdown HITL
items. Alex reviews, deletes the bad ones, runs the good ones.

This is not goal-seeking AI. It's bottleneck detection over a fixed
agent surface, gated by the human. The supervisor's value is
*prioritization* — picking which 3 of 50 possible tasks to surface — not
generality.

Cost: 1 Haiku call/day ≈ $0.001/day. Effectively free.

Schedule via launchd or cron:
    sfos-supervisor                 # writes proposals, exits
    sfos-supervisor --dry-run       # prints to stdout, doesn't write

Output format:
    ~/.solo-founder-os/proposed-tasks/pending/<YYYY-MM-DD-slug>.md
    YAML frontmatter (title / agent / priority / reasoning / command) +
    body explaining why now.
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .anthropic_client import AnthropicClient, DEFAULT_HAIKU_MODEL


# Default agent home directories (relative to user $HOME) that the
# supervisor inspects. Add new agents here when they ship.
DEFAULT_AGENT_DIRS = [
    ".build-quality-agent",
    ".customer-discovery-agent",
    ".funnel-analytics-agent",
    ".vc-outreach-agent",
    ".cost-audit-agent",
    ".bilingual-content-sync-agent",
    ".orallexa-marketing-agent",
]

PROPOSALS_DIR = (pathlib.Path.home() / ".solo-founder-os"
                 / "proposed-tasks" / "pending")

USAGE_LOG_PATH = (pathlib.Path.home() / ".solo-founder-os" / "usage.jsonl")


@dataclass
class AgentState:
    name: str
    home_dir: str
    usage_calls_24h: int = 0
    hitl_pending_count: int = 0
    recent_reflections: list[str] = field(default_factory=list)
    last_run_iso: Optional[str] = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Task:
    title: str
    agent: str
    reasoning: str
    command: str
    priority: str  # urgent | high | med | low


PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "agent": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "command": {"type": "string"},
                    "priority": {
                        "enum": ["urgent", "high", "med", "low"],
                    },
                },
                "required": ["title", "agent", "reasoning",
                              "command", "priority"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["tasks"],
    "additionalProperties": False,
}


def _read_usage_calls_last_24h(usage_log: pathlib.Path) -> int:
    """Count rows in usage.jsonl with ts within the last 24h. Cheap;
    bounded scan of last 2000 lines."""
    if not usage_log.exists():
        return 0
    try:
        lines = usage_log.read_text(encoding="utf-8").splitlines()[-2000:]
    except Exception:
        return 0
    cutoff = datetime.now(timezone.utc).timestamp() - 86_400
    count = 0
    for line in lines:
        try:
            row = json.loads(line)
            ts = row.get("ts", "")
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            if t >= cutoff:
                count += 1
        except Exception:
            continue
    return count


def _count_hitl_pending(home: pathlib.Path) -> int:
    """Count *.md files in any pending/ subdirectory under home."""
    if not home.exists():
        return 0
    total = 0
    try:
        for p in home.rglob("pending/*.md"):
            if p.is_file():
                total += 1
    except Exception:
        pass
    return total


def _last_run_iso(usage_log: pathlib.Path) -> Optional[str]:
    """ISO ts of the most recent usage entry, or None."""
    if not usage_log.exists():
        return None
    try:
        lines = usage_log.read_text(encoding="utf-8").splitlines()
        if not lines:
            return None
        row = json.loads(lines[-1])
        return row.get("ts")
    except Exception:
        return None


def _read_recent_reflections(home: pathlib.Path, n: int = 10) -> list[str]:
    """Pull the last N non-empty reflections across all task types."""
    rfile = home / "reflections.jsonl"
    if not rfile.exists():
        return []
    try:
        lines = rfile.read_text(encoding="utf-8").splitlines()[-200:]
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        ref = (row.get("reflection") or "").strip()
        if ref:
            task = row.get("task", "?")
            out.append(f"[{task}] {ref}")
    return out[-n:]


def gather_state(
    *,
    agent_dirs: Optional[list[str]] = None,
    home: Optional[pathlib.Path] = None,
) -> dict:
    """Read live state from every agent. Pure I/O, no Claude call.

    Each agent contributes:
      - usage_calls_24h: how active it is
      - hitl_pending_count: how much queued work the human owes
      - recent_reflections: what's been failing lately
      - last_run_iso: when did this agent last actually run

    Plus stack-wide notes (e.g. notifier configured?).
    """
    agent_dirs = agent_dirs or DEFAULT_AGENT_DIRS
    home = home or pathlib.Path.home()
    state: dict = {
        "now": datetime.now(timezone.utc).isoformat(),
        "agents": [],
        "stack_notes": [],
    }
    for d in agent_dirs:
        adir = home / d
        usage = adir / "usage.jsonl"
        s = AgentState(
            name=d.lstrip("."),
            home_dir=d,
            usage_calls_24h=_read_usage_calls_last_24h(usage),
            hitl_pending_count=_count_hitl_pending(adir),
            recent_reflections=_read_recent_reflections(adir),
            last_run_iso=_last_run_iso(usage),
        )
        state["agents"].append(asdict(s))

    # Stack-wide signals
    if not (os.getenv("NTFY_TOPIC") or os.getenv("TELEGRAM_BOT_TOKEN")
             or os.getenv("SLACK_WEBHOOK_URL")):
        state["stack_notes"].append(
            "no notifier configured — alerts have nowhere to go")
    if not os.getenv("ANTHROPIC_API_KEY"):
        state["stack_notes"].append(
            "ANTHROPIC_API_KEY unset — most agents are degraded")

    return state


def _build_prompt(state: dict, max_tasks: int) -> str:
    """Build the user prompt for Claude given the gathered state."""
    lines = [
        "You are the supervisor of a Solo Founder OS agent stack — 8 OSS",
        "Python agents that automate parts of solo founder ops:",
        "  - build-quality-agent: pre-push Claude diff review",
        "  - customer-discovery-agent: Reddit pain scraper + clustering",
        "  - funnel-analytics-agent: daily brief + real-time alerts",
        "  - vc-outreach-agent: VC cold-email drafter (HITL queue)",
        "  - cost-audit-agent: monthly bill audit",
        "  - bilingual-content-sync-agent: EN/ZH i18n translator",
        "  - orallexa-marketing-agent: multi-platform post drafter",
        "",
        f"Current state (as of {state['now']}):",
        "",
    ]
    for a in state["agents"]:
        lines.append(f"## {a['name']}")
        lines.append(f"  - usage calls last 24h: {a['usage_calls_24h']}")
        lines.append(f"  - HITL pending count: {a['hitl_pending_count']}")
        lines.append(f"  - last run: {a['last_run_iso'] or '(never)'}")
        if a["recent_reflections"]:
            lines.append(f"  - recent reflections (last {len(a['recent_reflections'])}):")
            for r in a["recent_reflections"]:
                lines.append(f"      - {r}")
        else:
            lines.append("  - reflections: (none yet)")
        lines.append("")

    if state.get("stack_notes"):
        lines.append("## stack-wide notes")
        for n in state["stack_notes"]:
            lines.append(f"  - {n}")
        lines.append("")

    lines += [
        f"Propose up to {max_tasks} tasks Alex should consider. Each task:",
        "  - title: short imperative (≤8 words)",
        "  - agent: which agent's home dir, e.g. '.vc-outreach-agent', or 'manual' if Alex must do it by hand",
        "  - reasoning: 2-3 sentences — why now, what's the bottleneck",
        "  - command: exact shell command (or 'manual' if no automation exists)",
        "  - priority: urgent | high | med | low",
        "",
        "Lean toward unblocking specific bottlenecks visible in the state.",
        "Skip generic advice. Skip anything that's obvious like 'run sfos-doctor'.",
        "If everything looks healthy and routine, return tasks: [].",
        "Output JSON conforming to the schema.",
    ]
    return "\n".join(lines)


def propose_tasks(
    state: dict,
    *,
    client: Optional[AnthropicClient] = None,
    max_tasks: int = 3,
) -> list[Task]:
    """One Haiku call → up to N task proposals. Empty list on any failure.

    Inject `client` for tests. In production leave it None and the
    function constructs an AnthropicClient pointed at supervisor's
    own usage log.
    """
    if client is None:
        client = AnthropicClient(usage_log_path=USAGE_LOG_PATH)
    if not client.configured:
        return []

    obj, err = client.messages_create_json(
        schema=PROPOSAL_SCHEMA,
        model=DEFAULT_HAIKU_MODEL,
        max_tokens=900,
        messages=[{
            "role": "user",
            "content": _build_prompt(state, max_tasks),
        }],
    )
    if err is not None or obj is None:
        return []

    out: list[Task] = []
    for raw in (obj.get("tasks") or [])[:max_tasks]:
        try:
            out.append(Task(
                title=str(raw["title"])[:120],
                agent=str(raw["agent"])[:80],
                reasoning=str(raw["reasoning"])[:1000],
                command=str(raw["command"])[:600],
                priority=str(raw["priority"]).lower(),
            ))
        except (KeyError, TypeError):
            continue
    return out


def _slug(title: str) -> str:
    """Kebab-case slug from a title, capped at 60 chars."""
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:60] or "task"


def _render_task_md(task: Task, *, ts: Optional[datetime] = None) -> str:
    """Render a Task as markdown for the HITL queue."""
    ts = ts or datetime.now(timezone.utc)
    return (
        "---\n"
        f"title: {task.title}\n"
        f"agent: {task.agent}\n"
        f"priority: {task.priority}\n"
        f"proposed_at: {ts.isoformat()}\n"
        "---\n\n"
        f"# {task.title}\n\n"
        f"**Why now:** {task.reasoning}\n\n"
        "## Run\n\n"
        f"```bash\n{task.command}\n```\n"
    )


def write_proposals(
    tasks: list[Task],
    *,
    out_dir: Optional[pathlib.Path] = None,
) -> list[pathlib.Path]:
    """Write each task to ~/.solo-founder-os/proposed-tasks/pending/<slug>.md.
    Returns the list of paths created (or would be created)."""
    out_dir = out_dir or PROPOSALS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    paths: list[pathlib.Path] = []
    for task in tasks:
        path = out_dir / f"{today}-{_slug(task.title)}.md"
        # If the same slug already exists, append a numeric suffix
        if path.exists():
            for i in range(2, 100):
                candidate = out_dir / f"{today}-{_slug(task.title)}-{i}.md"
                if not candidate.exists():
                    path = candidate
                    break
        path.write_text(_render_task_md(task), encoding="utf-8")
        paths.append(path)
    return paths


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="sfos-supervisor",
        description="Read agent stack state and propose top-N tasks "
                     "to the HITL queue.",
    )
    p.add_argument("--dry-run", action="store_true",
                    help="Print proposals to stdout, don't write to queue.")
    p.add_argument("--max-tasks", type=int, default=3,
                    help="Maximum number of tasks to propose (default 3).")
    p.add_argument("--quiet", action="store_true",
                    help="Suppress info output; errors still print.")
    args = p.parse_args(argv)

    if os.getenv("SUPERVISOR_SKIP") == "1":
        return 0

    state = gather_state()
    if not args.quiet:
        n_agents = len(state["agents"])
        print(f"# sfos-supervisor — {n_agents} agents inspected",
              file=sys.stderr)

    tasks = propose_tasks(state, max_tasks=args.max_tasks)

    if not tasks:
        if not args.quiet:
            print("No proposals — stack looks healthy or supervisor "
                  "couldn't reach Claude.", file=sys.stderr)
        return 0

    if args.dry_run:
        for t in tasks:
            print(_render_task_md(t))
            print("---DRY-RUN-DIVIDER---")
        return 0

    paths = write_proposals(tasks)
    if not args.quiet:
        print(f"✓ {len(paths)} task(s) written to "
              f"~/.solo-founder-os/proposed-tasks/pending/", file=sys.stderr)
        for path in paths:
            print(f"  - {path.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
