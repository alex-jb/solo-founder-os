"""Source ABC + the dataclasses every agent's fetch path passes around.

Why these specific shapes: after building 7 agents independently, the
unavoidable structure is "one fetch produces N metrics, each with severity
+ optional baseline + optional note". Anything richer (e.g. nested
metrics, time series) is over-design for the v1 use case.

`Source` and `Provider` (in providers.py) are the same shape; the names
diverge by domain — `Source` for live signals (Vercel deploys, PH votes),
`Provider` for billing snapshots (Stripe, AWS, Anthropic). v0.1 ships
Source only; Provider is a thin wrapper added in cost-audit-agent's
migration.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# Severity ladder — used by brief composer, alert mode, baseline promotion.
# Higher index = more severe. Strings are the canonical names; do NOT add
# new severities without bumping the major version (downstream agents
# pattern-match on these literally).
SEVERITY_ORDER = ["info", "warn", "alert", "critical"]


@dataclass
class MetricSample:
    """A single metric reading at one point in time.

    `baseline` and `delta_pct` are populated by the baseline module after
    fetch; sources should leave them as None. Sources DO set `severity`
    and `note` based on their own thresholds.
    """
    name: str                                # e.g. "signup_count_24h"
    value: float | int                       # current value
    baseline: float | int | None = None      # 7-day median (post-enrichment)
    delta_pct: float | None = None           # vs baseline (post-enrichment)
    severity: str = "info"                   # one of SEVERITY_ORDER
    note: str = ""                           # one-line human explanation
    raw: dict = field(default_factory=dict)  # source-specific extras


@dataclass
class SourceReport:
    """Output from one Source.fetch() call."""
    source: str                              # e.g. "vercel"
    fetched_at: datetime
    metrics: list[MetricSample] = field(default_factory=list)
    error: Optional[str] = None              # set when source failed; metrics empty


class Source:
    """Subclass per data provider. Implement fetch() to return a SourceReport.

    Two methods to override:
      - configured (property): True iff env vars / creds are present
      - fetch(): return SourceReport. Should NEVER raise — wrap exceptions
        as SourceReport.error and return.
    """
    name: str = "base"

    @property
    def configured(self) -> bool:
        """Whether this source has the env vars / creds it needs."""
        return True

    def fetch(self) -> SourceReport:
        raise NotImplementedError
