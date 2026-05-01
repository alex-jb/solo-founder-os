"""Tests for baseline module — same behavior as funnel-analytics-agent's
inline version, now generic over log_path."""
from __future__ import annotations
import gzip
import json
import os
import pathlib
import sys
from datetime import datetime, timezone, timedelta


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.source import MetricSample, SourceReport
from solo_founder_os.baseline import (
    enrich_with_baseline, record_samples,
    _baseline_for, _rotate_if_needed,
)
from solo_founder_os import baseline as bl


def _seed(path: pathlib.Path, source: str, name: str,
          values: list[tuple[datetime, float]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for ts, v in values:
            f.write(json.dumps({
                "ts": ts.isoformat(), "source": source, "name": name, "value": v,
            }) + "\n")


def _report(source: str, metrics: list[MetricSample]) -> SourceReport:
    return SourceReport(source=source,
                         fetched_at=datetime.now(timezone.utc),
                         metrics=metrics)


# ─── _baseline_for ─────────────────────────────────────────

def test_baseline_none_with_no_history():
    assert _baseline_for([], "v", "m") is None


def test_baseline_none_with_too_few_samples():
    samples = [{"ts": datetime.now(timezone.utc).isoformat(),
                "source": "v", "name": "m", "value": 1}] * 2
    assert _baseline_for(samples, "v", "m") is None


def test_baseline_median_over_recent():
    now = datetime.now(timezone.utc)
    samples = [
        {"ts": (now - timedelta(days=i)).isoformat(),
         "source": "v", "name": "m", "value": v}
        for i, v in enumerate([10, 20, 30], start=1)
    ]
    assert _baseline_for(samples, "v", "m", now=now) == 20.0


def test_baseline_excludes_old():
    now = datetime.now(timezone.utc)
    samples = [
        {"ts": (now - timedelta(days=1)).isoformat(),
         "source": "v", "name": "m", "value": 10},
        {"ts": (now - timedelta(days=2)).isoformat(),
         "source": "v", "name": "m", "value": 12},
        {"ts": (now - timedelta(days=3)).isoformat(),
         "source": "v", "name": "m", "value": 14},
        {"ts": (now - timedelta(days=15)).isoformat(),  # too old
         "source": "v", "name": "m", "value": 9999},
    ]
    assert _baseline_for(samples, "v", "m", now=now) == 12.0


# ─── enrich_with_baseline ──────────────────────────────────

def test_enrich_no_history_does_nothing(tmp_path):
    log = tmp_path / "missing.jsonl"
    m = MetricSample(name="x", value=10)
    enrich_with_baseline([_report("v", [m])], log_path=log)
    assert m.delta_pct is None
    assert m.severity == "info"


def test_enrich_populates_delta_pct(tmp_path):
    log = tmp_path / "baseline.jsonl"
    now = datetime.now(timezone.utc)
    _seed(log, "v", "x",
          [(now - timedelta(days=i), 100) for i in (1, 2, 3)])
    m = MetricSample(name="x", value=150)
    enrich_with_baseline([_report("v", [m])], log_path=log)
    assert m.baseline == 100.0
    assert m.delta_pct == 50.0


def test_enrich_promotes_severity_on_drop(tmp_path):
    log = tmp_path / "baseline.jsonl"
    now = datetime.now(timezone.utc)
    _seed(log, "v", "x",
          [(now - timedelta(days=i), 100) for i in (1, 2, 3)])
    m = MetricSample(name="x", value=30, severity="info")
    enrich_with_baseline([_report("v", [m])], log_path=log)
    assert m.severity == "warn"
    assert "below 7-day median" in m.note


def test_enrich_does_not_demote_critical(tmp_path):
    log = tmp_path / "baseline.jsonl"
    now = datetime.now(timezone.utc)
    _seed(log, "v", "x",
          [(now - timedelta(days=i), 100) for i in (1, 2, 3)])
    m = MetricSample(name="x", value=30, severity="critical")
    enrich_with_baseline([_report("v", [m])], log_path=log)
    assert m.severity == "critical"  # never demoted


def test_enrich_skips_zero_baseline(tmp_path):
    log = tmp_path / "baseline.jsonl"
    now = datetime.now(timezone.utc)
    _seed(log, "v", "x",
          [(now - timedelta(days=i), 0) for i in (1, 2, 3)])
    m = MetricSample(name="x", value=10)
    enrich_with_baseline([_report("v", [m])], log_path=log)
    assert m.delta_pct is None  # divide by zero avoided


# ─── record_samples ────────────────────────────────────────

def test_record_appends_one_row_per_metric(tmp_path):
    log = tmp_path / "baseline.jsonl"
    metrics = [MetricSample(name="a", value=1), MetricSample(name="b", value=2)]
    record_samples([_report("v", metrics)], log_path=log)
    rows = log.read_text().strip().splitlines()
    assert len(rows) == 2


def test_record_skips_non_numeric(tmp_path):
    log = tmp_path / "baseline.jsonl"
    metrics = [MetricSample(name="bad", value="not_a_number"),
               MetricSample(name="good", value=42)]
    record_samples([_report("v", metrics)], log_path=log)
    rows = log.read_text().strip().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["name"] == "good"


# ─── rotation ──────────────────────────────────────────────

def test_rotate_skips_under_threshold(tmp_path):
    log = tmp_path / "baseline.jsonl"
    log.write_text(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "v", "name": "x", "value": 1,
    }) + "\n")
    before = log.read_text()
    _rotate_if_needed(log)
    assert log.read_text() == before
    assert list(tmp_path.glob("baseline-*.jsonl.gz")) == []


def test_rotate_archives_old(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "ROTATE_THRESHOLD_BYTES", 1)
    log = tmp_path / "baseline.jsonl"
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=30)).isoformat()
    new_ts = (now - timedelta(days=2)).isoformat()
    log.write_text("\n".join([
        json.dumps({"ts": old_ts, "source": "v", "name": "x", "value": 1}),
        json.dumps({"ts": new_ts, "source": "v", "name": "x", "value": 99}),
    ]) + "\n")

    _rotate_if_needed(log, now=now)

    live = log.read_text().strip().splitlines()
    assert len(live) == 1
    assert json.loads(live[0])["value"] == 99

    archives = list(tmp_path.glob("baseline-*.jsonl.gz"))
    assert len(archives) == 1


def test_rotate_appends_to_existing_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "ROTATE_THRESHOLD_BYTES", 1)
    log = tmp_path / "baseline.jsonl"
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=30))
    archive_path = tmp_path / f"baseline-{old_ts.strftime('%Y-%m')}.jsonl.gz"
    with gzip.open(archive_path, "wb") as f:
        f.write(json.dumps({"ts": old_ts.isoformat(),
                              "source": "v", "name": "x", "value": 999}).encode() + b"\n")
    log.write_text(json.dumps({"ts": old_ts.isoformat(),
                                 "source": "v", "name": "x", "value": 1}) + "\n")
    _rotate_if_needed(log, now=now)

    with gzip.open(archive_path, "rb") as f:
        archived = f.read().decode().strip().splitlines()
    assert len(archived) == 2
    values = sorted(json.loads(r)["value"] for r in archived)
    assert values == [1, 999]
