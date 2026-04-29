"""7-day rolling baseline + auto-rotation. Generic over any source.

This is the funnel-analytics-agent baseline module, lifted with one
generalization: the log file path is parameterized so multiple agents can
each have their own baseline (e.g. ~/.funnel-analytics-agent/baseline.jsonl
vs ~/.cost-audit-agent/baseline.jsonl).

Storage: append-only JSONL, one row per (run, source, metric).
Rotation: when the live file passes ROTATE_THRESHOLD_BYTES (10MB),
samples older than ROTATE_KEEP_DAYS get gzipped to baseline-<yyyy-mm>.jsonl.gz
alongside.

Anomaly: delta_pct < -50% on a metric currently at "info" severity gets
promoted to "warn". This is opinionated — agents that want different
thresholds can call enrich_with_baseline() then post-process.
"""
from __future__ import annotations
import gzip
import json
import os
import pathlib
import statistics
from datetime import datetime, timezone, timedelta
from typing import Iterable

from .source import MetricSample, SourceReport


BASELINE_WINDOW_DAYS = 7
ANOMALY_DROP_PCT = -50.0
ROTATE_THRESHOLD_BYTES = 10 * 1024 * 1024
ROTATE_KEEP_DAYS = BASELINE_WINDOW_DAYS * 2


def _resolve_log_path(env_var: str | None, default_path: pathlib.Path) -> pathlib.Path:
    """Honor an env-var override (used by tests + custom deployments),
    else fall back to default_path."""
    if env_var:
        override = os.getenv(env_var)
        if override:
            return pathlib.Path(override)
    return default_path


def _load_samples(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _baseline_for(samples: list[dict], source: str, name: str,
                  *, now: datetime | None = None) -> float | None:
    """Median of values for (source, name) within the last 7 days. None if
    fewer than 3 samples — too noisy to call a baseline."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=BASELINE_WINDOW_DAYS)
    values: list[float] = []
    for row in samples:
        if row.get("source") != source or row.get("name") != name:
            continue
        try:
            ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        try:
            values.append(float(row["value"]))
        except Exception:
            continue
    if len(values) < 3:
        return None
    return statistics.median(values)


def enrich_with_baseline(reports: Iterable[SourceReport],
                         *, log_path: pathlib.Path,
                         now: datetime | None = None) -> None:
    """In-place: populate MetricSample.baseline + delta_pct, and promote
    severity to 'warn' on >50% drops vs baseline.

    Numeric metrics only. Baseline must be > 0 for delta to be computed
    (avoids division-by-zero on bootstrap).
    """
    now = now or datetime.now(timezone.utc)
    samples = _load_samples(log_path)
    if not samples:
        return  # bootstrap mode

    for r in reports:
        for m in r.metrics:
            try:
                current = float(m.value)
            except (TypeError, ValueError):
                continue
            base = _baseline_for(samples, r.source, m.name, now=now)
            if base is None or base == 0:
                continue
            delta = (current - base) / base * 100.0
            m.baseline = base
            m.delta_pct = delta
            if delta < ANOMALY_DROP_PCT and m.severity == "info":
                m.severity = "warn"
                drop = abs(delta)
                m.note = (m.note or "") + (
                    f" ⚠ {drop:.0f}% below 7-day median ({base:.0f})")


def _rotate_if_needed(path: pathlib.Path,
                      *, now: datetime | None = None) -> None:
    """Rotate when the file exceeds ROTATE_THRESHOLD_BYTES."""
    if not path.exists():
        return
    try:
        if path.stat().st_size < ROTATE_THRESHOLD_BYTES:
            return
    except Exception:
        return

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ROTATE_KEEP_DAYS)
    keep_lines: list[str] = []
    archive_lines: list[str] = []

    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
            except Exception:
                keep_lines.append(line)
                continue
            if ts >= cutoff:
                keep_lines.append(line)
            else:
                archive_lines.append(line)
    except Exception:
        return

    if not archive_lines:
        return

    try:
        first_archived_ts = datetime.fromisoformat(
            json.loads(archive_lines[0])["ts"].replace("Z", "+00:00"))
        archive_name = f"baseline-{first_archived_ts.strftime('%Y-%m')}.jsonl.gz"
        archive_path = path.parent / archive_name

        existing = b""
        if archive_path.exists():
            with gzip.open(archive_path, "rb") as f:
                existing = f.read()
        new_content = existing + ("\n".join(archive_lines) + "\n").encode()
        with gzip.open(archive_path, "wb") as f:
            f.write(new_content)

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text("\n".join(keep_lines) + "\n" if keep_lines else "")
        tmp_path.replace(path)
    except Exception:
        return


def record_samples(reports: Iterable[SourceReport],
                   *, log_path: pathlib.Path,
                   now: datetime | None = None) -> None:
    """Append the current run's metrics to log_path. Auto-rotates."""
    now = now or datetime.now(timezone.utc)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(log_path, now=now)
        with log_path.open("a") as f:
            for r in reports:
                for m in r.metrics:
                    try:
                        value = float(m.value)
                    except (TypeError, ValueError):
                        continue
                    f.write(json.dumps({
                        "ts": now.isoformat(),
                        "source": r.source,
                        "name": m.name,
                        "value": value,
                    }) + "\n")
    except Exception:
        pass
