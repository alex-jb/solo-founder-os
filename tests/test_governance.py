"""Tests for governance — unified inbox + audit log."""
from __future__ import annotations
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.governance import (
    InboxItem,
    _short_id,
    approve,
    main,
    reject,
    scan_inbox,
)


def _plant_pending(home: pathlib.Path, agent: str, filename: str,
                    title: str = "test", priority: str = "med",
                    body: str = "details") -> pathlib.Path:
    """Create a fake queue/pending/<filename> for the given agent."""
    pending = home / agent / "queue" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    p = pending / filename
    p.write_text(
        "---\n"
        f"title: {title}\n"
        f"priority: {priority}\n"
        f"proposed_at: {datetime.now(timezone.utc).isoformat()}\n"
        "---\n\n"
        f"# {title}\n\n{body}\n"
    )
    return p


def _patch_home(monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)


# ── _short_id ────────────────────────────────────────────


def test_short_id_stable():
    """Same agent + filename always produces the same id."""
    assert _short_id(".vc-outreach", "alice.md") == \
            _short_id(".vc-outreach", "alice.md")


def test_short_id_distinct():
    """Different inputs produce different ids."""
    assert _short_id(".vc-outreach", "alice.md") != \
            _short_id(".vc-outreach", "bob.md")
    assert _short_id(".vc-outreach", "x.md") != \
            _short_id(".bilingual", "x.md")


def test_short_id_length():
    assert len(_short_id(".a", "b")) == 8


# ── scan_inbox ───────────────────────────────────────────


def test_scan_empty_home(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    assert scan_inbox(home=tmp_path) == []


def test_scan_one_agent(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    _plant_pending(tmp_path, ".vc-outreach-agent", "alice.md",
                    title="Email Alice")
    items = scan_inbox(home=tmp_path)
    assert len(items) == 1
    assert items[0].title == "Email Alice"
    assert items[0].agent == ".vc-outreach-agent"


def test_scan_multiple_agents_sorts_by_priority(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    _plant_pending(tmp_path, ".vc-outreach-agent", "low.md",
                    priority="low", title="Low")
    _plant_pending(tmp_path, ".bilingual-content-sync-agent", "high.md",
                    priority="high", title="High")
    _plant_pending(tmp_path, ".customer-discovery-agent", "urgent.md",
                    priority="urgent", title="Urgent")
    items = scan_inbox(home=tmp_path)
    assert [it.title for it in items] == ["Urgent", "High", "Low"]


def test_scan_filter_by_agent(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    _plant_pending(tmp_path, ".vc-outreach-agent", "a.md", title="VC")
    _plant_pending(tmp_path, ".bilingual-content-sync-agent", "b.md",
                    title="i18n")
    items = scan_inbox(home=tmp_path, agent="vc-outreach-agent")
    assert len(items) == 1
    assert items[0].title == "VC"


def test_scan_filter_by_since(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    _plant_pending(tmp_path, ".vc-outreach-agent", "x.md", title="x")
    # since=future → 0 results
    items = scan_inbox(home=tmp_path,
                        since=datetime.now(timezone.utc) + timedelta(hours=1))
    assert items == []


def test_scan_includes_supervisor_proposed_tasks(monkeypatch, tmp_path):
    """sfos-supervisor proposals should also show up in the inbox."""
    _patch_home(monkeypatch, tmp_path)
    sup_dir = tmp_path / ".solo-founder-os" / "proposed-tasks" / "pending"
    sup_dir.mkdir(parents=True)
    (sup_dir / "ship-x.md").write_text(
        "---\ntitle: Ship X\npriority: high\n---\n\n# Ship X\n")
    items = scan_inbox(home=tmp_path)
    assert any(it.title == "Ship X" for it in items)


def test_scan_handles_corrupt_files(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    pending = tmp_path / ".vc-outreach-agent" / "queue" / "pending"
    pending.mkdir(parents=True)
    (pending / "garbage.md").write_text("not valid frontmatter")
    # Item still surfaces (with title from filename), but doesn't crash
    items = scan_inbox(home=tmp_path)
    assert len(items) == 1
    assert items[0].title == "garbage"


def test_scan_falls_back_to_h1_for_title(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    pending = tmp_path / ".vc-outreach-agent" / "queue" / "pending"
    pending.mkdir(parents=True)
    (pending / "x.md").write_text("---\n---\n\n# Real title from H1\n")
    items = scan_inbox(home=tmp_path)
    assert items[0].title == "Real title from H1"


# ── approve / reject ─────────────────────────────────────


def test_approve_moves_file_and_logs(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    p = _plant_pending(tmp_path, ".vc-outreach-agent", "alice.md")
    items = scan_inbox(home=tmp_path)
    log_path = tmp_path / "decisions.jsonl"
    new_path = approve(items[0], note="looks good", log_path=log_path)
    assert new_path is not None
    assert new_path.exists()
    assert "approved" in str(new_path)
    assert not p.exists()  # original moved
    rows = log_path.read_text().splitlines()
    assert len(rows) == 1
    entry = json.loads(rows[0])
    assert entry["action"] == "approve"
    assert entry["note"] == "looks good"


def test_reject_moves_file_and_logs(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    _plant_pending(tmp_path, ".bilingual-content-sync-agent", "x.md")
    items = scan_inbox(home=tmp_path)
    log_path = tmp_path / "decisions.jsonl"
    new_path = reject(items[0], note="off-brand", log_path=log_path)
    assert new_path is not None
    assert "rejected" in str(new_path)


def test_approve_missing_file_logs_failure(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    item = InboxItem(
        id="abc", agent=".x", filename="gone.md",
        path=tmp_path / "gone.md",
    )
    log_path = tmp_path / "decisions.jsonl"
    new_path = approve(item, log_path=log_path)
    assert new_path is None
    rows = log_path.read_text().splitlines()
    assert "FAIL" in rows[0]


# ── CLI ──────────────────────────────────────────────────


def test_main_skip_env(monkeypatch):
    monkeypatch.setenv("INBOX_SKIP", "1")
    rc = main([])
    assert rc == 0


def test_main_list_empty(monkeypatch, tmp_path, capsys):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("INBOX_SKIP", raising=False)
    rc = main([])
    assert rc == 0
    err = capsys.readouterr().err
    assert "inbox empty" in err


def test_main_list_shows_items(monkeypatch, tmp_path, capsys):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("INBOX_SKIP", raising=False)
    _plant_pending(tmp_path, ".vc-outreach-agent", "alice.md",
                    title="Draft to Alice", priority="urgent")
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Draft to Alice" in out
    assert "vc-outreach-agent" in out


def test_main_list_json(monkeypatch, tmp_path, capsys):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("INBOX_SKIP", raising=False)
    _plant_pending(tmp_path, ".vc-outreach-agent", "x.md",
                    title="Test", priority="high")
    rc = main(["list", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    blob = json.loads(out)
    assert len(blob) == 1
    assert blob[0]["title"] == "Test"
    assert blob[0]["priority"] == "high"


def test_main_approve_by_id(monkeypatch, tmp_path, capsys):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("INBOX_SKIP", raising=False)
    _plant_pending(tmp_path, ".vc-outreach-agent", "alice.md")
    items = scan_inbox(home=tmp_path)
    item_id = items[0].id
    rc = main(["approve", item_id, "--note", "ship it"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "approve" in err.lower()
    # File moved
    assert not (tmp_path / ".vc-outreach-agent" / "queue" / "pending"
                 / "alice.md").exists()
    assert (tmp_path / ".vc-outreach-agent" / "queue" / "approved"
             / "alice.md").exists()


def test_main_approve_unknown_id_returns_1(monkeypatch, tmp_path, capsys):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("INBOX_SKIP", raising=False)
    rc = main(["approve", "deadbeef"])
    assert rc == 1


def test_main_reject_by_id(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("INBOX_SKIP", raising=False)
    _plant_pending(tmp_path, ".vc-outreach-agent", "spam.md")
    items = scan_inbox(home=tmp_path)
    rc = main(["reject", items[0].id, "--note", "off-brand"])
    assert rc == 0
    assert (tmp_path / ".vc-outreach-agent" / "queue" / "rejected"
             / "spam.md").exists()
