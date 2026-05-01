"""Tests for solo_founder_os.autopsy — cross-agent post-mortem engine."""
from __future__ import annotations
from dataclasses import dataclass

from solo_founder_os.autopsy import autopsy, render_markdown


# ───────────────── Fakes ─────────────────


@dataclass
class FakeMetric:
    posts: dict
    metrics: dict
    baselines: dict

    def fetch_post(self, post_id):
        return self.posts.get(post_id)

    def fetch_metric(self, post_id, metric):
        return self.metrics.get((post_id, metric), 0)

    def peer_baseline(self, channel, metric, *, limit=30):
        return self.baselines.get((channel, metric),
                                   {"median": 0, "p25": 0, "p75": 0, "n": 0})


class FakeCritic:
    def __init__(self, score, reasons):
        self.score = score
        self.reasons = reasons

    def score_body(self, body):
        return self.score, self.reasons


class FakeBestTime:
    def __init__(self, wd, h, src="cdf-of-50"):
        self.wd, self.h, self.src = wd, h, src

    def optimal_time(self, channel, metric):
        return self.wd, self.h, self.src


# ───────────────── core engine ─────────────────


def test_autopsy_post_not_found():
    src = FakeMetric(posts={}, metrics={}, baselines={})
    r = autopsy("missing", metric_source=src)
    assert r["post"] is None
    assert "not found" in r["diagnoses"][0].lower()


def test_autopsy_underperforming_post():
    src = FakeMetric(
        posts={"p1": {"channel": "x", "body": "hello world",
                       "posted_at_iso": "2026-05-01T12:00:00+00:00"}},
        metrics={("p1", "like"): 5},
        baselines={("x", "like"): {"median": 50, "p25": 20, "p75": 100, "n": 30}},
    )
    r = autopsy("p1", metric_source=src)
    assert r["engagement"] == 5
    assert r["underperformance"] > 0.5
    assert any("well below" in d.lower() for d in r["diagnoses"])


def test_autopsy_normal_variance_no_alarm():
    src = FakeMetric(
        posts={"p1": {"channel": "x", "body": "hello world", "posted_at_iso": ""}},
        metrics={("p1", "like"): 45},  # close to median 50
        baselines={("x", "like"): {"median": 50, "p25": 20, "p75": 100, "n": 30}},
    )
    r = autopsy("p1", metric_source=src)
    assert any("within normal variance" in d for d in r["diagnoses"])


def test_autopsy_thin_baseline_warns():
    src = FakeMetric(
        posts={"p1": {"channel": "x", "body": "hello", "posted_at_iso": ""}},
        metrics={("p1", "like"): 10},
        baselines={("x", "like"): {"median": 0, "p25": 0, "p75": 0, "n": 2}},
    )
    r = autopsy("p1", metric_source=src)
    assert any("benchmark unstable" in d for d in r["diagnoses"])


# ───────────────── critic hook ─────────────────


def test_critic_hook_diagnoses_added():
    src = FakeMetric(
        posts={"p1": {"channel": "x",
                       "body": "Revolutionary AI changes everything!",
                       "posted_at_iso": ""}},
        metrics={("p1", "like"): 50},
        baselines={("x", "like"): {"median": 50, "p25": 20, "p75": 100, "n": 30}},
    )
    crit = FakeCritic(score=4.0, reasons=["hype words: revolutionary"])
    r = autopsy("p1", metric_source=src, critic=crit)
    assert r["critic"]["score"] == 4.0
    assert any("hype words" in d for d in r["diagnoses"])
    assert any("Strip flagged patterns" in rec for rec in r["recommendations"])


def test_critic_hook_skipped_when_no_body():
    src = FakeMetric(
        posts={"p1": {"channel": "x", "body": "", "posted_at_iso": ""}},
        metrics={("p1", "like"): 50},
        baselines={("x", "like"): {"median": 50, "p25": 20, "p75": 100, "n": 30}},
    )
    crit = FakeCritic(score=4.0, reasons=["x"])
    r = autopsy("p1", metric_source=src, critic=crit)
    # Critic skipped when body is empty — no critic payload, no critic-related diag
    assert not r["critic"]


# ───────────────── best-time hook ─────────────────


def test_best_time_hook_flags_mistimed_post():
    src = FakeMetric(
        posts={"p1": {"channel": "x", "body": "x" * 200,
                       "posted_at_iso": "2026-05-01T03:00:00+00:00"}},
        metrics={("p1", "like"): 50},
        baselines={("x", "like"): {"median": 50, "p25": 20, "p75": 100, "n": 30}},
    )
    # Post was Friday 03:00 UTC. Best is Mon 14:00.
    bt = FakeBestTime(wd=0, h=14)
    r = autopsy("p1", metric_source=src, best_time=bt)
    assert any("but best slot" in d for d in r["diagnoses"])


# ───────────────── length-vs-norm ─────────────────


def test_short_body_threshold_per_channel():
    src = FakeMetric(
        posts={"p1": {"channel": "x", "body": "tiny", "posted_at_iso": ""}},
        metrics={("p1", "like"): 50},
        baselines={("x", "like"): {"median": 50, "p25": 20, "p75": 100, "n": 30}},
    )
    r = autopsy("p1", metric_source=src,
                  short_body_thresholds={"x": 80})
    assert any("too thin" in d for d in r["diagnoses"])


def test_short_body_skipped_for_unconfigured_channel():
    src = FakeMetric(
        posts={"p1": {"channel": "linkedin", "body": "tiny",
                       "posted_at_iso": ""}},
        metrics={("p1", "like"): 50},
        baselines={("linkedin", "like"): {"median": 50, "p25": 20, "p75": 100, "n": 30}},
    )
    r = autopsy("p1", metric_source=src,
                  short_body_thresholds={"x": 80})  # only x configured
    assert not any("too thin" in d for d in r["diagnoses"])


# ───────────────── render_markdown ─────────────────


def test_render_markdown_smoke():
    src = FakeMetric(
        posts={"p1": {"channel": "x", "body": "hi",
                       "posted_at_iso": "2026-05-01T12:00:00+00:00",
                       "external_id": "p1"}},
        metrics={("p1", "like"): 5},
        baselines={("x", "like"): {"median": 50, "p25": 20, "p75": 100, "n": 30}},
    )
    r = autopsy("p1", metric_source=src)
    md = render_markdown(r)
    assert "# Post-mortem" in md
    assert "## Diagnoses" in md
    assert "## Recommendations" in md


def test_render_markdown_post_not_found():
    src = FakeMetric(posts={}, metrics={}, baselines={})
    r = autopsy("missing", metric_source=src)
    md = render_markdown(r)
    assert "Post not found" in md
