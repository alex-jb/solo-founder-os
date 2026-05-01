"""Centralized governance rail — one inbox for all HITL queues.

Right now each agent has its own queue/pending/ directory. With 7 agents
that's 7 places to check every morning. Per the 2026 governance research,
the governance rail should be ONE policy layer that:

  1. Surfaces a single inbox of all pending HITL items across all agents
  2. Records every approval/rejection decision in an audit log
  3. Lets the human filter by agent / priority / age

This module is the read-only consolidator. It does NOT move files (each
agent's queue/ stays canonical) — it just provides a unified view via
`scan_inbox()` and a CLI `sfos-inbox` to make morning review fast.

Storage:
  - Reads: ~/.<agent>/queue/pending/*.md across all 7 agents
  - Records decisions: ~/.solo-founder-os/governance/decisions.jsonl
    (append-only audit log: when, agent, item, action, who/terminal)

CLI:
    sfos-inbox                          # show all pending across all agents
    sfos-inbox --agent vc-outreach      # filter to one
    sfos-inbox --since 24h              # only items added in last 24h
    sfos-inbox --json                   # for piping to other tools
    sfos-inbox approve <id>             # log + move file to queue/approved/
    sfos-inbox reject <id>              # log + move file to queue/rejected/

`<id>` is a short stable hash of agent+filename so collisions are unlikely.

Why not a database / web UI: the filesystem IS the database. Files in
queue/pending/ are the source of truth. This module is a view + an audit
log over them. Stays compatible with each agent's existing queue code.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


GOVERNANCE_DIR = pathlib.Path.home() / ".solo-founder-os" / "governance"
DECISIONS_LOG = GOVERNANCE_DIR / "decisions.jsonl"

# Default agents to scan. Each agent's queue lives at
# ~/.<agent>/queue/pending/. Add new agents here.
DEFAULT_AGENT_DIRS = [
    ".vc-outreach-agent",
    ".bilingual-content-sync-agent",
    ".orallexa-marketing-agent",
    ".customer-discovery-agent",
    ".build-quality-agent",
    ".funnel-analytics-agent",
    ".cost-audit-agent",
]


@dataclass
class InboxItem:
    """One pending HITL item, regardless of which agent owns it."""
    id: str            # short stable hash for CLI references
    agent: str         # e.g. ".vc-outreach-agent"
    filename: str      # e.g. "alice-sequoia-2026-05-02.md"
    path: pathlib.Path
    title: str = ""    # parsed from frontmatter or first H1
    priority: str = "med"  # urgent | high | med | low
    created_at: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)
    body_preview: str = ""  # first 200 chars of body


def _short_id(agent: str, filename: str) -> str:
    """Stable 8-char hash for CLI references. Order-independent — the same
    (agent, filename) always produces the same id."""
    h = hashlib.blake2b(f"{agent}/{filename}".encode(), digest_size=4)
    return h.hexdigest()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Tiny YAML frontmatter parser (same shape as solo_founder_os.skills)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm = text[4:end]
    body = text[end + 5:]
    meta: dict = {}
    for line in fm.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            meta[k] = [s.strip() for s in inner.split(",") if s.strip()] \
                       if inner else []
        else:
            meta[k] = v
    return meta, body


def _parse_inbox_item(agent: str, path: pathlib.Path) -> Optional[InboxItem]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    meta, body = _parse_frontmatter(text)
    body = body.strip()
    title = meta.get("title", "")
    if not title:
        # Try first H1 from body
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("# "):
                title = line[2:].strip()
                break
    if not title:
        title = path.stem  # fallback to filename
    priority = (meta.get("priority") or "med").lower()
    tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
    created_at: Optional[datetime] = None
    for k in ("proposed_at", "drafted_at", "created_at", "ts"):
        if k in meta:
            try:
                created_at = datetime.fromisoformat(
                    str(meta[k]).replace("Z", "+00:00"))
                break
            except Exception:
                continue
    if created_at is None:
        try:
            created_at = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc)
        except Exception:
            created_at = datetime.now(timezone.utc)
    preview = body.replace("\n", " ").strip()[:200]
    return InboxItem(
        id=_short_id(agent, path.name),
        agent=agent,
        filename=path.name,
        path=path,
        title=title,
        priority=priority,
        created_at=created_at,
        tags=tags,
        body_preview=preview,
    )


# ── scan_inbox ──────────────────────────────────────────────


def scan_inbox(
    *,
    home: Optional[pathlib.Path] = None,
    agent_dirs: Optional[list[str]] = None,
    since: Optional[datetime] = None,
    agent: Optional[str] = None,
) -> list[InboxItem]:
    """Read all pending HITL items across all agents. Returns flat list
    sorted by priority (urgent → low) then created_at (oldest first)."""
    home = home or pathlib.Path.home()
    agent_dirs = agent_dirs or DEFAULT_AGENT_DIRS

    # Also include sfos-supervisor proposed-tasks/pending/
    extras: list[tuple[str, pathlib.Path]] = []
    sup_pending = (home / ".solo-founder-os" / "proposed-tasks" / "pending")
    if sup_pending.exists():
        for p in sup_pending.glob("*.md"):
            extras.append((".solo-founder-os/supervisor", p))

    items: list[InboxItem] = []
    for d in agent_dirs:
        if agent and d.lstrip(".") != agent.lstrip("."):
            continue
        pending_dir = home / d / "queue" / "pending"
        if not pending_dir.exists():
            continue
        for path in pending_dir.glob("*.md"):
            item = _parse_inbox_item(d, path)
            if item is None:
                continue
            if since and item.created_at and item.created_at < since:
                continue
            items.append(item)

    if not agent or agent.lstrip(".") in ("solo-founder-os/supervisor",
                                            "supervisor"):
        for slug, p in extras:
            item = _parse_inbox_item(slug, p)
            if item is None:
                continue
            if since and item.created_at and item.created_at < since:
                continue
            items.append(item)

    priority_order = {"urgent": 0, "high": 1, "med": 2, "low": 3}
    items.sort(key=lambda it: (
        priority_order.get(it.priority, 2),
        it.created_at or datetime.now(timezone.utc),
    ))
    return items


# ── Decision actions ───────────────────────────────────────


def _record_decision(
    item: InboxItem,
    action: str,
    *,
    actor: Optional[str] = None,
    note: str = "",
    log_path: Optional[pathlib.Path] = None,
) -> None:
    """Append the decision to the audit log. Best-effort, never raises."""
    log_path = log_path or DECISIONS_LOG
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "id": item.id,
        "agent": item.agent,
        "filename": item.filename,
        "action": action,  # "approve" | "reject" | "viewed"
        "actor": actor or os.getenv("SFOS_BUS_ID") or "human",
        "note": note,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def approve(
    item: InboxItem,
    *,
    note: str = "",
    log_path: Optional[pathlib.Path] = None,
    home: Optional[pathlib.Path] = None,
) -> Optional[pathlib.Path]:
    """Move the file to queue/approved/ + audit log entry. Returns new
    path on success, None on failure (file gone, fs error)."""
    home = home or pathlib.Path.home()
    if not item.path.exists():
        _record_decision(item, "approve", note=f"FAIL file gone | {note}",
                          log_path=log_path)
        return None
    approved_dir = item.path.parent.parent / "approved"
    try:
        approved_dir.mkdir(parents=True, exist_ok=True)
        new_path = approved_dir / item.filename
        shutil.move(str(item.path), str(new_path))
    except Exception as e:
        _record_decision(item, "approve",
                          note=f"FAIL move: {e} | {note}", log_path=log_path)
        return None
    _record_decision(item, "approve", note=note, log_path=log_path)
    return new_path


def reject(
    item: InboxItem,
    *,
    note: str = "",
    log_path: Optional[pathlib.Path] = None,
    home: Optional[pathlib.Path] = None,
) -> Optional[pathlib.Path]:
    """Move to queue/rejected/ + audit log."""
    home = home or pathlib.Path.home()
    if not item.path.exists():
        _record_decision(item, "reject", note=f"FAIL file gone | {note}",
                          log_path=log_path)
        return None
    rejected_dir = item.path.parent.parent / "rejected"
    try:
        rejected_dir.mkdir(parents=True, exist_ok=True)
        new_path = rejected_dir / item.filename
        shutil.move(str(item.path), str(new_path))
    except Exception as e:
        _record_decision(item, "reject",
                          note=f"FAIL move: {e} | {note}", log_path=log_path)
        return None
    _record_decision(item, "reject", note=note, log_path=log_path)
    return new_path


# ── CLI ────────────────────────────────────────────────────


def _parse_since(spec: str) -> Optional[datetime]:
    spec = spec.strip()
    m = re.match(r"^(\d+)([smhd])$", spec)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {
            "s": timedelta(seconds=n),
            "m": timedelta(minutes=n),
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
        }[unit]
        return datetime.now(timezone.utc) - delta
    try:
        return datetime.fromisoformat(spec.replace("Z", "+00:00"))
    except Exception:
        return None


def _render_one(item: InboxItem) -> str:
    age = ""
    if item.created_at:
        delta = datetime.now(timezone.utc) - item.created_at
        if delta.total_seconds() < 3600:
            age = f"{int(delta.total_seconds() / 60)}m"
        elif delta.total_seconds() < 86400:
            age = f"{int(delta.total_seconds() / 3600)}h"
        else:
            age = f"{int(delta.total_seconds() / 86400)}d"
    icon = {"urgent": "🚨", "high": "⚠️ ", "med": "·", "low": " "}.get(
        item.priority, "·")
    parts = [
        f"  {icon} [{item.id}] {item.title} "
        f"({item.priority}, {age} old, {item.agent})",
    ]
    if item.body_preview:
        parts.append(f"     → {item.body_preview[:120]}")
    return "\n".join(parts)


def _find_by_id(item_id: str, items: list[InboxItem]) -> Optional[InboxItem]:
    for it in items:
        if it.id == item_id:
            return it
    return None


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="sfos-inbox",
        description="Unified HITL inbox across all agents.",
    )
    sub = p.add_subparsers(dest="cmd")
    p.set_defaults(cmd="list")

    pl = sub.add_parser("list", help="(default) List pending items.")
    pl.add_argument("--agent", default=None)
    pl.add_argument("--since", default=None)
    pl.add_argument("--json", action="store_true",
                     help="Emit JSON for piping.")
    pl.add_argument("-n", type=int, default=None,
                     help="Show only top N items.")

    pa = sub.add_parser("approve", help="Approve a pending item by id.")
    pa.add_argument("item_id")
    pa.add_argument("--note", default="")

    pr = sub.add_parser("reject", help="Reject a pending item by id.")
    pr.add_argument("item_id")
    pr.add_argument("--note", default="")

    args = p.parse_args(argv)

    if os.getenv("INBOX_SKIP") == "1":
        return 0

    cmd = args.cmd or "list"

    if cmd == "list":
        since = _parse_since(args.since) if getattr(args, "since", None) else None
        items = scan_inbox(agent=getattr(args, "agent", None),
                            since=since)
        if getattr(args, "n", None):
            items = items[:args.n]
        if getattr(args, "json", False):
            payload = [
                {
                    "id": it.id, "agent": it.agent,
                    "filename": it.filename, "title": it.title,
                    "priority": it.priority,
                    "created_at": (it.created_at.isoformat()
                                    if it.created_at else None),
                    "tags": it.tags,
                    "body_preview": it.body_preview,
                }
                for it in items
            ]
            print(json.dumps(payload, indent=2))
            return 0
        if not items:
            print("(inbox empty — no pending HITL items)", file=sys.stderr)
            return 0
        urgent = sum(1 for it in items if it.priority == "urgent")
        high = sum(1 for it in items if it.priority == "high")
        print(f"# Inbox — {len(items)} pending "
              f"({urgent} urgent, {high} high)\n", file=sys.stderr)
        for it in items:
            print(_render_one(it))
            print()
        return 0

    if cmd in ("approve", "reject"):
        items = scan_inbox()
        item = _find_by_id(args.item_id, items)
        if item is None:
            print(f"item id '{args.item_id}' not found in inbox",
                  file=sys.stderr)
            return 1
        action_fn = approve if cmd == "approve" else reject
        new_path = action_fn(item, note=args.note)
        if new_path is None:
            print(f"failed to {cmd} {args.item_id}", file=sys.stderr)
            return 1
        print(f"✓ {cmd}d → {new_path}", file=sys.stderr)
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
