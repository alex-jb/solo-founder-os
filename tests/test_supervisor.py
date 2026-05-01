"""Tests for L2 supervisor — auto-find work across the agent stack.

Strategy: build a fake `home` directory with controlled agent state,
inject a fake AnthropicClient, verify gather_state + propose_tasks +
write_proposals all behave correctly without network or filesystem outside tmp.
"""
from __future__ import annotations
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.supervisor import (
    AgentState,
    Task,
    _read_recent_reflections,
    _read_usage_calls_last_24h,
    _slug,
    gather_state,
    main,
    propose_tasks,
    write_proposals,
)


def _patch_home(monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)


def _make_usage_log(home: pathlib.Path, agent_dir: str,
                     n_within_24h: int, n_older: int = 0) -> pathlib.Path:
    """Write a fake usage.jsonl with N entries within last 24h + N older."""
    p = home / agent_dir / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(n_within_24h):
        rows.append({
            "ts": (now - timedelta(hours=i % 23)).isoformat(),
            "model": "claude-haiku-4-5",
            "input_tokens": 100, "output_tokens": 20,
        })
    for i in range(n_older):
        rows.append({
            "ts": (now - timedelta(days=10 + i)).isoformat(),
            "model": "claude-haiku-4-5",
            "input_tokens": 100, "output_tokens": 20,
        })
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


# ── Pure I/O helpers ────────────────────────────────────────────


def test_count_usage_calls_24h(monkeypatch, tmp_path):
    p = _make_usage_log(tmp_path, ".test-agent",
                          n_within_24h=15, n_older=8)
    assert _read_usage_calls_last_24h(p) == 15


def test_count_usage_no_log(tmp_path):
    assert _read_usage_calls_last_24h(tmp_path / "missing") == 0


def test_count_usage_corrupt_lines_skipped(tmp_path):
    p = tmp_path / "u.jsonl"
    p.write_text("not json\n" + json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": "x", "input_tokens": 1, "output_tokens": 1,
    }) + "\n")
    assert _read_usage_calls_last_24h(p) == 1


def test_read_reflections(monkeypatch, tmp_path):
    rfile = tmp_path / "reflections.jsonl"
    rfile.write_text("\n".join([
        json.dumps({"task": "draft_email", "outcome": "FAILED",
                     "reflection": "soften the tone"}),
        json.dumps({"task": "scrape", "outcome": "OK",
                     "reflection": ""}),  # empty - skip
        json.dumps({"task": "draft_email", "outcome": "FAILED",
                     "reflection": "shorten subject"}),
    ]))
    refs = _read_recent_reflections(tmp_path)
    assert len(refs) == 2
    assert "draft_email" in refs[0]
    assert "soften the tone" in refs[0]


def test_slug_kebab_case():
    assert _slug("Run sfos-doctor before sleep!") == "run-sfos-doctor-before-sleep"
    assert _slug("Fix Bug #42") == "fix-bug-42"
    assert _slug("") == "task"
    long = "a" * 200
    assert len(_slug(long)) == 60


# ── gather_state integration ───────────────────────────────────


def test_gather_state_empty_home(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    state = gather_state()
    assert "now" in state
    assert len(state["agents"]) >= 7  # default registry
    # All agents zero state
    assert all(a["usage_calls_24h"] == 0 for a in state["agents"])
    assert all(a["recent_reflections"] == [] for a in state["agents"])
    # Stack notes captured the missing notifier + key
    assert any("notifier" in n for n in state["stack_notes"])
    assert any("ANTHROPIC_API_KEY" in n for n in state["stack_notes"])


def test_gather_state_active_agent(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("NTFY_TOPIC", "alex-test")
    _make_usage_log(tmp_path, ".funnel-analytics-agent",
                     n_within_24h=42)
    # Plant a reflection
    rfile = tmp_path / ".funnel-analytics-agent" / "reflections.jsonl"
    rfile.write_text(json.dumps({
        "task": "alert_producthunt", "outcome": "FAILED",
        "reflection": "rank fell out of top 10 — push another DM wave",
    }) + "\n")
    state = gather_state()
    fa = next(a for a in state["agents"]
               if a["name"] == "funnel-analytics-agent")
    assert fa["usage_calls_24h"] == 42
    assert any("rank fell out" in r for r in fa["recent_reflections"])
    # Stack notes empty (notifier + key both set)
    assert state["stack_notes"] == []


def test_gather_state_counts_pending_hitl(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    pending = (tmp_path / ".vc-outreach-agent" / "queue" / "pending")
    pending.mkdir(parents=True)
    (pending / "draft1.md").write_text("body")
    (pending / "draft2.md").write_text("body")
    state = gather_state()
    vc = next(a for a in state["agents"]
               if a["name"] == "vc-outreach-agent")
    assert vc["hitl_pending_count"] == 2


# ── propose_tasks via fake client ───────────────────────────────


def _fake_client(*, configured: bool = True, tasks: list | None = None,
                  err: str | None = None):
    c = MagicMock()
    c.configured = configured
    if err is not None:
        c.messages_create_json.return_value = (None, err)
    else:
        c.messages_create_json.return_value = ({"tasks": tasks or []}, None)
    return c


def test_propose_tasks_unconfigured_returns_empty():
    fake = _fake_client(configured=False)
    out = propose_tasks({"now": "x", "agents": [], "stack_notes": []},
                          client=fake)
    assert out == []
    assert fake.messages_create_json.call_count == 0


def test_propose_tasks_parses_response():
    fake = _fake_client(tasks=[
        {"title": "Push another backer DM wave",
         "agent": "manual",
         "reasoning": "PH rank slipped out of top 10 in the last hour.",
         "command": "manual",
         "priority": "urgent"},
        {"title": "Run vc-outreach send queue",
         "agent": ".vc-outreach-agent",
         "reasoning": "3 drafts approved 2 days ago, still unsent.",
         "command": "vc-outreach-agent send",
         "priority": "high"},
    ])
    state = {"now": "2026-05-04T07:00:00+00:00", "agents": [],
             "stack_notes": []}
    out = propose_tasks(state, client=fake)
    assert len(out) == 2
    assert isinstance(out[0], Task)
    assert out[0].priority == "urgent"
    assert out[1].agent == ".vc-outreach-agent"


def test_propose_tasks_truncates_to_max():
    fake = _fake_client(tasks=[
        {"title": f"Task {i}", "agent": "manual",
         "reasoning": "...", "command": "x", "priority": "low"}
        for i in range(10)
    ])
    out = propose_tasks({"now": "x", "agents": [], "stack_notes": []},
                          client=fake, max_tasks=3)
    assert len(out) == 3


def test_propose_tasks_handles_anthropic_error():
    fake = _fake_client(err="rate limit")
    out = propose_tasks({"now": "x", "agents": [], "stack_notes": []},
                          client=fake)
    assert out == []


def test_propose_tasks_drops_malformed_entries():
    """If one entry is missing required fields, skip it but keep the others."""
    fake = _fake_client(tasks=[
        {"title": "Good", "agent": "manual", "reasoning": "...",
         "command": "x", "priority": "high"},
        {"title": "Bad — no priority", "agent": "manual",
         "reasoning": "...", "command": "x"},  # missing priority
        {"title": "Also good", "agent": "manual", "reasoning": "...",
         "command": "y", "priority": "med"},
    ])
    out = propose_tasks({"now": "x", "agents": [], "stack_notes": []},
                          client=fake)
    assert len(out) == 2
    assert out[0].title == "Good"


# ── write_proposals ────────────────────────────────────────────


def test_write_proposals_creates_markdown(tmp_path):
    out_dir = tmp_path / "pending"
    tasks = [
        Task(title="Push another backer DM wave",
             agent="manual",
             reasoning="PH rank slipped — momentum stalls.",
             command="manual",
             priority="urgent"),
    ]
    paths = write_proposals(tasks, out_dir=out_dir)
    assert len(paths) == 1
    md = paths[0].read_text()
    assert "title: Push another backer DM wave" in md
    assert "priority: urgent" in md
    assert "PH rank slipped" in md
    assert "manual" in md


def test_write_proposals_appends_suffix_on_collision(tmp_path):
    """Same title twice in one day → second one gets a numeric suffix."""
    out_dir = tmp_path / "pending"
    task = Task(title="Same task", agent="manual", reasoning="r",
                 command="x", priority="low")
    p1 = write_proposals([task], out_dir=out_dir)[0]
    p2 = write_proposals([task], out_dir=out_dir)[0]
    assert p1 != p2
    assert "-2.md" in str(p2)


# ── main() CLI ─────────────────────────────────────────────────


def test_main_skip_env(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_SKIP", "1")
    rc = main([])
    assert rc == 0


def test_main_dry_run_prints(monkeypatch, tmp_path, capsys):
    """--dry-run shouldn't write to disk; should print to stdout."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("SUPERVISOR_SKIP", raising=False)

    # Patch the AnthropicClient so the constructed-by-default client returns
    # a fake response.
    import solo_founder_os.supervisor as sup

    class FakeClient:
        configured = True
        def messages_create_json(self, **kw):
            return ({"tasks": [{
                "title": "Sample task",
                "agent": "manual",
                "reasoning": "test",
                "command": "echo hi",
                "priority": "med",
            }]}, None)

    monkeypatch.setattr(sup, "AnthropicClient",
                         lambda **kw: FakeClient())
    rc = main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Sample task" in out
    # No files written under the proposals dir
    assert not (tmp_path / ".solo-founder-os" / "proposed-tasks"
                / "pending").exists()


def test_main_writes_when_not_dry_run(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    import solo_founder_os.supervisor as sup
    class FakeClient:
        configured = True
        def messages_create_json(self, **kw):
            return ({"tasks": [{
                "title": "Real task",
                "agent": "manual",
                "reasoning": "test",
                "command": "echo hi",
                "priority": "high",
            }]}, None)
    monkeypatch.setattr(sup, "AnthropicClient",
                         lambda **kw: FakeClient())
    monkeypatch.setattr(sup, "PROPOSALS_DIR",
                         tmp_path / ".solo-founder-os" / "proposed-tasks"
                         / "pending")
    rc = main([])
    assert rc == 0
    pending = (tmp_path / ".solo-founder-os" / "proposed-tasks" / "pending")
    files = list(pending.glob("*.md"))
    assert len(files) == 1
    assert "Real task" in files[0].read_text()


def test_main_no_tasks_returns_zero(monkeypatch, tmp_path, capsys):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    import solo_founder_os.supervisor as sup
    class FakeClient:
        configured = True
        def messages_create_json(self, **kw):
            return ({"tasks": []}, None)
    monkeypatch.setattr(sup, "AnthropicClient",
                         lambda **kw: FakeClient())
    rc = main([])
    assert rc == 0


# ── Sanity ────────────────────────────────────────────────────


def test_agentstate_default_construction():
    s = AgentState(name="x", home_dir=".x")
    assert s.usage_calls_24h == 0
    assert s.recent_reflections == []
