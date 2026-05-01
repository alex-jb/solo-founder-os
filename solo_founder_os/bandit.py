"""Bandit — Thompson-sampling variant selection, cross-agent.

Lifted from marketing-agent (where it proved out on X variant choice
in v0.4) into the shared lib so any agent that has multiple stylistic
options for the same task can A/B them without re-implementing the
Beta-conjugate math.

Use cases proven so far:
  - marketing-agent X variants: emoji-led / question-led / stat-led
  - vc-outreach cold-email templates: value-first / question-first / story-first (planned)
  - bilingual translation styles: literal / idiomatic / native (planned)

Math: classic Thompson sampling with Beta(1, 1) prior. After each
trial we record a continuous reward in [0, 1] (often a logistic-squashed
engagement count); the conjugate update treats the reward as a partial
success (α += r, β += 1 - r). At choose() time we sample each arm's
Beta and pick argmax — exploration emerges from posterior uncertainty.

Storage: a single shared SQLite at ~/.solo-founder-os/bandit.sqlite
with composite primary key (agent, channel, variant_key). Override
location with SFOS_BANDIT_DB. The cross-agent table means a future
"learn across agents" pass can see, e.g., that emoji-led wins on
marketing-agent's X channel AND on customer-discovery-agent's
notification channel.

Usage:
    from solo_founder_os.bandit import Bandit
    b = Bandit(agent="vc-outreach-agent", channel="cold_email")
    chosen = b.choose(["value_first", "question_first", "story_first"])
    # ... after seeing the reply rate ...
    b.update_from_engagement("value_first", raw_engagement=12, midpoint=10)
"""
from __future__ import annotations
import math
import os
import pathlib
import random
import sqlite3
from datetime import datetime, timezone
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS bandit_arm (
    agent         TEXT NOT NULL,
    channel       TEXT NOT NULL,
    variant_key   TEXT NOT NULL,
    alpha         REAL NOT NULL DEFAULT 1.0,
    beta          REAL NOT NULL DEFAULT 1.0,
    n_pulls       INTEGER NOT NULL DEFAULT 0,
    last_updated  TEXT,
    PRIMARY KEY (agent, channel, variant_key)
);
CREATE INDEX IF NOT EXISTS idx_bandit_agent_channel
    ON bandit_arm(agent, channel);
"""


def _default_db_path() -> pathlib.Path:
    """Shared bandit DB across the whole agent stack."""
    override = os.getenv("SFOS_BANDIT_DB")
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".solo-founder-os" / "bandit.sqlite"


def squash(raw: float, midpoint: float = 50.0) -> float:
    """Logistic squash: raw engagement count → reward in [0, 1].

    `midpoint` is where the curve hits 0.5 — pick something close to
    a "normal" engagement value for the channel. For X likes ~50 is
    sensible; for cold-email reply rate ~0.1 is sensible (so set
    midpoint=0.1 there).
    """
    if raw <= 0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-(raw - midpoint) / midpoint))


class Bandit:
    """Thompson-sampling Beta-conjugate variant chooser, namespaced by
    (agent, channel). Shared SQLite — see module docstring.
    """

    def __init__(self, *, agent: str, channel: str,
                   db_path: Optional[pathlib.Path | str] = None):
        if not agent or not channel:
            raise ValueError("agent and channel are both required")
        self.agent = agent
        self.channel = channel
        self.db_path = pathlib.Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)

    # ───────────────── private ─────────────────

    def _ensure_arm(self, key: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO bandit_arm "
                "(agent, channel, variant_key) VALUES (?, ?, ?)",
                (self.agent, self.channel, key),
            )

    # ───────────────── public ─────────────────

    def choose(self, variant_keys: list[str]) -> str:
        """Sample one variant via Thompson sampling. Single-element list
        is returned as-is (no exploration needed)."""
        if not variant_keys:
            raise ValueError("variant_keys cannot be empty")
        if len(variant_keys) == 1:
            return variant_keys[0]
        for k in variant_keys:
            self._ensure_arm(k)
        with sqlite3.connect(self.db_path) as conn:
            rows = {r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT variant_key, alpha, beta FROM bandit_arm "
                "WHERE agent=? AND channel=? AND variant_key IN ("
                + ",".join("?" * len(variant_keys)) + ")",
                [self.agent, self.channel, *variant_keys],
            ).fetchall()}
        samples = {k: random.betavariate(*rows.get(k, (1.0, 1.0)))
                   for k in variant_keys}
        return max(samples.items(), key=lambda kv: kv[1])[0]

    def update(self, variant_key: str, *, reward: float) -> None:
        """Update Beta(α, β) for an arm with a reward in [0, 1].

        Continuous-reward Beta update (industry approximation):
            α += r
            β += 1 - r
        """
        if not 0.0 <= reward <= 1.0:
            raise ValueError(f"reward must be in [0, 1], got {reward}")
        self._ensure_arm(variant_key)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE bandit_arm
                   SET alpha = alpha + ?, beta = beta + ?,
                       n_pulls = n_pulls + 1,
                       last_updated = ?
                   WHERE agent=? AND channel=? AND variant_key=?""",
                (reward, 1.0 - reward,
                 datetime.now(timezone.utc).isoformat(),
                 self.agent, self.channel, variant_key),
            )

    def update_from_engagement(self, variant_key: str,
                                  raw_engagement: float,
                                  midpoint: float = 50.0) -> float:
        """Squash raw engagement → reward → update. Returns reward used."""
        r = squash(raw_engagement, midpoint=midpoint)
        self.update(variant_key, reward=r)
        return r

    def stats(self) -> list[dict]:
        """Per-arm summary scoped to this (agent, channel)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT variant_key, alpha, beta, n_pulls, last_updated "
                "FROM bandit_arm WHERE agent=? AND channel=? "
                "ORDER BY n_pulls DESC",
                (self.agent, self.channel),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["mean"] = round(d["alpha"] / (d["alpha"] + d["beta"]), 4)
            out.append(d)
        return out

    def report(self, *, min_pulls: int = 3) -> dict:
        """A/B winner report with 95% credible intervals, scoped to
        this (agent, channel). Single-channel version of the
        marketing-agent multi-channel report."""
        arms = self.stats()
        qualified = [a for a in arms if a["n_pulls"] >= min_pulls]
        winner = None
        if qualified:
            winner = max(qualified, key=lambda a: a["mean"])["variant_key"]
        for a in arms:
            a_, b_ = a["alpha"], a["beta"]
            denom = (a_ + b_) ** 2 * (a_ + b_ + 1)
            std = (a_ * b_ / denom) ** 0.5 if denom else 0.0
            a["std"] = round(std, 4)
            a["ci95_low"] = round(max(0.0, a["mean"] - 1.96 * std), 4)
            a["ci95_high"] = round(min(1.0, a["mean"] + 1.96 * std), 4)
        return {
            "agent": self.agent,
            "channel": self.channel,
            "winner": winner,
            "arms": sorted(arms, key=lambda a: a["mean"], reverse=True),
            "sample_size_warning": (winner is not None and qualified
                                       and max(a["n_pulls"] for a in qualified) < 10),
        }
