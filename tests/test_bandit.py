"""Tests for solo_founder_os.bandit — Thompson Beta-conjugate variant chooser."""
from __future__ import annotations
import sqlite3
from collections import Counter

import pytest

from solo_founder_os.bandit import Bandit, squash


@pytest.fixture
def b(tmp_path):
    return Bandit(agent="test-agent", channel="x",
                    db_path=tmp_path / "b.sqlite")


# ───────────────── squash ─────────────────


def test_squash_zero_returns_zero():
    assert squash(0) == 0.0


def test_squash_at_midpoint_returns_half():
    assert abs(squash(50, midpoint=50) - 0.5) < 1e-6


def test_squash_high_engagement_caps_below_one():
    assert 0.9 < squash(1000, midpoint=50) < 1.0


# ───────────────── construction + namespacing ─────────────────


def test_requires_agent_and_channel():
    with pytest.raises(ValueError):
        Bandit(agent="", channel="x")
    with pytest.raises(ValueError):
        Bandit(agent="ok", channel="")


def test_namespacing_isolates_agents(tmp_path):
    db = tmp_path / "shared.sqlite"
    b1 = Bandit(agent="agent-A", channel="x", db_path=db)
    b2 = Bandit(agent="agent-B", channel="x", db_path=db)
    b1.update("v1", reward=0.9)
    # b2 sees no data even though same channel name
    assert b2.stats() == []
    assert len(b1.stats()) == 1


# ───────────────── choose ─────────────────


def test_choose_with_one_variant_returns_it(b):
    assert b.choose(["only"]) == "only"


def test_choose_empty_raises(b):
    with pytest.raises(ValueError):
        b.choose([])


def test_choose_with_priors_picks_each_eventually(b):
    """With Beta(1,1) prior on every arm, 1000 draws should pick each
    of 3 variants meaningfully (not 1 absorbing all)."""
    counts = Counter(b.choose(["a", "b", "c"]) for _ in range(1000))
    assert all(n > 100 for n in counts.values())


def test_choose_after_strong_signal_prefers_winner(b):
    # Train: a wins 10 times, b loses 10 times
    for _ in range(10):
        b.update("a", reward=1.0)
        b.update("b", reward=0.0)
    counts = Counter(b.choose(["a", "b"]) for _ in range(200))
    assert counts["a"] > counts["b"] * 3  # decisive lean


# ───────────────── update ─────────────────


def test_update_invalid_reward(b):
    with pytest.raises(ValueError):
        b.update("k", reward=1.5)
    with pytest.raises(ValueError):
        b.update("k", reward=-0.1)


def test_update_increments_pulls_and_alpha(b):
    b.update("k", reward=0.7)
    s = next(a for a in b.stats() if a["variant_key"] == "k")
    assert s["n_pulls"] == 1
    assert abs(s["alpha"] - 1.7) < 1e-6
    assert abs(s["beta"] - 1.3) < 1e-6


def test_update_from_engagement_squashes_then_updates(b):
    r = b.update_from_engagement("k", raw_engagement=50, midpoint=50)
    assert abs(r - 0.5) < 1e-6
    s = next(a for a in b.stats() if a["variant_key"] == "k")
    assert s["n_pulls"] == 1


# ───────────────── stats + report ─────────────────


def test_stats_orders_by_pulls_desc(b):
    b.update("a", reward=0.5)
    for _ in range(3):
        b.update("b", reward=0.5)
    s = b.stats()
    assert s[0]["variant_key"] == "b"
    assert s[1]["variant_key"] == "a"


def test_report_picks_winner_when_qualified(b):
    for _ in range(5):
        b.update("winner", reward=0.9)
        b.update("loser", reward=0.1)
    r = b.report(min_pulls=3)
    assert r["winner"] == "winner"
    assert r["arms"][0]["variant_key"] == "winner"
    assert r["sample_size_warning"] is True  # < 10 pulls


def test_report_no_winner_below_min_pulls(b):
    b.update("a", reward=0.5)
    b.update("b", reward=0.5)
    r = b.report(min_pulls=5)
    assert r["winner"] is None


def test_report_includes_ci(b):
    for _ in range(20):
        b.update("a", reward=0.7)
    r = b.report(min_pulls=5)
    arm = r["arms"][0]
    assert arm["ci95_low"] < arm["mean"] < arm["ci95_high"]


# ───────────────── env override ─────────────────


def test_env_override_picks_alt_db(tmp_path, monkeypatch):
    custom = tmp_path / "custom-bandit.sqlite"
    monkeypatch.setenv("SFOS_BANDIT_DB", str(custom))
    b = Bandit(agent="t", channel="t")
    b.update("k", reward=0.5)
    assert custom.exists()
    # Verify the row landed there, not in default ~/.solo-founder-os/
    with sqlite3.connect(custom) as conn:
        n = conn.execute("SELECT COUNT(*) FROM bandit_arm").fetchone()[0]
    assert n == 1
