"""Tests for splunk_obs translator + emit_loop watermark resume.

We use the real HECClient with a recording stub instead of a real Splunk
backend — verifies the wire protocol shape AND the per-source state
machine in one shot.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

from solo_founder_os.splunk_obs import sfos_translator as T
from solo_founder_os.splunk_obs import emit_loop


class RecordingHEC:
    """Stand-in for HECClient.send_event that records what was sent.

    Matches the public interface emit_loop uses (`send_event` + `enabled`).
    """
    enabled = True

    def __init__(self) -> None:
        self.events: list[tuple[dict, str, float | None]] = []

    def send_event(self, event, *, sourcetype, event_time=None, **kw):
        self.events.append((event, sourcetype, event_time))
        return 1


def test_translate_reflection_shape():
    row = {
        "ts": "2026-06-11T18:42:00+00:00",
        "task": "draft_email",
        "outcome": "FAILED",
        "verbatim_signal": "user moved to rejected/",
        "reflection": "Drop the boilerplate intro next time.",
    }
    event, st, t = T.translate_reflection(row, agent_dir=".vc-outreach-agent")
    assert st == "sfos:reflection"
    assert event["agent_dir"] == ".vc-outreach-agent"
    assert event["task"] == "draft_email"
    assert event["outcome"] == "FAILED"
    assert "boilerplate" in event["reflection_text"]
    assert isinstance(t, float) and t > 1.7e9  # post-2023 epoch


def test_translate_eval_shape():
    row = {
        "skill": "translate-en-to-zh",
        "ts": "2026-05-13T20:30:00Z",
        "n_examples": 9,
        "mean": 1.13,
        "p50": 1.0,
        "p10": 0.5,
    }
    event, st, t = T.translate_eval(row)
    assert st == "sfos:eval"
    assert event["skill"] == "translate-en-to-zh"
    assert event["score"] == 1.13
    assert event["n_samples"] == 9
    assert event["p10"] == 0.5


def test_translate_bandit_handles_none_regret():
    row = {
        "ts": "2026-06-11T12:00:00+00:00",
        "arm_slug": "hn-launch-hook-v3",
        "predicted_reward": 0.42,
        "actual_reward": None,  # pull hasn't been resolved yet
        "regret": None,
    }
    event, st, _ = T.translate_bandit(row)
    assert st == "sfos:bandit"
    assert event["arm_slug"] == "hn-launch-hook-v3"
    assert event["actual_reward"] == 0.0
    assert event["regret"] == 0.0


def test_translate_many_unknown_kind_raises():
    try:
        T.translate_many([], kind="not-a-kind")
    except ValueError as e:
        assert "not-a-kind" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_emit_reflections_watermark_resumes(tmp_path, monkeypatch):
    fake_home = tmp_path
    agent_dir = fake_home / ".vc-outreach-agent"
    agent_dir.mkdir()
    refl = agent_dir / "reflections.jsonl"

    def _write(rows):
        with refl.open("a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    _write([{
        "ts": "2026-06-11T10:00:00+00:00",
        "task": "draft_email",
        "outcome": "OK",
        "verbatim_signal": "sent",
        "reflection": "",
    }])

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    hec = RecordingHEC()
    sent_1 = emit_loop.emit_reflections(hec)
    assert sent_1 == 1
    assert hec.events[0][1] == "sfos:reflection"

    # Second sweep with no new rows: 0 sent, watermark unchanged.
    hec = RecordingHEC()
    assert emit_loop.emit_reflections(hec) == 0

    # Append a new row: only the new one ships.
    _write([{
        "ts": "2026-06-11T11:00:00+00:00",
        "task": "draft_email",
        "outcome": "FAILED",
        "verbatim_signal": "rejected/",
        "reflection": "Less filler.",
    }])
    hec = RecordingHEC()
    assert emit_loop.emit_reflections(hec) == 1
    assert hec.events[0][0]["outcome"] == "FAILED"


def test_emit_evals_skips_already_seen(tmp_path, monkeypatch):
    fake_home = tmp_path
    evals_dir = fake_home / ".solo-founder-os" / "evals"
    evals_dir.mkdir(parents=True)
    (evals_dir / "2026-06-11-translate-en-to-zh.json").write_text(json.dumps({
        "skill": "translate-en-to-zh",
        "ts": "2026-06-11T20:00:00+00:00",
        "n_examples": 5, "mean": 4.2, "p50": 4.0, "p10": 3.5,
    }))
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    hec = RecordingHEC()
    assert emit_loop.emit_evals(hec) == 1
    # Second sweep: nothing new.
    hec = RecordingHEC()
    assert emit_loop.emit_evals(hec) == 0


def test_emit_bandit_reads_only_new_rowids(tmp_path, monkeypatch):
    fake_home = tmp_path
    ma_dir = fake_home / ".marketing_agent"
    ma_dir.mkdir()
    db = ma_dir / "history.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE pulls (ts TEXT, arm_slug TEXT, predicted_reward REAL, "
        "actual_reward REAL, regret REAL)"
    )
    conn.execute(
        "INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
        ("2026-06-11T10:00:00+00:00", "hn-v1", 0.3, 0.4, 0.1),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    hec = RecordingHEC()
    assert emit_loop.emit_bandit(hec) == 1
    assert hec.events[0][0]["arm_slug"] == "hn-v1"

    # Insert another row, second sweep ships only the new one.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
        ("2026-06-11T11:00:00+00:00", "hn-v2", 0.5, 0.6, 0.05),
    )
    conn.commit()
    conn.close()

    hec = RecordingHEC()
    assert emit_loop.emit_bandit(hec) == 1
    assert hec.events[0][0]["arm_slug"] == "hn-v2"


def test_emit_once_no_env_returns_zeros(monkeypatch, tmp_path):
    # No SPLUNK_HEC_URL / TOKEN — HECClient.from_env() is disabled, so
    # send_event returns 0 and we expect zeros across the board.
    monkeypatch.delenv("SPLUNK_HEC_URL", raising=False)
    monkeypatch.delenv("SPLUNK_HEC_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = emit_loop.emit_once()
    assert result == {"reflections": 0, "evals": 0, "bandit": 0}
