"""Filesystem-backed HITL workflow: pending → approved → rejected / sent.

Lifted from vc-outreach-agent v0.3 + bilingual-content-sync-agent v0.1
where the same pattern shipped twice. Now one canonical implementation.

The pattern:
    queue/
      pending/   ← agent writes proposals here
      approved/  ← human moves files in (file content is the contract)
      rejected/  ← human moves files here to archive without acting
      sent/      ← agent moves files here after acting on `approved/`

Agents render + parse their own markdown body. This class only handles:
- where files live (directory tree)
- how they're named (timestamp + slug)
- status transitions (move with fresh timestamp prefix)
- standard YAML-ish frontmatter parser (sufficient for our flat dicts)
- filename sanitization (filesystem-safe slugs)

Why frontmatter instead of nested JSON metadata: founders open these
files in Obsidian / VS Code and edit by hand. Frontmatter renders nicely.
"""
from __future__ import annotations
import os
import pathlib
import re
from datetime import datetime, timezone
from typing import Iterable


PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
SENT = "sent"
VALID_STATUSES = (PENDING, APPROVED, REJECTED, SENT)


_FILENAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9-]+")


def sanitize_filename_part(s: str) -> str:
    """Filesystem-safe slug: alnum + dashes only, no leading/trailing dashes."""
    return _FILENAME_SAFE_RE.sub("-", s).strip("-").lower() or "x"


def make_basename(parts: Iterable[str], *, ts: datetime | None = None) -> str:
    """Build `<YYYYMMDDTHHMMSS>-<part1>-<part2>.md`.

    `parts` is sanitized + dash-joined. `ts` defaults to now (UTC).
    """
    ts = ts or datetime.now(timezone.utc)
    slug = "-".join(sanitize_filename_part(p) for p in parts if p)
    return f"{ts.strftime('%Y%m%dT%H%M%S')}-{slug}.md"


# ─── frontmatter ─────────────────────────────────────────────────

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict:
    """Return YAML-ish frontmatter as a flat dict. Returns {} if absent.

    Grammar: lines of `key: value` between leading and trailing `---`.
    Multi-line values are not supported (kept simple on purpose — agents
    that need richer schemas can JSON-encode into a single value).
    """
    m = _FM_RE.match(text)
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def render_frontmatter(d: dict) -> str:
    """Render a flat dict as `---\\n…\\n---\\n` block. Caller appends body."""
    lines = ["---"]
    for k, v in d.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ─── HitlQueue ───────────────────────────────────────────────────

class HitlQueue:
    """Directory-backed pending/approved/rejected/sent workflow.

    Args:
        root: directory that contains the four status subdirs. Created
            on first write. Conventional location:
            ~/.<agent-name>/queue.
    """
    PENDING = PENDING
    APPROVED = APPROVED
    REJECTED = REJECTED
    SENT = SENT

    def __init__(self, root: pathlib.Path | str):
        self.root = pathlib.Path(root)

    @classmethod
    def from_env(cls, env_var: str, *, default: pathlib.Path | str) -> "HitlQueue":
        """Honor `os.environ[env_var]` if set; otherwise use `default`.
        Used by agents to expose a `<AGENT>_QUEUE` env var while keeping
        a sensible fallback in `~/.<agent-name>/queue/`.
        """
        override = os.getenv(env_var)
        return cls(pathlib.Path(override) if override else pathlib.Path(default))

    def _path_for(self, status: str) -> pathlib.Path:
        if status not in VALID_STATUSES:
            raise ValueError(f"unknown status: {status!r} "
                             f"(expected one of {VALID_STATUSES})")
        return self.root / status

    def write(self, basename: str, content: str,
              *, status: str = PENDING) -> pathlib.Path:
        """Write `content` to `<root>/<status>/<basename>`. Creates parent
        dir if missing. Returns the path."""
        p = self._path_for(status)
        p.mkdir(parents=True, exist_ok=True)
        path = p / basename
        path.write_text(content, encoding="utf-8")
        return path

    def list(self, *, status: str = PENDING) -> list[pathlib.Path]:
        """Return all .md files at `<root>/<status>/`, sorted by name
        (which sorts by timestamp due to our naming convention)."""
        p = self._path_for(status)
        if not p.exists():
            return []
        return sorted(p.glob("*.md"))

    def move(self, path: pathlib.Path, *, to: str,
             prefix_ts: bool = True) -> pathlib.Path:
        """Move `path` to the `to` status dir. By default prefixes the
        destination filename with a fresh UTC timestamp so chronological
        order in the destination reflects the move time, not the original
        write time. (Set `prefix_ts=False` to keep the original name.)
        """
        dest_dir = self._path_for(to)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if prefix_ts:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            dest = dest_dir / f"{ts}-{path.name}"
        else:
            dest = dest_dir / path.name
        return path.rename(dest)
