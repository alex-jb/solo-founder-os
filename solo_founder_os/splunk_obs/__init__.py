"""splunk_obs — Splunk HEC adapter for SFOS agent streams.

Submission for the Splunk Agentic Ops Hackathon 2026 (deadline 2026-06-15).

What it ships:
- `hec_client.HECClient`: stdlib-only POST wrapper for Splunk HTTP Event
  Collector. Token via constructor or `SPLUNK_HEC_TOKEN` env. Constant-time
  compare on inbound webhook side (see `webhook.py`).
- `sfos_translator`: turns SFOS reflection / eval / bandit rows into HEC
  events with stable sourcetypes (`sfos:reflection`, `sfos:eval`,
  `sfos:bandit`).
- `emit_loop`: tails the local JSONL/SQLite stores, POSTs new rows, writes
  a watermark file so restart doesn't double-send.

Design choices:
- No new dependencies. Mirrors `solo_founder_os.http` urllib pattern so
  install stays seconds, not minutes.
- Watermark-per-source so one slow source can't block the others.
- All emitters are no-op if `SPLUNK_HEC_URL` is unset — the rest of SFOS
  keeps running with zero Splunk coupling.
"""
from .hec_client import HECClient, HECError

__all__ = ["HECClient", "HECError"]
