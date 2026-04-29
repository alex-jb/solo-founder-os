"""Tests for source module."""
from __future__ import annotations
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.source import (
    Source, SourceReport, MetricSample, SEVERITY_ORDER,
)


def test_severity_order_is_canonical():
    """Pinning the exact order — agents pattern-match on these strings."""
    assert SEVERITY_ORDER == ["info", "warn", "alert", "critical"]


def test_metric_sample_defaults():
    m = MetricSample(name="x", value=1)
    assert m.severity == "info"
    assert m.baseline is None
    assert m.delta_pct is None
    assert m.note == ""
    assert m.raw == {}


def test_source_report_defaults():
    r = SourceReport(source="x", fetched_at=datetime.now(timezone.utc))
    assert r.metrics == []
    assert r.error is None


def test_source_base_class_raises_not_implemented():
    s = Source()
    try:
        s.fetch()
    except NotImplementedError:
        return
    raise AssertionError("Source.fetch() should raise NotImplementedError")


def test_source_default_configured_true():
    """Subclasses opt in to checking creds; default is configured=True."""
    assert Source().configured is True
