"""CLI skeleton — common flags every solo-founder-os agent gets for free.

Pattern:
    p = argparse.ArgumentParser(prog="my-agent")
    add_common_args(p)
    sub = p.add_subparsers(dest="cmd", required=True)
    # ... agent-specific subparsers ...
    args = p.parse_args(argv)
    if check_skip(args, "MY_AGENT_SKIP"):
        return 0

The flags `add_common_args` adds:
  --quiet           suppress info-level output
  --dry-run         do everything except writes / sends / publishes
  --no-baseline     skip baseline lookup + record (per-run override)
  --notify          comma-separated notifier names (only meaningful if
                     the agent has wired solo_founder_os.notifier)

These flags are NOT all relevant to every agent; agents can drop ones
they don't use by passing `omit=["--no-baseline"]` etc.
"""
from __future__ import annotations
import argparse
import os


def add_common_args(parser: argparse.ArgumentParser, *,
                    omit: tuple[str, ...] = ()) -> None:
    """Add the canonical common flags. Pass `omit` to skip flags an agent
    doesn't use (e.g. `omit=("--no-baseline",)` for agents without a
    baseline log).
    """
    if "--quiet" not in omit:
        parser.add_argument(
            "--quiet", action="store_true",
            help="Suppress info-level output (errors still print).")
    if "--dry-run" not in omit:
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Run logic but don't write files / send messages / publish.")
    if "--no-baseline" not in omit:
        parser.add_argument(
            "--no-baseline", action="store_true",
            help="Skip 7-day baseline lookup + record (per-run override).")
    if "--notify" not in omit:
        parser.add_argument(
            "--notify", default=None,
            help="Comma-separated notifier names (ntfy,telegram,slack). "
                 "Default: NOTIFIER_DEFAULT env or none.")


def check_skip(skip_env: str) -> bool:
    """Standard `<AGENT>_SKIP=1` no-op gate. Agents check this first thing
    in their main() so cron or CI can disable them cheaply.

    Returns True iff the agent should exit 0 immediately.
    """
    return os.getenv(skip_env) == "1"


def resolve_notify_targets(args_notify: str | None,
                           default_env: str = "NOTIFIER_DEFAULT") -> list[str]:
    """Parse `--notify` arg or fall back to env var. Returns a clean list
    of notifier names (whitespace stripped, empties removed). Agent
    callers still need to filter against their wired notifier registry."""
    raw = args_notify or os.getenv(default_env, "")
    return [n.strip() for n in raw.split(",") if n.strip()]
