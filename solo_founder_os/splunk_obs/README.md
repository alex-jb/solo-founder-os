# sfos-splunk-obs

Splunk adapter for [solo-founder-os](https://github.com/alex-jb/solo-founder-os) — the 11-agent autonomous stack a single founder uses to run a company.

**Submission for the Splunk Agentic Ops Hackathon 2026.**

> LangSmith and Phoenix are designed for synchronous request-response agents. SFOS-obs is for the other shape: long-running cron-scheduled file-based agents that reflect on themselves and learn. This package emits the three streams that matter — agent outcomes, eval drift, bandit regret — into Splunk over HEC. 5-minute install. Zero new Python deps.

> **"Almost everything can be made verifiable to some extent... the skill being built right now is judgment: what to delegate, how to specify it, how to review it fast."** — Andrej Karpathy on agentic engineering, June 2026.
>
> sfos-splunk-obs is the **verification surface** for that judgment. Reflections are the agent reviewing itself in plain text; eval drift is the auditor measuring whether the review predicted reality; bandit regret is the realized-vs-expected ledger of every promotion choice the marketing agent made. All three land in Splunk so a single dashboard answers "is this agent learning, or just running?"

---

## Three streams, three sourcetypes

| Sourcetype | Source | What it answers |
|---|---|---|
| `sfos:reflection` | `~/.<agent>/reflections.jsonl` | What did each agent try to do, and did it work? |
| `sfos:eval` | `~/.solo-founder-os/evals/*.json` | Is any skill silently regressing? |
| `sfos:bandit` | `~/.marketing_agent/history.db` | Which marketing arms is the bandit learning are best? |

## 5-minute install

1. **Sign up for Splunk Cloud free tier**, enable an HEC token (Settings → Data Inputs → HTTP Event Collector → New Token, sourcetypes `sfos:reflection`, `sfos:eval`, `sfos:bandit`).

2. **Set two env vars** (in your shell rc or via `launchctl setenv`):

   ```bash
   export SPLUNK_HEC_URL="https://<your-stack>.splunkcloud.com:8088"
   export SPLUNK_HEC_TOKEN="<paste-token>"
   ```

3. **Run one sweep** to verify connectivity:

   ```bash
   python3 -c "from solo_founder_os.splunk_obs.emit_loop import emit_once; print(emit_once())"
   # => {'reflections': 12, 'evals': 3, 'bandit': 41}
   ```

4. **Schedule it via cron / launchd** every 5 min — the watermark files under `~/.solo-founder-os/splunk_obs/` mean a restart never double-sends.

5. **Import the dashboard**: in Splunk Cloud, Settings → User Interface → Views → Import. Upload `dashboard.xml` from this folder. You'll see all 6 panels populated within one sweep.

## Dashboard panels

- **Panel 1 — Agent Run Trace (24h)**: table of every agent x outcome over the last day, plus the last 50 reflections so you can read the verbatim signal that triggered each entry.
- **Panel 2 — Eval Drift**: per-skill mean Brier score over 30 days. Lower = the prompt is regressing on examples it used to do well on. Companion panel surfaces any skill whose latest score dropped below 2.5/5 so you can act before users notice.
- **Panel 3 — Bandit Regret**: cumulative regret per marketing-agent arm. Lets you see the moment the bandit "wakes up" and starts exploiting a winning arm vs. exploring blindly.

## Why it differs from generic OpenTelemetry adapters

- **Schemas match the streams**: emitting `verbatim_signal` and `reflection_text` as event fields means you can search free-text reflections in Splunk like any other log, not as opaque JSON blobs.
- **Watermarks are per-source**: a stuck SQLite file can't block the JSONL stream. Each source advances independently.
- **No-op when unconfigured**: if `SPLUNK_HEC_URL` is unset, every emitter returns 0 and the rest of SFOS keeps running. Easy to opt in.
- **Zero new Python deps**: stdlib `urllib` + `json` + `sqlite3`. The agent install stays seconds, not minutes — important for cron servers, Vercel functions, locked-down boxes.

## Security track stretch

`hec_client.verify_webhook_signature` ships HMAC-SHA256 constant-time compare for Splunk Alert Action webhooks. Couple it with the SFOS HITL queue and you get a record of every outbound message attempt the founder caught the agent about to send incorrectly — Splunk panel "HITL rejections by agent" is the closest thing to an agentic security event log.

## Source

- [`hec_client.py`](./hec_client.py) — 122 lines, stdlib only, retry on 5xx, constant-time webhook verify
- [`sfos_translator.py`](./sfos_translator.py) — pure row→event functions per sourcetype
- [`emit_loop.py`](./emit_loop.py) — tail + watermark per source
- [`dashboard.xml`](./dashboard.xml) — 6-panel Splunk dashboard, ready to import

## License

MIT, same as the parent solo-founder-os repo.
