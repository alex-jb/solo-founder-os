"""Tests for notifier + brief modules."""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.notifier import (
    NtfyNotifier, TelegramNotifier, SlackNotifier, fan_out, MAX_MESSAGE_CHARS,
)
from solo_founder_os.brief import compose_brief, has_critical
from solo_founder_os.source import MetricSample, SourceReport


def _ok():
    fake = MagicMock(); fake.status = 200
    fake.__enter__ = lambda s: s; fake.__exit__ = lambda *a: None
    return fake


def _report(source="x", metrics=None, error=None):
    return SourceReport(source=source,
                         fetched_at=datetime.now(timezone.utc),
                         metrics=metrics or [], error=error)


# ─── Ntfy ────────────────────────────────────────────────

def test_ntfy_unconfigured(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert NtfyNotifier().configured is False
    assert NtfyNotifier().send("x") is False


def test_ntfy_send_with_priority(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    with patch("urllib.request.urlopen", return_value=_ok()) as up:
        ok = NtfyNotifier().send("body", title="hi", priority="urgent")
    assert ok
    req = up.call_args[0][0]
    assert req.headers["Priority"] == "5"


def test_ntfy_truncates(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    big = "A" * (MAX_MESSAGE_CHARS + 5000)
    with patch("urllib.request.urlopen", return_value=_ok()) as up:
        NtfyNotifier().send(big)
    assert len(up.call_args[0][0].data) <= MAX_MESSAGE_CHARS


# ─── Telegram (plain text) ───────────────────────────────

def test_telegram_send_no_parse_mode(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "b")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    with patch("urllib.request.urlopen", return_value=_ok()) as up:
        TelegramNotifier().send("body", title="t")
    payload = json.loads(up.call_args[0][0].data)
    assert "parse_mode" not in payload
    assert payload["text"].startswith("t\n\nbody")


# ─── Slack ───────────────────────────────────────────────

def test_slack_send(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
    with patch("urllib.request.urlopen", return_value=_ok()) as up:
        ok = SlackNotifier().send("body")
    assert ok


# ─── fan_out ─────────────────────────────────────────────

def test_fan_out_partial_configuration(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with patch("urllib.request.urlopen", return_value=_ok()):
        results = fan_out(["ntfy", "telegram"], "msg")
    assert results == {"ntfy": True, "telegram": False}


def test_fan_out_swallows_runtime_errors(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    with patch("urllib.request.urlopen", side_effect=RuntimeError("boom")):
        results = fan_out(["ntfy"], "x")
    assert results == {"ntfy": False}


# ─── brief composer ─────────────────────────────────────

def test_brief_empty():
    text = compose_brief([])
    assert "Brief —" in text
    assert "Generated at" in text


def test_brief_summary_section_when_provided():
    m = MetricSample(name="x", value=1)
    text = compose_brief([_report("v", [m])], summary="all good")
    assert "🧠 Summary" in text
    assert "all good" in text
    assert text.index("🧠 Summary") < text.index("📊 Metrics")


def test_brief_omits_summary_when_empty():
    m = MetricSample(name="x", value=1)
    text = compose_brief([_report("v", [m])], summary="")
    assert "🧠 Summary" not in text


def test_brief_critical_first():
    crit = MetricSample(name="x", value=1, severity="critical", note="db down")
    info = MetricSample(name="y", value=2, note="ok")
    text = compose_brief([_report("vercel", [crit, info])])
    assert text.index("🚨 Critical") < text.index("📊 Metrics")


def test_brief_failed_source_at_bottom():
    text = compose_brief([_report("vercel", error="API down")])
    assert "Sources unavailable" in text
    assert "API down" in text


def test_brief_renders_delta_pct():
    m = MetricSample(name="x", value=50, baseline=100, delta_pct=-50.0)
    text = compose_brief([_report("v", [m])])
    assert "-50.0%" in text


# ─── has_critical ───────────────────────────────────────

def test_has_critical_true_for_critical():
    m = MetricSample(name="x", value=1, severity="critical")
    assert has_critical([_report("v", [m])])


def test_has_critical_true_for_alert():
    m = MetricSample(name="x", value=1, severity="alert")
    assert has_critical([_report("v", [m])])


def test_has_critical_false_for_warn_only():
    m = MetricSample(name="x", value=1, severity="warn")
    assert not has_critical([_report("v", [m])])
