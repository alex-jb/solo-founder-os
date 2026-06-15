# Architecture Diagram

**Solo Founder OS · splunk_obs + sfos-evolver — closed-loop agent ops**

## High-level loop

```mermaid
graph TB
    subgraph AGENTS["11 AI Agents on launchd cron ($0.06/week)"]
        direction LR
        A1[marketing-agent]
        A2[funnel-analytics]
        A3[customer-discovery]
        A4[vc-outreach]
        A5[cost-audit]
        A6[bilingual-content-sync]
        A7[build-quality]
        A8[customer-support]
        A9[payments-agent]
        A10[vibex-publish]
        A11[solo-founder-os]
    end

    subgraph DATA["Local data layer (JSONL + SQLite)"]
        D1["~/.solo-founder-os/evals/*.json<br/>(sfos:eval)"]
        D2["~/.&lt;agent&gt;/reflections.jsonl<br/>(sfos:reflection)"]
        D3["~/.marketing_agent/history.db<br/>(sfos:bandit)"]
    end

    subgraph EYES["splunk_obs — the eyes (stdlib only, 200 LOC)"]
        E1[emit_loop.py<br/>watermark per source]
        E2[hec_client.py<br/>urllib + retry]
        E3[sfos_translator.py<br/>row to event]
    end

    subgraph SPLUNK["Splunk Enterprise (HEC, port 8088)"]
        S1[sfos:reflection]
        S2[sfos:eval]
        S3[sfos:bandit]
        S4[dashboard.xml<br/>6 panels: drift / regret / outliers]
    end

    subgraph HANDS["sfos-evolver — the hands (weekly cron)"]
        EV1[find_drift_patterns<br/>evolver.py:223]
        EV2[synthesize_proposal<br/>+ council notes]
        EV3[Claude Haiku<br/>code patch]
        EV4[GitHub PR<br/>NO auto-merge]
    end

    A1 & A2 & A3 & A4 & A5 & A6 & A7 & A8 & A9 & A10 & A11 --> D1
    A1 & A2 & A3 & A4 & A5 & A6 & A7 & A8 & A9 & A10 & A11 --> D2
    A1 --> D3

    D1 --> E3
    D2 --> E3
    D3 --> E3
    E3 --> E1
    E1 --> E2
    E2 --> S1
    E2 --> S2
    E2 --> S3
    S2 --> S4

    D1 --> EV1
    EV1 --> EV2
    EV2 --> EV3
    EV3 --> EV4
    EV4 -.->|human review + approve| A1
```

## Closed-loop sequence

1. **11 agents** write JSONL traces to `~/.solo-founder-os/` and `~/.<agent>/` directories.
2. **sfos-eval** (L6 of the stack) scores each example 1 to 5 with a Claude judge (Brier-style).
3. **splunk_obs** tails 3 streams and emits to Splunk HEC every 5 minutes (per-source watermark, dedup-safe across restarts).
4. **6-panel dashboard** surfaces drift in Splunk:
   - Panel 1: Agent Run Trace (24h)
   - Panel 2: Eval Drift over time (per skill mean Brier, 30d)
   - Panel 2b: Low-tail (p10) Drift Alert table
   - Panel 3: Bandit Regret cumulative
   - Plus 2 more for reflections + HITL rejections
5. **sfos-evolver** reads the same drift signal weekly. When a skill regresses >0.5 mean across two runs OR a failure repeats >=3 times, it asks Claude Haiku to propose a patch.
6. **Hard whitelist**: prompts / schemas / error-strings / markdown only. **Never** touches auth / network / money code (see `is_safe_path` in `evolver.py:87`).
7. **GitHub PR is the gate**. Human reviews and approves. No auto-merge.

## Key files

| Path | Role | LOC |
|---|---|---|
| `solo_founder_os/splunk_obs/hec_client.py` | stdlib HEC client + HMAC webhook verify | 122 |
| `solo_founder_os/splunk_obs/sfos_translator.py` | row to event per sourcetype | 80 |
| `solo_founder_os/splunk_obs/emit_loop.py` | tail + watermark per source | 110 |
| `solo_founder_os/splunk_obs/dashboard.xml` | 6-panel Splunk dashboard | XML |
| `solo_founder_os/evolver.py` | find_drift_patterns + synthesize_proposal + GitHub PR | 672 |
| `solo_founder_os/eval.py` | Brier judge over record_example data | 240 |
| `solo_founder_os/council.py` | 5-voice council + ICPL preference logging | 180 |

## Why this shape

- **Stdlib only**: no `requests`, no Splunk SDK, no new pip deps. Enterprise pip install politics avoided.
- **HEC + Classic Dashboard XML**: works on every Splunk version since 2014.
- **Disk-based bus**: agents communicate through JSONL files, not a message queue. Restart is free.
- **PR-gated evolver**: Reflexion + DGM papers showed self-improvement works, but only when constrained. The whitelist was locked before the first Haiku prompt was written.

License: MIT
