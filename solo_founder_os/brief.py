"""Generic markdown brief composer + has_critical helper.

Lifted from funnel-analytics-agent. Same shape: takes a list of
SourceReport, renders critical → alert → warn → metrics-by-source →
unavailable. Optional `summary` string renders at the top (typically a
Claude-generated narrative).
"""
from __future__ import annotations
from datetime import datetime, timezone
from .source import SourceReport, MetricSample


SEVERITY_ICON = {"critical": "🚨", "alert": "❗", "warn": "🟡", "info": "·"}


def compose_brief(reports: list[SourceReport], *,
                  title: str | None = None,
                  summary: str | None = None) -> str:
    """Render a list of SourceReports as one markdown brief.

    Optional `summary` is rendered as the second section (after title),
    before any details. Pass None or "" to skip.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = title or f"Brief — {now}"

    out: list[str] = [f"# {title}", ""]
    if summary:
        out += ["## 🧠 Summary", "", summary.strip(), ""]

    flat: list[tuple[str, MetricSample]] = []
    for r in reports:
        for m in r.metrics:
            flat.append((r.source, m))

    crit = [(s, m) for s, m in flat if m.severity == "critical"]
    alerts = [(s, m) for s, m in flat if m.severity == "alert"]
    warns = [(s, m) for s, m in flat if m.severity == "warn"]

    if crit:
        out += ["## 🚨 Critical", ""]
        for s, m in crit:
            out.append(f"- **[{s}]** {m.note}")
        out.append("")
    if alerts:
        out += ["## ❗ Alerts", ""]
        for s, m in alerts:
            out.append(f"- **[{s}]** {m.note}")
        out.append("")
    if warns:
        out += ["## 🟡 Warnings", ""]
        for s, m in warns:
            out.append(f"- **[{s}]** {m.note}")
        out.append("")

    out += ["## 📊 Metrics by source", ""]
    for r in sorted(reports, key=lambda r: r.source):
        if r.error:
            continue
        out.append(f"### {r.source}")
        if not r.metrics:
            out.append("_(no metrics)_")
            out.append("")
            continue
        for m in r.metrics:
            icon = SEVERITY_ICON.get(m.severity, "·")
            line = f"- {icon} `{m.name}` = **{m.value}**"
            if m.delta_pct is not None:
                sign = "+" if m.delta_pct >= 0 else ""
                line += f" ({sign}{m.delta_pct:.1f}% vs baseline)"
            if m.note:
                line += f" — {m.note}"
            out.append(line)
        out.append("")

    failed = [r for r in reports if r.error]
    if failed:
        out += ["## ⚠️ Sources unavailable", ""]
        for r in failed:
            out.append(f"- **{r.source}** — {r.error}")
        out.append("")

    out += ["---", f"_Generated at {now}_"]
    return "\n".join(out)


def has_critical(reports: list[SourceReport]) -> bool:
    """True if any metric is critical or alert severity."""
    for r in reports:
        for m in r.metrics:
            if m.severity in ("critical", "alert"):
                return True
    return False
