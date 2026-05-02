"""Cross-agent retro — aggregate the stack's learning artifacts.

After 0.13's bandit + autopsy promotion and the marketing-agent v0.18.x
SFOS-mirror sinks landed, every agent in the stack writes its
reflection / skill-promotion / preference-edit data to SFOS-readable
paths. This module walks all 8 (and counting) agent dirs + the shared
SFOS dirs and produces ONE markdown digest answering:

  - Which agents are actively running? (file mtime / row counts)
  - What's each agent failing at most often? (top-3 reflexion patterns
    per agent, FAILED outcomes only)
  - Which Voyager-promoted skills are bubbling up? (skill name + age)
  - What variant_keys are winning their bandits? (per (agent, channel))
  - How thick is the ICPL preference signal per agent? (pairs in last
    7 / 30 / 90 days)

Designed to be run weekly — typical install: launchd at Sunday 09:00
local. Output lands at `~/.solo-founder-os/retro-<UTC date>.md` so
sfos-evolver / sfos-eval / a Claude conversation can read it.

Usage:
    sfos-retro                                # write to default path
    sfos-retro --out /tmp/retro.md            # custom path
    sfos-retro --json                         # machine-readable
    sfos-retro --since 7                      # last N days only
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional


# Directories the SFOS evolver already scans for reflections; keep in
# sync. Ordered: solo-founder-os' own dotdir + each agent's home dir.
KNOWN_AGENT_DIRS: list[str] = [
    ".solo-founder-os",
    ".orallexa-marketing-agent",
    ".build-quality-agent",
    ".customer-discovery-agent",
    ".funnel-analytics-agent",
    ".vc-outreach-agent",
    ".cost-audit-agent",
    ".bilingual-content-sync-agent",
    ".customer-support-agent",
    ".customer-outreach-agent",
]

SHARED_SKILLS_DIR = pathlib.Path.home() / ".solo-founder-os" / "skills"
SHARED_BANDIT_DB = pathlib.Path.home() / ".solo-founder-os" / "bandit.sqlite"


# ───────────────── data collectors ─────────────────


def _scan_reflections(agent_dir: str, *, since_days: int) -> dict:
    """Return per-agent reflexion stats: counts by outcome + top
    failure-signal buckets among FAILED rows."""
    path = pathlib.Path.home() / agent_dir / "reflections.jsonl"
    if not path.exists():
        return {"present": False}
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    cutoff_iso = cutoff.isoformat()

    counts: Counter = Counter()
    failed_signals: Counter = Counter()
    last_ts = ""
    n = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = str(row.get("ts", ""))
                if ts and ts < cutoff_iso:
                    continue
                n += 1
                if ts > last_ts:
                    last_ts = ts
                outcome = str(row.get("outcome", "UNKNOWN"))
                counts[outcome] += 1
                if outcome == "FAILED":
                    sig = str(row.get("verbatim_signal", ""))
                    bucket = _bucket_signal(sig)
                    if bucket:
                        failed_signals[bucket] += 1
    except OSError:
        return {"present": False, "error": "read failed"}

    return {
        "present": True,
        "rows_in_window": n,
        "by_outcome": dict(counts),
        "top_failure_buckets": failed_signals.most_common(3),
        "last_ts": last_ts,
    }


def _bucket_signal(signal: str) -> Optional[str]:
    """Cluster a free-text reflexion signal into a coarse bucket so
    evolver-style aggregation works. Mirrors the heuristic in
    sfos-evolver._bucket_signal."""
    s = signal.lower()
    if not s:
        return None
    rules = [
        (r"hype|revolutionary|cutting.edge|game.chang", "hype-words"),
        (r"too long|exceeds.+chars|over.+chars", "length-overshoot"),
        (r"hashtag", "hashtag-spam"),
        (r"all.caps|shouting", "all-caps"),
        (r"duplicate|near.dup|paraphrase", "duplicate-content"),
        (r"timeout|connection|network|unreachable", "network-error"),
        (r"401|unauth|invalid token|bad credentials", "auth-failure"),
        (r"rate.limit|429|too many requests", "rate-limit"),
        (r"empty|no.content|blank", "empty-output"),
        (r"missing|required|not provided", "missing-input"),
    ]
    for pattern, bucket in rules:
        if re.search(pattern, s):
            return bucket
    # First two words as a fallback bucket
    words = re.findall(r"[a-z]+", s)
    if not words:
        return None
    return "-".join(words[:2])[:40]


def _scan_preferences(agent_dir: str, *, since_days: int) -> dict:
    """Per-agent ICPL preference-pair stats. Pairs in the window and
    distinct task types touched."""
    path = pathlib.Path.home() / agent_dir / "preference-pairs.jsonl"
    if not path.exists():
        return {"present": False}
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    cutoff_iso = cutoff.isoformat()
    n = 0
    by_task: Counter = Counter()
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = str(row.get("ts", ""))
                if ts and ts < cutoff_iso:
                    continue
                n += 1
                by_task[str(row.get("task", "?"))] += 1
    except OSError:
        return {"present": False, "error": "read failed"}
    return {
        "present": True,
        "pairs_in_window": n,
        "by_task": dict(by_task.most_common(5)),
    }


def _scan_skills() -> list[dict]:
    """Walk the shared SFOS skills dir. Returns sorted-newest first."""
    if not SHARED_SKILLS_DIR.exists():
        return []
    out: list[dict] = []
    for p in SHARED_SKILLS_DIR.glob("*.md"):
        try:
            stat = p.stat()
        except OSError:
            continue
        out.append({
            "name": p.stem,
            "path": str(p),
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return sorted(out, key=lambda r: r["mtime"], reverse=True)


def _scan_bandit() -> list[dict]:
    """Per-(agent, channel) bandit winners + arm summary."""
    if not SHARED_BANDIT_DB.exists():
        return []
    out: list[dict] = []
    with sqlite3.connect(SHARED_BANDIT_DB) as conn:
        try:
            rows = conn.execute(
                """SELECT agent, channel, variant_key, alpha, beta, n_pulls
                   FROM bandit_arm
                   ORDER BY agent, channel, n_pulls DESC"""
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    by_pair: dict[tuple, list[dict]] = defaultdict(list)
    for agent, channel, vk, alpha, beta, n_pulls in rows:
        by_pair[(agent, channel)].append({
            "variant_key": vk,
            "n_pulls": int(n_pulls),
            "mean": round(alpha / (alpha + beta), 4),
        })
    for (agent, channel), arms in by_pair.items():
        winner = max(arms, key=lambda a: a["mean"])["variant_key"]
        total = sum(a["n_pulls"] for a in arms)
        out.append({
            "agent": agent,
            "channel": channel,
            "winner": winner,
            "total_pulls": total,
            "arms": arms,
        })
    return out


# ───────────────── aggregator ─────────────────


def collect(*, since_days: int = 30) -> dict:
    """Collect everything. Returns a structured dict; render_markdown
    or render_json walk it from there."""
    per_agent: dict[str, dict] = {}
    for agent_dir in KNOWN_AGENT_DIRS:
        per_agent[agent_dir] = {
            "reflections": _scan_reflections(agent_dir, since_days=since_days),
            "preferences": _scan_preferences(agent_dir, since_days=since_days),
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since_days": since_days,
        "per_agent": per_agent,
        "skills": _scan_skills(),
        "bandit": _scan_bandit(),
    }


def render_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append("# Solo Founder OS — cross-agent retro")
    lines.append("")
    lines.append(f"*Generated {report['generated_at']} · "
                    f"window {report['since_days']} days*")
    lines.append("")

    # Stack-wide summary
    n_active = sum(1 for d in report["per_agent"].values()
                       if d["reflections"].get("present"))
    n_pref_active = sum(1 for d in report["per_agent"].values()
                            if d["preferences"].get("present"))
    total_refl = sum(d["reflections"].get("rows_in_window", 0)
                          for d in report["per_agent"].values())
    total_pref = sum(d["preferences"].get("pairs_in_window", 0)
                          for d in report["per_agent"].values())
    n_skills = len(report["skills"])
    n_bandit = len(report["bandit"])
    lines.extend([
        "## Stack-wide",
        "",
        f"- **Agents writing reflections:** {n_active}/{len(KNOWN_AGENT_DIRS)}",
        f"- **Agents writing preferences:** {n_pref_active}/{len(KNOWN_AGENT_DIRS)}",
        f"- **Total reflexion rows in window:** {total_refl}",
        f"- **Total preference pairs in window:** {total_pref}",
        f"- **Promoted skills (shared dir):** {n_skills}",
        f"- **Active bandits (agent × channel):** {n_bandit}",
        "",
    ])

    # Per-agent
    lines.append("## Per-agent")
    lines.append("")
    for agent_dir, d in report["per_agent"].items():
        refl = d["reflections"]
        pref = d["preferences"]
        lines.append(f"### `{agent_dir}`")
        lines.append("")
        if not refl.get("present") and not pref.get("present"):
            lines.append("- *No SFOS-readable data yet (agent not run / "
                            "not yet writing mirrored sinks).*")
            lines.append("")
            continue
        if refl.get("present"):
            counts = refl.get("by_outcome", {})
            counts_s = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            lines.append(f"- **Reflections in window:** "
                            f"{refl['rows_in_window']} ({counts_s or 'no rows'})")
            top = refl.get("top_failure_buckets", [])
            if top:
                lines.append("- **Top failure patterns:** "
                                + ", ".join(f"`{b}` ({n})" for b, n in top))
        if pref.get("present"):
            lines.append(f"- **Preference pairs in window:** "
                            f"{pref['pairs_in_window']}"
                            + (f"  ({pref['by_task']})"
                                  if pref.get("by_task") else ""))
        lines.append("")

    # Skills
    if report["skills"]:
        lines.append("## Promoted skills (shared dir, newest first)")
        lines.append("")
        for s in report["skills"][:15]:
            lines.append(f"- `{s['name']}` "
                            f"(updated {s['mtime']}, {s['size_bytes']} bytes)")
        if len(report["skills"]) > 15:
            lines.append(f"- *…and {len(report['skills']) - 15} more*")
        lines.append("")

    # Bandit
    if report["bandit"]:
        lines.append("## Bandit winners per (agent, channel)")
        lines.append("")
        lines.append("| agent | channel | winner | total pulls | arms |")
        lines.append("|---|---|---|---|---|")
        for b in report["bandit"]:
            arms_s = ", ".join(f"{a['variant_key']} (n={a['n_pulls']}, "
                                  f"μ={a['mean']})"
                                  for a in b["arms"])
            lines.append(f"| {b['agent']} | {b['channel']} | "
                            f"**{b['winner']}** | {b['total_pulls']} | "
                            f"{arms_s} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# ───────────────── CLI ─────────────────


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sfos-retro",
        description="Aggregate cross-agent reflection / skill / "
                    "preference / bandit data into one weekly retro.",
    )
    ap.add_argument("--out", default=None,
                     help="Write markdown to this path "
                          "(default: ~/.solo-founder-os/retro-<UTC date>.md)")
    ap.add_argument("--json", action="store_true",
                     help="Emit machine-readable JSON instead of markdown")
    ap.add_argument("--since", type=int, default=30,
                     help="Lookback window in days (default 30)")
    args = ap.parse_args(argv)

    report = collect(since_days=args.since)

    if args.json:
        out_text = json.dumps(report, indent=2, ensure_ascii=False)
    else:
        out_text = render_markdown(report)

    if args.out:
        out_path = pathlib.Path(args.out)
    elif args.json:
        out_path = (pathlib.Path.home() / ".solo-founder-os"
                       / f"retro-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json")
    else:
        out_path = (pathlib.Path.home() / ".solo-founder-os"
                       / f"retro-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out_text, encoding="utf-8")
    print(f"📊 retro written: {out_path}")
    if not args.json:
        # Print one-line stack-wide summary for the cron / human reader
        n_active = sum(1 for d in report["per_agent"].values()
                            if d["reflections"].get("present"))
        total_refl = sum(d["reflections"].get("rows_in_window", 0)
                              for d in report["per_agent"].values())
        n_skills = len(report["skills"])
        n_bandit = len(report["bandit"])
        print(f"   {n_active} agents active · {total_refl} reflections · "
              f"{n_skills} skills · {n_bandit} bandits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
