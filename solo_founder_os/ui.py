"""sfos-ui — local Streamlit dashboard for the 10-agent stack.

The pain this fixes: with 10 agents writing logs, examples, eval
reports, and HITL drafts to scattered ~/.dotdirs, the operator can't
see who's working on what without manually grepping. sfos-retro
generates a weekly digest but nothing surfaces the day-to-day.

Architecture (local-first, no cloud):
- Data loaders below are pure functions that read JSONL / JSON / MD
  files in the user's home dir. Testable without Streamlit.
- Rendering uses Streamlit. The optional [ui] extra installs it.
- `sfos-ui` CLI spawns `streamlit run` on this same file so a single
  module ships both the data layer and the UI.

Sections (v1):
  1. Stack Status — last-activity badge per agent
  2. Activity Timeline — chronological feed across the stack
  3. Pending HITL — read-only count + filenames per agent (v1; the
     approve / reject buttons are a v2 follow-up because each agent's
     queue layout differs slightly)
  4. Quality Trends — sfos-eval mean scores per skill
  5. Cron Log Tail — last N lines of ~/.solo-founder-os/cron-logs/

Why not Phoenix / Langfuse / LangSmith: those observe LLM CALLS
(latency, tokens, prompts). This dashboard observes AGENT TASKS
(what each agent did today, was it in queue, did it succeed).
Different layer of abstraction; both are useful but this is the one
the operator actually wants when they ask "what's running right now?".
"""
from __future__ import annotations
import argparse
import json
import pathlib
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


# Keep in sync with cross_agent_report.KNOWN_AGENT_DIRS.
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


# ──────────────────────────── Data layer ────────────────────────────


@dataclass(frozen=True)
class ActivityRow:
    ts: str
    agent: str
    kind: str       # "reflexion" | "eval" | "proposal"
    task: str
    outcome: str    # for reflexion: SUCCESS/FAILED/PARTIAL; else ""
    summary: str
    source_path: str


def _safe_load_jsonl(path: pathlib.Path, limit: int = 500) -> list[dict]:
    """Tail-of-file scan, swallow malformed rows. Returns newest-last."""
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


def scan_reflexions(
    *, home: Optional[pathlib.Path] = None, per_agent_limit: int = 100,
) -> list[ActivityRow]:
    """Walk every known agent dir's reflections.jsonl. Newest-last."""
    home = home or pathlib.Path.home()
    rows: list[ActivityRow] = []
    for slug in KNOWN_AGENT_DIRS:
        path = home / slug / "reflections.jsonl"
        for entry in _safe_load_jsonl(path, limit=per_agent_limit):
            rows.append(ActivityRow(
                ts=str(entry.get("ts", "")),
                agent=slug,
                kind="reflexion",
                task=str(entry.get("task", "?"))[:60],
                outcome=str(entry.get("outcome", "")),
                summary=str(entry.get("verbatim_signal", ""))[:200],
                source_path=str(path),
            ))
    rows.sort(key=lambda r: r.ts)
    return rows


def scan_evals(
    *, home: Optional[pathlib.Path] = None,
) -> list[dict]:
    """Read every JSON in ~/.solo-founder-os/evals/. Returns each report
    as-is (skill / ts / mean_overall etc) sorted oldest-first."""
    home = home or pathlib.Path.home()
    base = home / ".solo-founder-os" / "evals"
    if not base.exists():
        return []
    out: list[dict] = []
    for p in sorted(base.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def scan_proposals(
    *, home: Optional[pathlib.Path] = None,
) -> list[dict]:
    """Parse evolver-proposals/*.md frontmatter — { agent, task,
    target_file, occurrences, generated_at, path }."""
    home = home or pathlib.Path.home()
    base = home / ".solo-founder-os" / "evolver-proposals"
    if not base.exists():
        return []
    out: list[dict] = []
    for p in sorted(base.glob("*.md"), reverse=True):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = {"path": str(p), "filename": p.name}
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end > 0:
                for line in text[4:end].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
        out.append(meta)
    return out


def scan_pending_queues(
    *, home: Optional[pathlib.Path] = None,
) -> dict[str, list[str]]:
    """For each agent, list pending HITL filenames. Walks both common
    layouts: ~/.<agent>/queue/pending/*.md AND
    ~/.<agent>/queue/pending/  (some older agents nest deeper)."""
    home = home or pathlib.Path.home()
    out: dict[str, list[str]] = {}
    for slug in KNOWN_AGENT_DIRS:
        agent_root = home / slug
        if not agent_root.exists():
            continue
        # Standard layout
        pending_dirs = list(agent_root.glob("queue/pending"))
        # Nested marketing-agent layout
        pending_dirs += list(agent_root.glob("queue/*/pending"))
        files: list[str] = []
        for d in pending_dirs:
            if d.is_dir():
                files.extend(sorted(p.name for p in d.glob("*.md")))
        if files:
            out[slug] = files
    return out


def scan_cron_logs(
    *, home: Optional[pathlib.Path] = None, tail_lines: int = 50,
) -> dict[str, list[str]]:
    """Read the last N lines of every ~/.solo-founder-os/cron-logs/*.log."""
    home = home or pathlib.Path.home()
    base = home / ".solo-founder-os" / "cron-logs"
    if not base.exists():
        return {}
    out: dict[str, list[str]] = {}
    for p in sorted(base.glob("*.log")):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()[-tail_lines:]
        except OSError:
            continue
        if lines:
            out[p.name] = lines
    return out


def stack_status(
    *, home: Optional[pathlib.Path] = None,
) -> list[dict]:
    """One row per agent with a freshness badge. Reads each agent's
    reflections.jsonl mtime (or last-row ts if file is empty/recent)."""
    home = home or pathlib.Path.home()
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for slug in KNOWN_AGENT_DIRS:
        path = home / slug / "reflections.jsonl"
        last_ts: Optional[datetime] = None
        n_rows = 0
        if path.exists():
            rows = _safe_load_jsonl(path, limit=200)
            n_rows = len(rows)
            for r in reversed(rows):
                ts = r.get("ts")
                if ts:
                    try:
                        last_ts = datetime.fromisoformat(str(ts))
                        break
                    except ValueError:
                        continue
        if last_ts is None:
            badge = "🔴 never"
            age_h = None
        else:
            delta = now - last_ts
            age_h = delta.total_seconds() / 3600
            if age_h < 24:
                badge = "✅ active"
            elif age_h < 24 * 7:
                badge = "🟡 idle"
            else:
                badge = "🔴 stale"
        out.append({
            "agent": slug,
            "badge": badge,
            "last_ts": last_ts.isoformat() if last_ts else "",
            "age_hours": round(age_h, 1) if age_h is not None else None,
            "n_recent_rows": n_rows,
        })
    return out


# ──────────────────────────── Streamlit rendering ────────────────────────────


def render_dashboard() -> None:
    """Top-level Streamlit body — only called inside `streamlit run`."""
    import streamlit as st

    st.set_page_config(
        page_title="SFOS Stack",
        page_icon="🧰",
        layout="wide",
    )

    st.title("Solo Founder OS — stack dashboard")
    home_dir = pathlib.Path.home()
    st.caption(f"Reading from `{home_dir}` · "
                f"refreshed {datetime.now().strftime('%H:%M:%S')}")

    refresh_col, _ = st.columns([1, 9])
    if refresh_col.button("↻ Refresh"):
        st.rerun()

    # ── Section 1: stack status ──
    st.header("① Stack status")
    status_rows = stack_status()
    cols = st.columns(2)
    for i, row in enumerate(status_rows):
        col = cols[i % 2]
        with col:
            label = row["badge"]
            age = row["age_hours"]
            if age is None:
                detail = "no activity yet"
            elif age < 1:
                detail = f"{int(age * 60)} min ago · {row['n_recent_rows']} rows"
            elif age < 48:
                detail = f"{age:.1f}h ago · {row['n_recent_rows']} rows"
            else:
                detail = f"{age / 24:.1f}d ago · {row['n_recent_rows']} rows"
            st.markdown(f"**{row['agent']}** — {label}  \n*{detail}*")

    st.divider()

    # ── Section 2: activity timeline ──
    st.header("② Activity timeline")
    refl = scan_reflexions(per_agent_limit=50)
    if not refl:
        st.info("No reflexion rows yet — agents will populate this as "
                 "they run. The launchd cron jobs first fire Sunday 08:00.")
    else:
        # Newest first for timeline display.
        refl_sorted = sorted(refl, key=lambda r: r.ts, reverse=True)[:80]
        for r in refl_sorted:
            color = {"SUCCESS": "🟢", "FAILED": "🔴",
                       "PARTIAL": "🟡"}.get(r.outcome, "⚪")
            ts_short = r.ts[:19].replace("T", " ") if r.ts else "?"
            st.markdown(
                f"`{ts_short}`  {color} **{r.agent}** · "
                f"`{r.task}` — {r.summary}"
            )

    st.divider()

    # ── Section 3: pending HITL ──
    st.header("③ Pending HITL")
    pending = scan_pending_queues()
    if not pending:
        st.info("No pending HITL items across the stack.")
    else:
        for agent, files in pending.items():
            with st.expander(f"{agent} — {len(files)} pending"):
                for f in files:
                    st.code(f, language="text")
                st.caption(
                    f"Approve / reject by `mv` from "
                    f"`~/{agent}/queue/pending/` to "
                    f"`approved/` or `rejected/`. "
                    f"Inline buttons coming in v2."
                )

    st.divider()

    # ── Section 4: quality trends ──
    st.header("④ Quality trends (sfos-eval)")
    evals = scan_evals()
    if not evals:
        st.info("No eval reports yet. First auto-run: Sunday 08:00.")
    else:
        # Group by skill, sort by ts.
        by_skill: dict[str, list[dict]] = {}
        for e in evals:
            by_skill.setdefault(e.get("skill", "?"), []).append(e)
        for skill, runs in sorted(by_skill.items()):
            runs.sort(key=lambda r: r.get("ts", ""))
            scores = [r.get("mean_overall", 0) for r in runs]
            n_examples = runs[-1].get("n_examples", 0)
            last = runs[-1].get("mean_overall", 0)
            trend = ""
            if len(scores) >= 2:
                delta = scores[-1] - scores[-2]
                trend = f"  ({'+' if delta >= 0 else ''}{delta:.2f} vs prev)"
            st.markdown(
                f"**{skill}** — last mean **{last:.2f}** / 5 "
                f"(n={n_examples}){trend}"
            )
            if len(scores) >= 2:
                st.line_chart(scores, height=80)

    st.divider()

    # ── Section 5: cron tail ──
    st.header("⑤ Cron log tail")
    logs = scan_cron_logs(tail_lines=30)
    if not logs:
        st.info("No cron logs yet — first scheduled run Sunday 08:00.")
    else:
        for name, lines in logs.items():
            with st.expander(f"{name}  ({len(lines)} lines)"):
                st.code("\n".join(lines), language="text")


# ──────────────────────────── CLI ────────────────────────────


def _under_streamlit() -> bool:
    """Detect whether this module is being executed inside a
    `streamlit run` subprocess."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="sfos-ui",
        description="Local Streamlit dashboard for the SFOS agent stack.",
    )
    p.add_argument("--port", default="8501",
                    help="Port for the Streamlit server (default 8501).")
    p.add_argument("--no-browser", action="store_true",
                    help="Don't auto-open the browser.")
    args = p.parse_args(argv)

    try:
        import streamlit  # noqa: F401
    except ImportError:
        print(
            "sfos-ui requires Streamlit.\n"
            "Install with: pip install 'solo-founder-os[ui]'",
            file=sys.stderr,
        )
        return 2

    cmd = [
        sys.executable, "-m", "streamlit", "run", __file__,
        "--server.port", str(args.port),
    ]
    if args.no_browser:
        cmd += ["--server.headless", "true"]
    return subprocess.call(cmd)


# When `streamlit run solo_founder_os/ui.py` runs, this module is
# imported and executed top-to-bottom; the guard ensures we render
# only inside that subprocess (not when imported by tests / CLI).
if _under_streamlit():
    render_dashboard()


if __name__ == "__main__":
    sys.exit(main())
