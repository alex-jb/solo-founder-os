"""Agent bus — filesystem broadcast for cross-terminal / cross-process coordination.

Use case: you have 3 Claude Code instances open in different terminals, all
working on parts of the same stack. Each one wants to know what the others
are doing, leave notes for them, claim work to avoid duplicate effort.

The bus is a directory tree of markdown files:

    ~/.solo-founder-os/bus/<channel>/<YYYY-MM-DDTHH-MM-SS>-<terminal>.md

Each post is one file, append-only, atomic via filesystem write. No locks,
no daemons, no extra deps. Anyone with read access to the dir sees the
same view.

Default channel: `general`. Useful named channels:
  - general       general status / "I'm working on X"
  - coord         "I'm taking the brain index, please don't touch for 5min"
  - blockers      "Cron install failing, need a hand"
  - findings      "Found a real bug in vc-outreach drafter:..."

CLI:
    sfos-bus post "working on customer-support-agent"
    sfos-bus post --channel coord "claiming brain index, 5 min"
    sfos-bus tail                       # last 20 messages, all channels
    sfos-bus tail --channel coord       # filter by channel
    sfos-bus tail --since 30m           # last 30 minutes
    sfos-bus tail --me                  # only my own messages

Terminal id auto-derives from tty + ppid: a stable string like
'tty1-pid1234' that distinguishes 3 simultaneous Claude Code sessions
without requiring config. Override via SFOS_BUS_ID env var.

This is NOT a chat — there's no synchronization, no notifications, no
read state. It's a shared notebook. If you need real coordination
(claim-and-execute task list), use Anthropic Claude Code Agent Teams
or the HITL queue.
"""
from __future__ import annotations
import argparse
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


BUS_DIR = pathlib.Path.home() / ".solo-founder-os" / "bus"


@dataclass
class Message:
    channel: str
    terminal: str
    ts: datetime
    body: str
    path: Optional[pathlib.Path] = None
    tags: list[str] = field(default_factory=list)


# ── Terminal identity ─────────────────────────────────────


def _terminal_id() -> str:
    """Stable per-Claude-Code-instance ID. Uses parent PID + tty so multiple
    sessions in different terminals naturally have distinct IDs.

    Override with SFOS_BUS_ID for custom names like 'main', 'review'."""
    override = os.getenv("SFOS_BUS_ID")
    if override:
        return _slug(override) or "anon"
    # Cross-platform best-effort
    try:
        ppid = os.getppid()
    except Exception:
        ppid = 0
    tty = ""
    try:
        # On macOS / Linux, this returns the controlling tty if any
        tty = os.ttyname(0) if hasattr(os, "ttyname") else ""
    except Exception:
        tty = ""
    if tty:
        # Strip /dev/ prefix and slashes
        tty = tty.replace("/dev/", "").replace("/", "-")
    base = f"{tty}-pid{ppid}" if tty else f"pid{ppid}"
    return _slug(base) or "anon"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "-", text).strip("-")
    return s[:60]


# ── Post / read ──────────────────────────────────────────


def post(
    body: str,
    *,
    channel: str = "general",
    terminal: Optional[str] = None,
    base: Optional[pathlib.Path] = None,
    tags: Optional[list[str]] = None,
) -> pathlib.Path:
    """Write a message to the bus. Returns the file path created.

    `body` is the message text — markdown supported. Capped at 4000 chars.
    `tags` is an optional list of short labels (added to YAML frontmatter
    so other readers can filter).
    """
    base = base or pathlib.Path.home() / ".solo-founder-os" / "bus"
    terminal = terminal or _terminal_id()
    channel = _slug(channel) or "general"
    ts = datetime.now(timezone.utc)
    ts_compact = ts.strftime("%Y-%m-%dT%H-%M-%S")

    chan_dir = base / channel
    try:
        chan_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Filesystem error — return a placeholder path; never raise
        return base / channel / "ERROR.md"

    filename = f"{ts_compact}-{_slug(terminal)}.md"
    path = chan_dir / filename
    if path.exists():
        # Race: same second, same terminal. Tiebreak with microseconds.
        path = chan_dir / f"{ts_compact}-{_slug(terminal)}-{ts.microsecond:06d}.md"

    tags_line = ""
    if tags:
        clean = [_slug(t) for t in tags if t]
        if clean:
            tags_line = f"tags: [{', '.join(clean)}]\n"

    md = (
        "---\n"
        f"channel: {channel}\n"
        f"terminal: {terminal}\n"
        f"ts: {ts.isoformat()}\n"
        f"{tags_line}"
        "---\n\n"
        f"{body[:4000]}\n"
    )
    try:
        path.write_text(md, encoding="utf-8")
    except Exception:
        pass
    return path


def _parse_message(path: pathlib.Path) -> Optional[Message]:
    """Parse a bus message file. Returns None if malformed."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    fm = text[4:end]
    body = text[end + 5:].strip()
    meta = {}
    for line in fm.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    try:
        ts = datetime.fromisoformat(
            (meta.get("ts") or "").replace("Z", "+00:00"))
    except Exception:
        return None
    tags: list[str] = []
    raw_tags = meta.get("tags", "")
    if raw_tags.startswith("[") and raw_tags.endswith("]"):
        inner = raw_tags[1:-1]
        tags = [t.strip() for t in inner.split(",") if t.strip()]
    return Message(
        channel=meta.get("channel", "general"),
        terminal=meta.get("terminal", "?"),
        ts=ts,
        body=body,
        path=path,
        tags=tags,
    )


def read(
    *,
    channel: Optional[str] = None,
    since: Optional[datetime] = None,
    terminal: Optional[str] = None,
    n: int = 50,
    base: Optional[pathlib.Path] = None,
) -> list[Message]:
    """Read messages from the bus, newest last.

    `channel`: limit to one channel; None = all channels
    `since`: only messages after this timestamp (UTC)
    `terminal`: filter to one terminal id
    `n`: cap at last N messages (default 50)
    """
    base = base or pathlib.Path.home() / ".solo-founder-os" / "bus"
    if not base.exists():
        return []
    paths: list[pathlib.Path] = []
    if channel:
        chan_dir = base / _slug(channel)
        if chan_dir.exists():
            paths = list(chan_dir.glob("*.md"))
    else:
        for chan_dir in base.iterdir():
            if chan_dir.is_dir():
                paths.extend(chan_dir.glob("*.md"))

    msgs: list[Message] = []
    for p in paths:
        m = _parse_message(p)
        if m is None:
            continue
        if since and m.ts < since:
            continue
        if terminal and m.terminal != terminal:
            continue
        msgs.append(m)
    msgs.sort(key=lambda m: m.ts)
    return msgs[-n:]


# ── CLI ──────────────────────────────────────────────────


def _parse_since(spec: str) -> Optional[datetime]:
    """Parse '30m', '2h', '1d', or an ISO timestamp."""
    if not spec:
        return None
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


def _render_one(msg: Message) -> str:
    """Pretty-print one message for terminal display."""
    local = msg.ts.astimezone()
    return (
        f"  [{local.strftime('%Y-%m-%d %H:%M:%S')}] "
        f"[{msg.channel}] @{msg.terminal}\n"
        f"  {msg.body[:200].replace(chr(10), ' ⏎ ')}"
        + (f"\n  tags: {', '.join(msg.tags)}" if msg.tags else "")
    )


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="sfos-bus",
        description="Cross-terminal coordination via filesystem-broadcast.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("post", help="Post a message to the bus.")
    pp.add_argument("body", help="Message body (markdown ok).")
    pp.add_argument("--channel", default="general")
    pp.add_argument("--terminal", default=None,
                     help="Override terminal id (default: auto-derived)")
    pp.add_argument("--tags", default="",
                     help="Comma-separated tags.")

    pt = sub.add_parser("tail", help="Show recent messages.")
    pt.add_argument("--channel", default=None)
    pt.add_argument("--since", default=None,
                     help="Filter: '30m', '2h', '1d', or ISO timestamp.")
    pt.add_argument("--me", action="store_true",
                     help="Show only this terminal's messages.")
    pt.add_argument("-n", type=int, default=20)

    pl = sub.add_parser("list-channels", help="List active channels.")
    pl.add_argument("--base", default=None)

    args = p.parse_args(argv)

    if os.getenv("SFOS_BUS_SKIP") == "1":
        return 0

    if args.cmd == "post":
        tags = [t for t in (args.tags or "").split(",") if t.strip()]
        path = post(args.body, channel=args.channel,
                     terminal=args.terminal, tags=tags or None)
        print(f"✓ {path}", file=sys.stderr)
        return 0

    if args.cmd == "tail":
        since = _parse_since(args.since) if args.since else None
        terminal = _terminal_id() if args.me else None
        msgs = read(channel=args.channel, since=since,
                     terminal=terminal, n=args.n)
        if not msgs:
            print("(no messages)", file=sys.stderr)
            return 0
        for m in msgs:
            print(_render_one(m))
            print()
        return 0

    if args.cmd == "list-channels":
        bus_dir = pathlib.Path.home() / ".solo-founder-os" / "bus"
        if not bus_dir.exists():
            print("(no channels yet)", file=sys.stderr)
            return 0
        channels = sorted(p.name for p in bus_dir.iterdir() if p.is_dir())
        for c in channels:
            n = len(list((bus_dir / c).glob("*.md")))
            print(f"  {c}: {n} messages")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
