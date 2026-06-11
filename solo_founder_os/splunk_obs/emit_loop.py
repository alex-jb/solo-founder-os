"""Tail the three SFOS sources, POST new rows to Splunk HEC.

Per-source watermark files live under ~/.solo-founder-os/splunk_obs/
so a restart resumes where we left off and one slow source can't block
the others. Watermark shape is intentionally minimal:

    {"offset": <jsonl byte offset>}                  # reflections.jsonl
    {"seen": ["2026-06-10-skill-foo.json", ...]}     # eval dir files
    {"row_id": <sqlite max(rowid) emitted>}          # marketing history.db

Designed to run from cron every 5 min via `sfos-splunk` (added in cli.py
in a follow-up). For the hackathon demo, calling `emit_once()` from a
loom recording is enough.
"""
from __future__ import annotations
import glob
import json
import os
import pathlib
import sqlite3
from typing import Iterable

from .hec_client import HECClient
from . import sfos_translator as T


def _state_dir() -> pathlib.Path:
    """Resolve at call time, not module-import time, so tests that
    monkeypatch Path.home() see the redirected location."""
    return pathlib.Path.home() / ".solo-founder-os" / "splunk_obs"


def _load_state(name: str) -> dict:
    sd = _state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    p = sd / f"{name}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(name: str, state: dict) -> None:
    sd = _state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    (sd / f"{name}.json").write_text(json.dumps(state))


def _send_batch(
    hec: HECClient,
    items: Iterable[tuple[dict, str, float]],
) -> int:
    """Group by sourcetype so each HEC POST is one sourcetype's events."""
    by_st: dict[str, list[tuple[dict, float]]] = {}
    for event, sourcetype, ev_time in items:
        by_st.setdefault(sourcetype, []).append((event, ev_time))
    total = 0
    for st, evs in by_st.items():
        # HEC accepts mixed event_time per call via the per-line `time`
        # field, but our send_events takes a single event_time. So loop
        # per-event for cleanliness — still ONE HTTP POST per sourcetype
        # because send_events batches at the wire level.
        for event, t in evs:
            total += hec.send_event(event, sourcetype=st, event_time=t)
    return total


def emit_reflections(hec: HECClient) -> int:
    """Tail every ~/.<agent>/reflections.jsonl from byte offset."""
    home = pathlib.Path.home()
    sent = 0
    for p in home.glob(".*/reflections.jsonl"):
        agent_dir = p.parent.name
        state_name = f"reflections-{agent_dir.lstrip('.')}"
        state = _load_state(state_name)
        offset = state.get("offset", 0)
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size < offset:
            # File rotated or truncated; restart from 0.
            offset = 0
        if size == offset:
            continue
        rows = []
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            new_offset = fh.tell()
        items = T.translate_many(rows, kind="reflection", agent_dir=agent_dir)
        sent += _send_batch(hec, items)
        _save_state(state_name, {"offset": new_offset})
    return sent


def emit_evals(hec: HECClient) -> int:
    """Walk ~/.solo-founder-os/evals/*.json, skip ones we've sent."""
    evals_dir = pathlib.Path.home() / ".solo-founder-os" / "evals"
    if not evals_dir.exists():
        return 0
    state = _load_state("evals")
    seen = set(state.get("seen", []))
    sent = 0
    new_seen = list(seen)
    for path in sorted(evals_dir.glob("*.json")):
        if path.name in seen:
            continue
        try:
            row = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        items = T.translate_many([row], kind="eval")
        sent += _send_batch(hec, items)
        new_seen.append(path.name)
    if len(new_seen) != len(seen):
        _save_state("evals", {"seen": new_seen})
    return sent


def emit_bandit(hec: HECClient) -> int:
    """Read new marketing-agent history.db pulls beyond last row_id."""
    db_path = pathlib.Path.home() / ".marketing_agent" / "history.db"
    if not db_path.exists():
        return 0
    state = _load_state("bandit")
    last_id = int(state.get("row_id", 0))
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return 0
    try:
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT rowid, ts, arm_slug, predicted_reward, "
                "actual_reward, regret FROM pulls "
                "WHERE rowid > ? ORDER BY rowid",
                (last_id,),
            )
        except sqlite3.OperationalError:
            return 0
        rows = []
        max_id = last_id
        for r in cur:
            rows.append(dict(r))
            max_id = max(max_id, r["rowid"])
    finally:
        conn.close()
    if not rows:
        return 0
    items = T.translate_many(rows, kind="bandit")
    sent = _send_batch(hec, items)
    _save_state("bandit", {"row_id": max_id})
    return sent


def emit_once(hec: HECClient | None = None) -> dict:
    """One sweep across all three sources. Returns count per source."""
    if hec is None:
        hec = HECClient.from_env()
    return {
        "reflections": emit_reflections(hec),
        "evals": emit_evals(hec),
        "bandit": emit_bandit(hec),
    }
