---
name: sfos-splunk-obs
description: Ship reflections, eval drift, and bandit regret from the solo-founder-os 11-agent stack into Splunk over HEC. Five-minute install, zero new Python deps (stdlib urllib + json + sqlite3), six-panel dashboard you import once. Use when the user runs solo-founder-os agents on cron / launchd and asks "are my agents learning or just running," "why is this skill silently regressing," "set up Splunk dashboards for SFOS," or "make my long-running cron agents observable in the same place as my server logs." Designed for long-running file-based agents where LangSmith / Phoenix do not fit.
license: MIT
allowed-tools: Bash, Read, Write
metadata:
  version: "0.28.0"
  homepage: "https://github.com/alex-jb/solo-founder-os"
  pypi: "solo-founder-os"
  author: "alex-jb"
  runtime: "python3.10+"
  parent_stack: "solo-founder-os"
  hackathon: "Splunk Agentic Ops 2026 (Observability track)"
  tags: "splunk, observability, hec, agents, brier, bandit, sfos, cron"
---

# sfos-splunk-obs

Splunk adapter for [solo-founder-os](https://github.com/alex-jb/solo-founder-os) — the 11-agent autonomous stack a single founder uses to run a company.

LangSmith and Phoenix are designed for synchronous request-response agents. SFOS-obs is for the other shape: long-running cron-scheduled file-based agents that reflect on themselves and learn. This skill emits the three streams that matter — agent outcomes, eval drift, bandit regret — into Splunk over HEC.

## When to use this skill

Invoke this skill whenever the user:

- Runs solo-founder-os (or any SFOS agent) on cron / launchd and wants observability
- Asks "are my agents learning or just running"
- Reports a skill quietly regressing on its own eval set
- Wants to see bandit regret per marketing arm
- Wants the SFOS reflections / evals / bandit streams to land in the same Splunk index as their server logs
- Sets up dashboards for SFOS — import `dashboard.xml` and the six panels populate within one sweep

Do NOT invoke for short-lived synchronous agent observability (LangSmith / Phoenix fit that shape better), for hosted SaaS APM (Datadog / HyperDX), or when the user does not already have a Splunk Cloud / Enterprise stack.

## Three streams, three sourcetypes

| Sourcetype | Source | What it answers |
|---|---|---|
| `sfos:reflection` | `~/.<agent>/reflections.jsonl` | What did each agent try to do, and did it work? |
| `sfos:eval` | `~/.solo-founder-os/evals/*.json` | Is any skill silently regressing? |
| `sfos:bandit` | `~/.marketing_agent/history.db` | Which marketing arms is the bandit learning are best? |

## Five-minute install

1. **Sign up for Splunk Cloud free tier** and enable an HEC token (Settings → Data Inputs → HTTP Event Collector → New Token, sourcetypes `sfos:reflection`, `sfos:eval`, `sfos:bandit`).

2. **Set two env vars:**

   ```bash
   export SPLUNK_HEC_URL="https://<your-stack>.splunkcloud.com:8088"
   export SPLUNK_HEC_TOKEN="<paste-token>"
   ```

3. **Run one sweep to verify connectivity:**

   ```bash
   pip install solo-founder-os==0.28.0
   python3 -c "from solo_founder_os.splunk_obs.emit_loop import emit_once; print(emit_once())"
   # => {'reflections': 12, 'evals': 3, 'bandit': 41}
   ```

4. **Schedule via cron / launchd every 5 min.** Watermark files under `~/.solo-founder-os/splunk_obs/` mean a restart never double-sends.

5. **Import the dashboard.** In Splunk Cloud → Settings → User Interface → Views → Import. Upload `dashboard.xml` from `solo_founder_os/splunk_obs/`. Six panels populate within one sweep.

## Why it differs from generic OpenTelemetry adapters

- **Schemas match the streams** — `verbatim_signal` and `reflection_text` emitted as event fields so you can search free-text reflections like any other log, not as opaque JSON blobs.
- **Watermarks are per-source** — a stuck SQLite file can't block the JSONL stream. Each source advances independently.
- **No-op when unconfigured** — if `SPLUNK_HEC_URL` is unset, every emitter returns 0 and SFOS keeps running. Easy to opt in.
- **Zero new Python deps** — stdlib `urllib` + `json` + `sqlite3`. Agent install stays seconds, not minutes — important for cron servers, Vercel functions, locked-down boxes.

## Security stretch

`hec_client.verify_webhook_signature` ships HMAC-SHA256 constant-time compare for Splunk Alert Action webhooks. Couple it with the SFOS HITL queue and the "HITL rejections by agent" panel becomes the closest thing to an agentic security event log.

## Source

- `solo_founder_os/splunk_obs/hec_client.py` — 122 lines, stdlib only, retry on 5xx, constant-time webhook verify
- `solo_founder_os/splunk_obs/sfos_translator.py` — pure row → event functions per sourcetype
- `solo_founder_os/splunk_obs/emit_loop.py` — tail + watermark per source
- `solo_founder_os/splunk_obs/dashboard.xml` — six-panel Splunk dashboard, ready to import

## License

MIT, inherited from the parent `solo-founder-os` repo.
