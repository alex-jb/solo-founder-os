"""Morning Brief — the canonical solo-founder-stack daily digest.

Lifted from research on what real one-person agent companies (Pieter Levels
$3M ARR, YC W26 22 solo cohort, Mavik HITL framework, DEV.to "20+ agents"
post) actually do: the founder reads ONE digest at 7am over coffee, runs
two batch-approval passes per day, ~30 minutes total time on the stack.

The dashboard's homepage IS this digest — research showed solo founders
don't watch real-time dashboards, they read morning briefs. Real-time
monitoring is alert-fatigue anti-pattern at our volume (5-15 HITL items/wk).

This module is pure-functional data assembly. ui.py renders it.

Sections:
  1. Overnight activity — what happened in the last N hours across the stack
  2. Today's queue — what's blocking the founder right now (HITL pending)
  3. Anomalies — failed crons, eval drift, high reflexion FAILED rate
  4. Cost — last 7 days of Anthropic spend vs prior 7 (if usage logs exist)

Confidence-banded routing (Mavik framework) is surfaced visually:
  >90% auto-acted (no review needed)
   70-90% queued (in HITL)
  <70% rejected with reason

Cost: zero. Pure file scans, no Claude calls.
"""
from __future__ import annotations
import json
import pathlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from .cross_agent_report import KNOWN_AGENT_DIRS


@dataclass
class BriefSection:
    """One renderable block of the morning brief."""
    title: str
    summary: str                      # one-line headline
    bullets: list[str] = field(default_factory=list)
    severity: str = "info"            # info | warn | alert


@dataclass
class MorningBrief:
    generated_at: str
    window_hours: int
    sections: list[BriefSection]
    total_pending_hitl: int
    total_anomalies: int


# ─────────────────────────── data scans ───────────────────────────


def _read_jsonl_window(
    path: pathlib.Path, *, since: datetime, limit: int = 1000,
) -> list[dict]:
    """Tail-of-file scan; only return rows whose `ts` is >= since."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    cutoff_iso = since.isoformat()
    out: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = str(row.get("ts", ""))
        if ts and ts >= cutoff_iso:
            out.append(row)
    return out


def _overnight_activity(home: pathlib.Path, since: datetime) -> BriefSection:
    """Walk every agent's reflections.jsonl in window. Headline = what
    each agent shipped, what failed."""
    by_agent: dict[str, Counter] = {}
    n_total = 0
    for slug in KNOWN_AGENT_DIRS:
        rows = _read_jsonl_window(
            home / slug / "reflections.jsonl", since=since,
        )
        if not rows:
            continue
        c: Counter = Counter()
        for r in rows:
            c[r.get("outcome", "?")] += 1
        by_agent[slug] = c
        n_total += sum(c.values())

    if not by_agent:
        return BriefSection(
            title="Overnight activity",
            summary="No reflexion rows in the window. Agents may not have run "
                    "yet, or are running but not logging.",
            severity="info",
        )

    bullets = []
    for agent in sorted(by_agent):
        c = by_agent[agent]
        parts = [f"{count} {outcome}" for outcome, count in c.most_common()]
        bullets.append(f"`{agent}` — {', '.join(parts)}")

    return BriefSection(
        title="Overnight activity",
        summary=f"{n_total} reflexion rows across "
                f"{len(by_agent)}/{len(KNOWN_AGENT_DIRS)} agents.",
        bullets=bullets,
        severity="info",
    )


def _hitl_pending(home: pathlib.Path) -> tuple[BriefSection, int]:
    """Count pending HITL items per agent. Used both as a section AND
    to feed the 'how long to clear queue' estimate."""
    by_agent: dict[str, int] = {}
    total = 0
    for slug in KNOWN_AGENT_DIRS:
        agent_root = home / slug
        if not agent_root.exists():
            continue
        # Standard layout + nested marketing layout (same as ui.py scanner)
        pending_dirs = list(agent_root.glob("queue/pending"))
        pending_dirs += list(agent_root.glob("queue/*/pending"))
        n = 0
        for d in pending_dirs:
            if d.is_dir():
                n += len(list(d.glob("*.md")))
        if n:
            by_agent[slug] = n
            total += n

    if total == 0:
        return BriefSection(
            title="What needs you today",
            summary="Inbox zero. Nothing pending.",
            severity="info",
        ), 0

    bullets = [f"`{a}` — {n} pending" for a, n in sorted(by_agent.items())]
    # Solo-founder time-budget heuristic (from Mavik): ~30s per HITL card.
    minutes = max(1, round(total * 30 / 60))
    severity = "info" if total < 10 else "warn"

    return BriefSection(
        title="What needs you today",
        summary=f"{total} item{'s' if total != 1 else ''} pending — "
                f"~{minutes} min to clear.",
        bullets=bullets,
        severity=severity,
    ), total


def _anomalies(
    home: pathlib.Path, since: datetime,
) -> tuple[BriefSection, int]:
    """Three anomaly classes:
        a) eval drift — any skill whose latest mean_overall < 2.0/5
        b) high reflexion FAILED rate — agent with > 30% FAILED in window
        c) cron failures — non-zero exit signal in cron-logs/*.err.log
    """
    bullets: list[str] = []

    # (a) Eval drift — read latest report per skill
    evals_dir = home / ".solo-founder-os" / "evals"
    if evals_dir.exists():
        latest_per_skill: dict[str, dict] = {}
        for p in sorted(evals_dir.glob("*.json")):
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            skill = obj.get("skill", "?")
            latest_per_skill[skill] = obj  # last-wins because sorted ascending
        for skill, obj in sorted(latest_per_skill.items()):
            mean = obj.get("mean_overall", 0)
            n = obj.get("n_examples", 0)
            if mean and mean < 2.0:
                bullets.append(
                    f"⚠️  `{skill}` mean {mean:.2f}/5 (n={n}) — "
                    f"prompt likely needs work"
                )

    # (b) Per-agent FAILED rate in window
    for slug in KNOWN_AGENT_DIRS:
        rows = _read_jsonl_window(
            home / slug / "reflections.jsonl", since=since,
        )
        if len(rows) < 4:
            continue  # too few rows to draw a rate signal
        failed = sum(1 for r in rows if r.get("outcome") == "FAILED")
        rate = failed / len(rows)
        if rate > 0.3:
            bullets.append(
                f"🔴 `{slug}` — {failed}/{len(rows)} FAILED ({rate:.0%}) "
                f"in window"
            )

    # (c) Cron failures — quick scan for "Traceback" / "Error" tail markers
    err_dir = home / ".solo-founder-os" / "cron-logs"
    if err_dir.exists():
        for p in sorted(err_dir.glob("*.err.log")):
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            tail = text.splitlines()[-50:]
            if any(("Traceback" in line) or ("Error:" in line)
                       for line in tail):
                bullets.append(f"🔴 cron error in `{p.name}` — check log tail")

    if not bullets:
        return BriefSection(
            title="Anomalies",
            summary="None. Stack looking healthy.",
            severity="info",
        ), 0

    severity = "alert" if any(b.startswith("🔴") for b in bullets) else "warn"
    return BriefSection(
        title="Anomalies",
        summary=f"{len(bullets)} signal{'s' if len(bullets) != 1 else ''} "
                f"flagged.",
        bullets=bullets,
        severity=severity,
    ), len(bullets)


def _cost_summary(home: pathlib.Path) -> Optional[BriefSection]:
    """Sum cost from per-agent usage.jsonl across last 7d vs prior 7d.
    Returns None if no usage logs exist anywhere (clean install).
    """
    now = datetime.now(timezone.utc)
    last_7d = (now - timedelta(days=7)).isoformat()
    prior_7d = (now - timedelta(days=14)).isoformat()

    last_total = 0.0
    prior_total = 0.0
    n_logs = 0

    for slug in KNOWN_AGENT_DIRS + [".solo-founder-os"]:
        path = home / slug / "usage.jsonl"
        if not path.exists():
            continue
        n_logs += 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-2000:]
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = str(row.get("ts", ""))
            cost = float(row.get("cost_usd", 0) or 0)
            if not ts or cost == 0:
                continue
            if ts >= last_7d:
                last_total += cost
            elif ts >= prior_7d:
                prior_total += cost

    if n_logs == 0:
        return None

    delta = last_total - prior_total
    sign = "+" if delta >= 0 else ""
    severity = "warn" if delta > 1.0 else "info"
    # Escape `$` as `\$` so Streamlit's markdown renderer doesn't
    # interpret `$...$` as inline LaTeX (which makes the dollar amounts
    # render as italic math).
    bullets = [
        f"This week: \\${last_total:.3f}",
        f"Prior week: \\${prior_total:.3f}",
        f"Δ: {sign}\\${delta:.3f}",
    ]
    return BriefSection(
        title="Anthropic cost (last 7d)",
        summary=f"\\${last_total:.2f} this week vs \\${prior_total:.2f} prior.",
        bullets=bullets,
        severity=severity,
    )


# ─────────────────────────── assembly ───────────────────────────


def assemble_brief(
    *,
    home: Optional[pathlib.Path] = None,
    since_hours: int = 24,
) -> MorningBrief:
    """One call returns everything ui.py needs to render the homepage."""
    home = home or pathlib.Path.home()
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=since_hours)

    sections: list[BriefSection] = []
    sections.append(_overnight_activity(home, since))
    hitl_section, n_pending = _hitl_pending(home)
    sections.append(hitl_section)
    anom_section, n_anom = _anomalies(home, since)
    sections.append(anom_section)
    cost = _cost_summary(home)
    if cost is not None:
        sections.append(cost)

    return MorningBrief(
        generated_at=now.isoformat(),
        window_hours=since_hours,
        sections=sections,
        total_pending_hitl=n_pending,
        total_anomalies=n_anom,
    )
