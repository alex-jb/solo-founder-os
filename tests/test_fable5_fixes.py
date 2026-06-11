"""Regression tests for the 3 production bugs Fable 5 audit surfaced
2026-06-11. Each test demonstrates the bug that existed before the fix.

Bugs:
  1. evolver: target_file JSON gate was bypassable via diff `+++` header
  2. agents_registry: governance/supervisor missed 3 agents (CS/CO/PA)
  3. usage_log: cost_usd never written, so morning_brief always saw $0
"""
from __future__ import annotations
import json
from pathlib import Path

from solo_founder_os.evolver import extract_diff_targets, is_safe_path
from solo_founder_os import agents_registry
from solo_founder_os import governance, supervisor
from solo_founder_os.usage_log import estimate_cost_usd, log_usage


# ─── Bug 1: evolver diff-target validation ───────────────────────────

def test_extract_diff_targets_pulls_both_sides():
    diff = (
        "--- a/solo_founder_os/drafter.py\n"
        "+++ b/solo_founder_os/drafter.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-old\n"
        "+new\n"
    )
    assert extract_diff_targets(diff) == ["solo_founder_os/drafter.py"]


def test_extract_diff_targets_ignores_dev_null():
    """New-file diffs have `--- /dev/null` — don't treat that as a target."""
    diff = (
        "--- /dev/null\n"
        "+++ b/templates/new_template.j2\n"
        "@@ -0,0 +1,1 @@\n"
        "+hi\n"
    )
    assert extract_diff_targets(diff) == ["templates/new_template.j2"]


def test_extract_diff_targets_normalizes_leading_dot_slash():
    """Without this normalization, an attacker could declare target_file
    `drafter.py` (safe) and ship a diff with `+++ b/./.env` and bypass
    the lowercase-substring check."""
    diff = "--- a/./.env\n+++ b/./.env\n"
    targets = extract_diff_targets(diff)
    assert targets == [".env"]
    assert not is_safe_path(targets[0])


def test_extract_diff_targets_catches_secret_swap_attempt():
    """The exact attack Fable 5 flagged: JSON says drafter.py, diff
    body actually clobbers .env. Both paths must surface so the safety
    gate can reject."""
    diff = (
        "--- a/.env\n"
        "+++ b/.env\n"
        "@@ -1 +1 @@\n"
        "-OK=1\n"
        "+OK=0\n"
    )
    targets = extract_diff_targets(diff)
    assert ".env" in targets
    assert not any(is_safe_path(t) for t in targets)


# ─── Bug 2: agents_registry single source of truth ───────────────────

def test_registry_includes_three_previously_invisible_agents():
    for slug in (".customer-support-agent", ".customer-outreach-agent", ".payments-agent"):
        assert slug in agents_registry.KNOWN_AGENT_DIRS, (
            f"{slug} not in registry — supervisor + governance would be blind"
        )


def test_governance_and_supervisor_share_registry():
    # Both modules now re-export the registry list. Membership must agree.
    assert set(governance.DEFAULT_AGENT_DIRS) == set(agents_registry.KNOWN_AGENT_DIRS)
    assert set(supervisor.DEFAULT_AGENT_DIRS) == set(agents_registry.KNOWN_AGENT_DIRS)


def test_registry_is_unique_no_dupes():
    assert len(agents_registry.KNOWN_AGENT_DIRS) == len(set(agents_registry.KNOWN_AGENT_DIRS))


# ─── Bug 3: usage_log writes cost_usd at log time ────────────────────

def test_estimate_cost_usd_haiku():
    # Haiku: $1/MTok in, $5/MTok out
    cost = estimate_cost_usd("claude-haiku-4-5", 1_000_000, 100_000)
    assert abs(cost - (1.0 + 0.5)) < 1e-9  # $1 in + $0.50 out


def test_estimate_cost_usd_unknown_model_returns_zero():
    """Don't drop rows for new model IDs — write the row with cost=0
    so the user notices via missing dollar signs, not via missing data."""
    assert estimate_cost_usd("claude-future-99", 100, 50) == 0.0


def test_log_usage_writes_cost_usd(tmp_path):
    """The exact bug morning_brief._cost_summary stumbled into."""
    log = tmp_path / "usage.jsonl"
    log_usage(
        log_path=log,
        model="claude-sonnet-4-6",
        input_tokens=100_000,
        output_tokens=10_000,
    )
    row = json.loads(log.read_text().strip())
    assert "cost_usd" in row, "morning_brief reads this field — must be present"
    # Sonnet: $3/MTok in, $15/MTok out -> $0.30 + $0.15 = $0.45
    assert abs(row["cost_usd"] - 0.45) < 1e-6
