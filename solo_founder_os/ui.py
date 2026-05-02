"""sfos-ui — local Streamlit dashboard for the 10-agent stack.

v2 (2026-05-02): research-driven 4-tab redesign.

Research findings that shaped this:
- Real solo founders (Pieter Levels, YC W26, "20+ agents" devs) spend
  ~30 min/day on the stack across 2 batches — they read a morning
  brief + clear an HITL inbox, NOT watch a real-time dashboard.
- Agent sprawl is the #1 complaint of 2026 (96% have it, 12% have a
  fix). The cure is ONE inbox across all agents, not N dashboards.
- Streamlit `st.fragment(run_every=…)` gives "live feel" via 3-second
  local-dir polling; WebSocket is overkill at SFOS volumes (5-15
  HITL items/wk).
- "Agent-to-agent chat" misrepresents SFOS: the agents communicate
  via files, asynchronously. A vertical timeline of file-handoff
  events captures the actual topology; chat bubbles would lie.

Tab layout (priority order):
  🏠 Morning Brief — homepage; what happened overnight, what needs you,
                      anomalies, cost. Driven by morning_brief.py.
  📥 Inbox        — split-pane HITL: list left (grouped by agent),
                      markdown right, [Approve][Reject] buttons →
                      HitlQueue.move(). Auto-refresh via st.fragment.
  🔀 Stack Flow   — vertical timeline of file events grouped by hour.
                      Cross-agent reflexions/evals/proposals/HITL/bus.
  📊 Status       — v1 sections (stack badges, history, quality trends,
                      cron tail) folded under one tab.

CLI is unchanged: `sfos-ui` spawns `streamlit run` on this same file.
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

from .hitl_queue import APPROVED, REJECTED, HitlQueue, parse_frontmatter
from .preference import log_edit


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
    kind: str
    task: str
    outcome: str
    summary: str
    source_path: str


def _safe_load_jsonl(path: pathlib.Path, limit: int = 500) -> list[dict]:
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


@dataclass(frozen=True)
class PendingItem:
    """One inbox row. `path` is absolute so HitlQueue.move can act on it."""
    agent: str
    filename: str
    path: pathlib.Path
    queue_root: pathlib.Path  # parent of pending/, for HitlQueue init


def scan_pending_items(
    *, home: Optional[pathlib.Path] = None,
) -> list[PendingItem]:
    """Return concrete PendingItem objects (with absolute paths) so the
    UI can call HitlQueue.move on click. Newest-first."""
    home = home or pathlib.Path.home()
    out: list[PendingItem] = []
    for slug in KNOWN_AGENT_DIRS:
        agent_root = home / slug
        if not agent_root.exists():
            continue
        # Standard + nested marketing layouts.
        pending_dirs = list(agent_root.glob("queue/pending"))
        pending_dirs += list(agent_root.glob("queue/*/pending"))
        for d in pending_dirs:
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.md")):
                out.append(PendingItem(
                    agent=slug,
                    filename=p.name,
                    path=p,
                    queue_root=d.parent,  # one level above pending/
                ))
    out.sort(key=lambda x: x.filename, reverse=True)
    return out


def scan_pending_queues(
    *, home: Optional[pathlib.Path] = None,
) -> dict[str, list[str]]:
    """Back-compat: kept so v1 tests + status tab keep working."""
    items = scan_pending_items(home=home)
    out: dict[str, list[str]] = {}
    for it in items:
        out.setdefault(it.agent, []).append(it.filename)
    # Restore alpha order per agent (scan_pending_items returned newest-first).
    for k in out:
        out[k] = sorted(out[k])
    return out


def scan_cron_logs(
    *, home: Optional[pathlib.Path] = None, tail_lines: int = 50,
) -> dict[str, list[str]]:
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


# ──────────────────────────── HITL action ────────────────────────────


def act_on_pending(item: PendingItem, *, verdict: str) -> pathlib.Path:
    """Approve or reject a pending HITL item via HitlQueue.move.
    Returns the new path. Raises ValueError on bad verdict."""
    if verdict not in (APPROVED, REJECTED):
        raise ValueError(f"verdict must be approved/rejected, got {verdict!r}")
    q = HitlQueue(item.queue_root)
    return q.move(item.path, to=verdict)


# ──────────────────────────── ICPL edit-detection ────────────────────────────


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split a `---\\n…\\n---\\nbody` markdown into (frontmatter_block, body).

    Both halves include their separators on the frontmatter side so that
    `frontmatter + body == text` losslessly. Returns ('', text) when the
    document has no frontmatter."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    return text[: end + 5], text[end + 5:]


def infer_task(frontmatter: dict, agent_slug: str) -> str:
    """Pick a stable task identifier from the markdown frontmatter for
    ICPL bookkeeping. Heuristic, ordered:

      1. explicit `task:` field (the canonical SFOS convention)
      2. `platform:` (marketing-agent posts: x / linkedin / reddit)
      3. `kind:` (some HITL queues use this)
      4. fallback: '<agent-slug-without-dot>-draft'

    Different (agent, task) tuples accumulate independent preference
    pools, so the choice matters for ICPL pre-amble retrieval."""
    for key in ("task", "platform", "kind"):
        v = frontmatter.get(key)
        if v:
            return str(v)[:80]
    return f"{agent_slug.lstrip('.')}-draft"[:80]


def approve_with_edit(
    item: PendingItem,
    *,
    edited_text: str,
    original_text: str,
) -> tuple[pathlib.Path, bool]:
    """The inbox-side equivalent of "user clicked Approve on possibly-
    edited content."

    1. If `edited_text` differs from `original_text`, persist the edit
       to disk before moving (so the approved/ copy reflects what the
       user actually approved).
    2. If the BODY portion differs, log an ICPL preference pair so
       future drafts of the same (agent, task) can pick up the user's
       voice as few-shot exemplars.
    3. Always move the file to approved/.

    Returns (new_path, was_edited).
    """
    was_edited = edited_text != original_text
    if was_edited:
        item.path.write_text(edited_text, encoding="utf-8")
        # Body-only diff drives the preference signal — frontmatter
        # changes (timestamps, etc) are noise for ICPL.
        _, orig_body = split_frontmatter(original_text)
        new_fm_text, new_body = split_frontmatter(edited_text)
        if orig_body != new_body and orig_body and new_body:
            try:
                fm = parse_frontmatter(new_fm_text + "body")
            except Exception:
                fm = {}
            task = infer_task(fm, item.agent)
            log_edit(
                item.agent,
                task,
                original=orig_body,
                edited=new_body,
                context={
                    "filename": item.filename,
                    "queue_root": str(item.queue_root),
                },
                note="approved via sfos-ui inbox",
            )
    new_path = act_on_pending(item, verdict=APPROVED)
    return new_path, was_edited


# ──────────────────────────── Streamlit rendering ────────────────────────────


def _render_morning_brief() -> None:
    """Tab: 🏠 Morning Brief — research-driven homepage."""
    import streamlit as st

    from .morning_brief import assemble_brief

    brief = assemble_brief(since_hours=24)

    # Top-line metrics
    col1, col2, col3 = st.columns(3)
    col1.metric("HITL pending", brief.total_pending_hitl)
    col2.metric("Anomalies", brief.total_anomalies)
    col3.metric("Window", f"{brief.window_hours}h")

    st.caption(f"Generated {brief.generated_at[:19].replace('T', ' ')} UTC")

    # Sections
    sev_emoji = {"info": "ℹ️", "warn": "🟡", "alert": "🔴"}
    for section in brief.sections:
        emoji = sev_emoji.get(section.severity, "ℹ️")
        st.subheader(f"{emoji} {section.title}")
        st.markdown(f"**{section.summary}**")
        for b in section.bullets:
            st.markdown(f"- {b}")


def _render_inbox() -> None:
    """Tab: 📥 Inbox — split-pane HITL with approve/reject buttons.

    Polled every 3s via st.fragment so the list reflects new pending
    items + post-action state without manual refresh.
    """
    import streamlit as st

    @st.fragment(run_every=3)
    def _inbox_body() -> None:
        items = scan_pending_items()
        if not items:
            st.info("Inbox zero. Nothing pending across the stack.")
            return

        # Group by agent for the left list
        by_agent: dict[str, list[PendingItem]] = {}
        for it in items:
            by_agent.setdefault(it.agent, []).append(it)

        list_col, detail_col = st.columns([1, 2])

        # Selection state — survives reruns within the fragment.
        if "inbox_selected" not in st.session_state:
            st.session_state.inbox_selected = items[0].path
        sel_path = st.session_state.inbox_selected

        # Left list — grouped by agent.
        with list_col:
            st.caption(f"{len(items)} pending across {len(by_agent)} agents")
            for agent in sorted(by_agent):
                with st.expander(f"{agent} ({len(by_agent[agent])})",
                                   expanded=True):
                    for it in by_agent[agent]:
                        is_selected = (it.path == sel_path)
                        label = ("👉 " if is_selected else "")  + it.filename
                        if st.button(label, key=f"sel-{it.path}",
                                       use_container_width=True):
                            st.session_state.inbox_selected = it.path
                            st.rerun()

        # Right detail panel.
        with detail_col:
            sel_item = next((it for it in items if it.path == sel_path), None)
            if sel_item is None:
                # Selection was approved/rejected on a previous tick.
                # Fall back to the first available item.
                sel_item = items[0]
                st.session_state.inbox_selected = sel_item.path

            st.markdown(f"### `{sel_item.agent}` / {sel_item.filename}")

            try:
                original_text = sel_item.path.read_text(encoding="utf-8")
            except OSError as e:
                st.error(f"Could not read file: {e}")
                original_text = ""

            # Editable buffer. Edits before approval are how ICPL
            # learns Alex's voice — the (original_body, edited_body)
            # diff feeds preference_preamble on the next draft.
            edit_key = f"edit-{sel_item.path}"
            edited_text = st.text_area(
                "Markdown (edit before approving — diff feeds ICPL)",
                value=original_text,
                key=edit_key,
                height=400,
            )

            # Action buttons.
            ac, rc, _ = st.columns([1, 1, 4])
            if ac.button("✅ Approve", key=f"appr-{sel_item.path}"):
                try:
                    new_path, was_edited = approve_with_edit(
                        sel_item,
                        edited_text=edited_text,
                        original_text=original_text,
                    )
                    if was_edited:
                        st.success(
                            f"Approved (with edits) → {new_path.name}"
                        )
                        st.caption(
                            "ICPL pair logged — future drafts of this "
                            "(agent, task) get your edit as a few-shot "
                            "exemplar."
                        )
                    else:
                        st.success(f"Approved → {new_path.name}")
                    st.session_state.pop("inbox_selected", None)
                    st.session_state.pop(edit_key, None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Move failed: {e}")
            if rc.button("❌ Reject", key=f"rej-{sel_item.path}"):
                try:
                    new_path = act_on_pending(sel_item, verdict=REJECTED)
                    st.warning(f"Rejected → {new_path.name}")
                    st.session_state.pop("inbox_selected", None)
                    st.session_state.pop(edit_key, None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Move failed: {e}")

            # Read-only preview below the editor — useful when the
            # markdown contains code blocks or lists that don't render
            # well as raw text.
            with st.expander("Rendered preview"):
                st.markdown(edited_text)

    _inbox_body()


def _render_stack_flow() -> None:
    """Tab: 🔀 Stack Flow — vertical timeline of cross-agent file events."""
    import streamlit as st

    from .stack_flow import assemble_timeline, group_by_hour

    window = st.selectbox(
        "Window", options=[24, 72, 168, 720], index=2,
        format_func=lambda h: (
            f"last {h}h" if h < 168 else (
                "last 7 days" if h == 168 else "last 30 days"
            )
        ),
    )
    events = assemble_timeline(since_hours=window)
    if not events:
        st.info("No events in window. The stack is quiet — or hasn't run yet.")
        return

    grouped = group_by_hour(events)
    kind_emoji = {"reflexion": "🔁", "eval": "📊", "proposal": "🔧",
                    "hitl": "📥", "bus": "📡"}
    sev_color = {"info": "ℹ️", "warn": "🟡", "alert": "🔴"}

    for hour, evs in grouped.items():
        with st.expander(f"{hour}  ({len(evs)} events)",
                           expanded=(hour == next(iter(grouped)))):
            for e in evs:
                emoji = kind_emoji.get(e.kind, "•")
                sev = sev_color.get(e.severity, "")
                ts_short = e.ts[11:19] if len(e.ts) >= 19 else e.ts
                st.markdown(
                    f"`{ts_short}`  {emoji} {sev} **{e.agent}** · {e.summary}"
                )


def _render_status() -> None:
    """Tab: 📊 Status — v1 sections folded together."""
    import streamlit as st

    st.subheader("Stack status")
    rows = stack_status()
    cols = st.columns(2)
    for i, row in enumerate(rows):
        col = cols[i % 2]
        with col:
            age = row["age_hours"]
            if age is None:
                detail = "no activity yet"
            elif age < 1:
                detail = f"{int(age * 60)} min ago · {row['n_recent_rows']} rows"
            elif age < 48:
                detail = f"{age:.1f}h ago · {row['n_recent_rows']} rows"
            else:
                detail = f"{age / 24:.1f}d ago · {row['n_recent_rows']} rows"
            st.markdown(f"**{row['agent']}** — {row['badge']}  \n*{detail}*")

    st.divider()

    st.subheader("Quality trends (sfos-eval)")
    evals = scan_evals()
    if not evals:
        st.info("No eval reports yet. First auto-run: Sunday 08:00.")
    else:
        by_skill: dict[str, list[dict]] = {}
        for e in evals:
            by_skill.setdefault(e.get("skill", "?"), []).append(e)
        for skill, runs in sorted(by_skill.items()):
            runs.sort(key=lambda r: r.get("ts", ""))
            scores = [r.get("mean_overall", 0) for r in runs]
            n = runs[-1].get("n_examples", 0)
            last = runs[-1].get("mean_overall", 0)
            trend = ""
            if len(scores) >= 2:
                delta = scores[-1] - scores[-2]
                trend = f"  ({'+' if delta >= 0 else ''}{delta:.2f} vs prev)"
            st.markdown(
                f"**{skill}** — last mean **{last:.2f}** / 5 (n={n}){trend}"
            )
            if len(scores) >= 2:
                st.line_chart(scores, height=80)

    st.divider()

    st.subheader("Cron log tail")
    logs = scan_cron_logs(tail_lines=30)
    if not logs:
        st.info("No cron logs yet — first scheduled run Sunday 08:00.")
    else:
        for name, lines in logs.items():
            with st.expander(f"{name}  ({len(lines)} lines)"):
                st.code("\n".join(lines), language="text")


def render_dashboard() -> None:
    """Top-level Streamlit body — only called inside `streamlit run`."""
    import streamlit as st

    st.set_page_config(
        page_title="SFOS Stack",
        page_icon="🧰",
        layout="wide",
    )
    st.title("Solo Founder OS — stack")
    st.caption(
        "Local dashboard. Reads JSONL/JSON/MD files in `~/`. "
        "Auto-refresh on Inbox tab; other tabs use the rerun button."
    )

    tabs = st.tabs([
        "🏠 Morning Brief",
        "📥 Inbox",
        "🔀 Stack Flow",
        "📊 Status",
    ])

    with tabs[0]:
        _render_morning_brief()
    with tabs[1]:
        _render_inbox()
    with tabs[2]:
        _render_stack_flow()
    with tabs[3]:
        _render_status()


# ──────────────────────────── CLI ────────────────────────────


def _under_streamlit() -> bool:
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


if _under_streamlit():
    render_dashboard()


if __name__ == "__main__":
    sys.exit(main())
