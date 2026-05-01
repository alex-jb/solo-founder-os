"""Autopsy — explain why a specific output (post / email / brief)
underperformed. Cross-agent.

Lifted from marketing-agent's autopsy.py — same logic, generalized
through three small Protocols so the same engine works for
marketing-agent (X likes), vc-outreach (cold-email reply rate),
funnel-analytics (daily-brief shares), customer-discovery (Reddit
upvotes on the seed post), etc.

The agent provides three plug-ins:

  - `MetricSource` (required) — knows how to fetch the output's
    metadata, its peak metric value, and a per-channel peer baseline.
  - `CriticHook` (optional) — heuristic-only body critic. Skip if the
    output isn't text-shaped (e.g. ROC curves).
  - `BestTimeHook` (optional) — produces an "ideal posting hour" so
    autopsy can flag bad-timing.

The engine itself is offline. It compares the output's metric to the
peer baseline, runs the optional critic and best-time checks, applies
length-vs-norm rules, and produces a structured dict + markdown
renderer.

Usage:
    from solo_founder_os.autopsy import autopsy, render_markdown
    report = autopsy(
        "2050112010461778080",
        metric_source=my_x_metric_source,
        critic=my_heuristic_critic,
        best_time=my_best_time,
        short_body_thresholds={"x": 80},  # X posts < 80 chars suspect
    )
    print(render_markdown(report))
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable


# ───────────────── Protocols ─────────────────


@runtime_checkable
class MetricSource(Protocol):
    """Plug-in: provides post data + engagement metrics from your store."""

    def fetch_post(self, post_id: str) -> Optional[dict]:
        """Return {channel, body, posted_at_iso} or None if missing.

        Extra keys are passed through to the report. Required keys:
          - channel: str (e.g. "x", "linkedin", "email", "brief")
          - body: str (full text or preview, used for critic + length check)
          - posted_at_iso: str (ISO 8601, used for best-time check)
        """
        ...  # pragma: no cover

    def fetch_metric(self, post_id: str, metric: str) -> int:
        """Peak metric value for this post (e.g. likes, opens, upvotes)."""
        ...  # pragma: no cover

    def peer_baseline(self, channel: str, metric: str,
                          *, limit: int = 30) -> dict:
        """Return {'median': float, 'p25': float, 'p75': float, 'n': int}."""
        ...  # pragma: no cover


@runtime_checkable
class CriticHook(Protocol):
    """Optional plug-in: text-only heuristic critic."""

    def score_body(self, body: str) -> "tuple[float, list[str]]":
        """Return (score in [0, 10], list of reason strings).

        Score < 7 typically means "structural problems"; reasons explain.
        """
        ...  # pragma: no cover


@runtime_checkable
class BestTimeHook(Protocol):
    """Optional plug-in: knows the ideal hour-of-week per channel."""

    def optimal_time(self, channel: str, metric: str
                       ) -> "tuple[int, int, str]":
        """Return (weekday 0-6, hour 0-23, source-name like 'cdf-of-50')."""
        ...  # pragma: no cover


# ───────────────── Engine ─────────────────


_DEFAULT_SHORT_BODY = {"x": 80}


def autopsy(
    post_id: str,
    *,
    metric_source: MetricSource,
    metric: str = "like",
    critic: Optional[CriticHook] = None,
    best_time: Optional[BestTimeHook] = None,
    short_body_thresholds: Optional[dict] = None,
    peer_limit: int = 30,
) -> dict:
    """Generate an autopsy report. See module docstring."""
    short_thresholds = (short_body_thresholds
                          if short_body_thresholds is not None
                          else _DEFAULT_SHORT_BODY)

    post = metric_source.fetch_post(post_id)
    if not post:
        return {
            "post": None,
            "diagnoses": [f"post {post_id} not found in metric source"],
            "recommendations": [],
        }

    channel = post.get("channel") or post.get("platform") or "unknown"
    body = post.get("body") or post.get("body_preview") or ""
    posted_at_iso = post.get("posted_at_iso") or post.get("posted_at") or ""

    eng = int(metric_source.fetch_metric(post_id, metric) or 0)
    baseline = metric_source.peer_baseline(channel, metric, limit=peer_limit)

    median = float(baseline.get("median", 0.0))
    underperf = 0.0
    if median > 0:
        underperf = max(0.0, (median - eng) / median)

    diagnoses: list[str] = []
    recs: list[str] = []

    # 1. Engagement vs peers
    n_peers = int(baseline.get("n", 0))
    if n_peers >= 5:
        if eng < median * 0.5 and median > 0:
            diagnoses.append(
                f"Engagement ({eng}) is well below {channel} median "
                f"({median:.0f}, based on last {n_peers} posts).")
            recs.append(
                "Try a different framing — your variant bandit may have "
                "better-performing arms.")
        elif eng < median:
            diagnoses.append(
                f"Engagement ({eng}) below median ({median:.0f}) but "
                f"within normal variance.")
    else:
        diagnoses.append(
            f"Only {n_peers} peer post(s) on {channel} — too few "
            f"to compute a reliable median; benchmark unstable.")

    # 2. Optional critic
    critic_payload: dict = {}
    if critic is not None and body:
        try:
            score, reasons = critic.score_body(body)
        except Exception:
            score, reasons = 0.0, []
        critic_payload = {"score": round(float(score), 2),
                            "reasons": list(reasons)}
        if reasons:
            diagnoses.append(
                f"Critic flagged structural issues (score {score}/10): "
                + "; ".join(reasons[:3]))
            recs.append("Strip flagged patterns and try generating again.")

    # 3. Optional best-time check
    if best_time is not None and posted_at_iso:
        try:
            wd_best, h_best, src = best_time.optimal_time(channel, metric)
            posted_dt = datetime.fromisoformat(posted_at_iso)
            if (wd_best, h_best) != (posted_dt.weekday(), posted_dt.hour):
                wkdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                diagnoses.append(
                    f"Posted {wkdays[posted_dt.weekday()]} "
                    f"{posted_dt.hour:02d}:00 UTC but best slot ({src}) "
                    f"is {wkdays[wd_best]} {h_best:02d}:00.")
                recs.append(
                    f"Schedule the next {channel} post for the optimal "
                    f"{wkdays[wd_best]} {h_best:02d}:00 slot.")
        except Exception:
            pass

    # 4. Length-vs-norm
    blen = len(body)
    floor = short_thresholds.get(channel)
    if floor is not None and blen < floor:
        diagnoses.append(
            f"Body is short ({blen} chars). {channel} posts < {floor} "
            f"chars often under-perform: too thin to spark engagement.")
        recs.append("Add a concrete number, link, or specific detail.")

    return {
        "post": post,
        "channel": channel,
        "engagement": eng,
        "metric": metric,
        "baseline": baseline,
        "underperformance": round(underperf, 2),
        "critic": critic_payload,
        "diagnoses": diagnoses,
        "recommendations": recs,
    }


def render_markdown(report: dict) -> str:
    """Render an autopsy dict as a markdown report."""
    if not report.get("post"):
        diags = "\n".join(f"- {d}" for d in report.get("diagnoses", []))
        return f"# Post-mortem\n\n**Post not found.**\n\n{diags}\n"
    p = report["post"]
    channel = report["channel"]
    metric = report.get("metric", "like")
    pid = p.get("external_id") or p.get("id") or "?"
    posted_at = p.get("posted_at_iso") or p.get("posted_at") or "?"
    body = p.get("body") or p.get("body_preview") or ""
    n_peers = report["baseline"].get("n", 0)
    median = report["baseline"].get("median", 0.0)

    lines = [
        f"# Post-mortem — {channel} · {pid}",
        "",
        f"*Posted {posted_at} · {metric}: **{report['engagement']}** "
        f"vs {channel} median {median:.0f} ({n_peers} peers)*",
        "",
        "## Body excerpt",
        "```",
        body[:600],
        "```",
        "",
    ]
    if report.get("critic"):
        crit = report["critic"]
        lines.extend([f"## Critic score: {crit['score']}/10", ""])
        for r in crit["reasons"]:
            lines.append(f"- {r}")
        if not crit["reasons"]:
            lines.append("- (no structural issues detected)")
        lines.append("")
    lines.extend(["## Diagnoses", ""])
    for d in report["diagnoses"]:
        lines.append(f"- {d}")
    lines.extend(["", "## Recommendations", ""])
    if not report["recommendations"]:
        lines.append("- (engagement within normal variance — no specific fix)")
    for r in report["recommendations"]:
        lines.append(f"- {r}")
    return "\n".join(lines)
