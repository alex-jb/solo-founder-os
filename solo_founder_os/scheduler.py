"""macOS launchd + crontab helpers.

Lifted from funnel-analytics-agent v0.4's install-cron.sh, generalized so
any agent in the stack can install its own scheduled job in one call.

Two output formats:
- launchd plist (macOS native — survives reboot, integrates with system
  logging)
- crontab line (Linux + macOS legacy)

The library ONLY produces the plist/crontab text + writes it to the
right path. It does NOT shell out to `launchctl`/`crontab`. That's
deliberate: agents wrap this with their own install-cron.sh that knows
how to handle reload errors, idempotency, env file location, etc. The
library covers the part that actually differs (the plist contents).

Usage:
    from solo_founder_os.scheduler import build_launchd_plist
    plist = build_launchd_plist(
        label="com.alex.funnel-analytics.brief",
        program=["/usr/local/bin/funnel-analytics-agent",
                 "--out", "/path/to/brief.md"],
        schedule={"hour": 7, "minute": 3},   # daily at 7:03 AM
        stdout_path="/path/to/log",
        stderr_path="/path/to/err.log",
    )
    Path("~/Library/LaunchAgents/com.alex.funnel-analytics.brief.plist").write_text(plist)
"""
from __future__ import annotations
import os
import pathlib
from typing import Iterable


def _xml_escape(s: str) -> str:
    """XML-escape for plist string fields. Keep deliberately minimal — we
    don't expect exotic chars in CLI args, but `&` and `<` we do."""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def build_launchd_plist(
    *,
    label: str,
    program: list[str] | tuple[str, ...],
    schedule: dict | int | None = None,
    stdout_path: str | os.PathLike | None = None,
    stderr_path: str | os.PathLike | None = None,
    working_dir: str | os.PathLike | None = None,
    run_at_load: bool = False,
    keep_alive: bool = False,
) -> str:
    """Build a launchd plist string.

    `schedule` accepts:
      - dict {"hour": 7, "minute": 3, "day": 15, ...} → StartCalendarInterval
      - int N → StartInterval (every N seconds)
      - None → no schedule (RunAtLoad / KeepAlive must be set or the job
        never fires)

    `program` is the argv. First element is the executable. Use absolute
    paths in production — launchd doesn't have a useful PATH.

    Returns the plist as a UTF-8 string. Caller is responsible for
    writing it to ~/Library/LaunchAgents/<label>.plist and running
    `launchctl load`.
    """
    if not label:
        raise ValueError("label is required")
    if not program:
        raise ValueError("program (argv) is required and must be non-empty")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
        '<plist version="1.0">',
        '<dict>',
        f'    <key>Label</key>',
        f'    <string>{_xml_escape(label)}</string>',
        '    <key>ProgramArguments</key>',
        '    <array>',
    ]
    for arg in program:
        lines.append(f'        <string>{_xml_escape(str(arg))}</string>')
    lines.append('    </array>')

    if isinstance(schedule, int):
        lines += [
            '    <key>StartInterval</key>',
            f'    <integer>{schedule}</integer>',
        ]
    elif isinstance(schedule, dict) and schedule:
        lines += ['    <key>StartCalendarInterval</key>', '    <dict>']
        # launchd supports Minute, Hour, Day, Month, Weekday
        key_map = {
            "minute": "Minute", "hour": "Hour", "day": "Day",
            "month": "Month", "weekday": "Weekday",
        }
        for k, v in schedule.items():
            plist_key = key_map.get(k.lower(), k)
            lines.append(f'        <key>{plist_key}</key>')
            lines.append(f'        <integer>{int(v)}</integer>')
        lines.append('    </dict>')

    if stdout_path:
        lines += [
            '    <key>StandardOutPath</key>',
            f'    <string>{_xml_escape(str(stdout_path))}</string>',
        ]
    if stderr_path:
        lines += [
            '    <key>StandardErrorPath</key>',
            f'    <string>{_xml_escape(str(stderr_path))}</string>',
        ]
    if working_dir:
        lines += [
            '    <key>WorkingDirectory</key>',
            f'    <string>{_xml_escape(str(working_dir))}</string>',
        ]
    if run_at_load:
        lines += ['    <key>RunAtLoad</key>', '    <true/>']
    if keep_alive:
        lines += ['    <key>KeepAlive</key>', '    <true/>']

    lines += ['</dict>', '</plist>', '']
    return "\n".join(lines)


def build_cron_line(
    *,
    schedule: str,
    command: str,
    comment: str = "",
) -> str:
    """Build a single crontab line for Linux / generic *nix.

    `schedule` is a 5-field cron expression (e.g. "*/7 * * * *").
    `command` is the full shell command to run (single string — caller
    handles quoting).
    `comment` is rendered as a `#` line above the entry.

    Returns the line(s) — caller is responsible for `crontab -e` or
    similar idempotent install.
    """
    out = []
    if comment:
        for line in comment.splitlines():
            out.append(f"# {line}")
    out.append(f"{schedule} {command}")
    return "\n".join(out) + "\n"


def launch_agent_path(label: str, *,
                      home: pathlib.Path | None = None) -> pathlib.Path:
    """Where launchd expects the plist file. Returns the canonical path
    `~/Library/LaunchAgents/<label>.plist`.
    """
    home = home or pathlib.Path.home()
    return home / "Library" / "LaunchAgents" / f"{label}.plist"


__all__ = [
    "build_launchd_plist",
    "build_cron_line",
    "launch_agent_path",
]
