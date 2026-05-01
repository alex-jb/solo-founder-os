"""Tests for sfos-doctor — the cross-stack health-check command."""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os import doctor


def _isolate_env(monkeypatch):
    """Strip every env var the doctor checks so tests start from a clean slate."""
    for k in [
        "ANTHROPIC_API_KEY", "ANTHROPIC_ADMIN_KEY", "ANTHROPIC_ORG_ID",
        "VERCEL_TOKEN", "PH_DEV_TOKEN", "PH_LAUNCH_SLUG",
        "SUPABASE_PERSONAL_ACCESS_TOKEN", "SUPABASE_PROJECT_REF",
        "VIBEX_PROJECT_REF", "REDDIT_CLIENT_ID",
        "SMTP_HOST", "SMTP_USER", "GITHUB_TOKEN",
        "NTFY_TOPIC", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "SLACK_WEBHOOK_URL", "X_API_KEY", "LINKEDIN_ACCESS_TOKEN",
    ]:
        monkeypatch.delenv(k, raising=False)


def test_check_env_required_missing(monkeypatch):
    _isolate_env(monkeypatch)
    r = doctor._check_env("ANTHROPIC_API_KEY", required=True)
    assert r.ok is False
    assert r.severity == "required"


def test_check_env_optional_missing_is_warn(monkeypatch):
    """Unset optional env vars are flagged severity=warn (graceful degrade
    is fine), but `ok` stays True so the agent isn't blocked."""
    _isolate_env(monkeypatch)
    r = doctor._check_env("VERCEL_TOKEN", required=False)
    assert r.ok is True  # optional, so unset is not a hard fail
    assert r.severity == "warn"
    assert "unset" in r.detail


def test_check_env_set_is_masked(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY",
                        "sk-ant-api03-totallyrealkey1234567890ABCDEF")
    r = doctor._check_env("ANTHROPIC_API_KEY", required=True)
    assert r.ok is True
    # Detail should not contain full key
    assert "totallyrealkey" not in r.detail
    assert "…" in r.detail


def test_stack_wide_no_notifier_warns(monkeypatch):
    _isolate_env(monkeypatch)
    results = doctor.check_stack_wide()
    notifier = [r for r in results if "notifier" in r.label][0]
    assert notifier.ok is False
    assert notifier.severity == "warn"


def test_stack_wide_with_ntfy_passes(monkeypatch):
    _isolate_env(monkeypatch)
    monkeypatch.setenv("NTFY_TOPIC", "alex-vibex-test")
    results = doctor.check_stack_wide()
    notifier = [r for r in results if "notifier" in r.label][0]
    assert notifier.ok is True
    assert "ntfy" in notifier.detail


def test_render_text_all_green(monkeypatch):
    """If every agent has its required env + console scripts are findable,
    the doctor exit code is 0."""
    _isolate_env(monkeypatch)
    monkeypatch.setenv("NTFY_TOPIC", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    # Mock shutil.which to always return a fake path so console scripts pass
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/{name}")
    # Mock launchctl call so the funnel-analytics launchd check doesn't shell out
    import subprocess
    def fake_run(*args, **kwargs):
        class R:
            stdout = "com.alex.funnel-analytics.brief"
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)
    reports = [doctor.check_agent(spec) for spec in doctor.AGENT_CHECKS]
    text, all_ok = doctor.render_text(reports, doctor.check_stack_wide())
    assert all_ok is True
    assert "ALL GREEN" in text


def test_render_text_missing_required_fails(monkeypatch):
    _isolate_env(monkeypatch)
    # build-quality requires ANTHROPIC_API_KEY — leave it unset
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/{name}")
    reports = [doctor.check_agent(spec) for spec in doctor.AGENT_CHECKS
               if spec["name"] == "build-quality-agent"]
    text, all_ok = doctor.render_text(reports, doctor.check_stack_wide())
    assert all_ok is False
    assert "FIXES NEEDED" in text


def test_render_json_machine_readable(monkeypatch):
    _isolate_env(monkeypatch)
    monkeypatch.setenv("NTFY_TOPIC", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/{name}")
    import subprocess
    def fake_run(*args, **kwargs):
        class R:
            stdout = "com.alex.funnel-analytics.brief"
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)
    reports = [doctor.check_agent(spec) for spec in doctor.AGENT_CHECKS]
    text, all_ok = doctor.render_json(reports, doctor.check_stack_wide())
    blob = json.loads(text)
    assert blob["all_required_passed"] is True
    assert "agents" in blob and "stack_wide" in blob


def test_main_returns_nonzero_on_required_fail(monkeypatch, capsys):
    _isolate_env(monkeypatch)
    # Force ALL agents missing console scripts → exit non-zero
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    rc = doctor.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FIXES NEEDED" in out


def test_main_json_flag(monkeypatch, capsys):
    _isolate_env(monkeypatch)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    doctor.main(["--json"])
    out = capsys.readouterr().out
    blob = json.loads(out)
    assert "agents" in blob
