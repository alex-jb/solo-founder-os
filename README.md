# solo-founder-os

**English** | [中文](README.zh-CN.md)

> The 6-layer self-evolving agent stack a one-person company actually runs.
> Local-first. Zero cloud infra. Sub-$0.06/week autonomous spend.

[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/solo-founder-os.svg)](https://pypi.org/project/solo-founder-os/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](#)

## What it does

Solo Founder OS is the shared library + CLI suite that runs the
[11-agent indie-founder stack](#the-stack-11-agents-7-canonical-layers).
Five things no other agent platform does together:

1. **Reads plain Python agents.** No SDK injection. Agents write JSONL/markdown
   to `~/.<agent>/`; SFOS tails those files. No re-instrumentation.
2. **Closes the L1↔L4↔L5↔L6 self-improving loop end-to-end.** Reflexion logs
   feed an evolver that opens PR-gated proposals; an eval judge surfaces
   quality drift; a council deliberates on severe drops; the council's
   conclusions flow back into the evolver's Haiku prompt. All on cron.
3. **Local-first observability.** `sfos-ui` is a Streamlit dashboard
   reading the same JSONL files — no Phoenix instance, no Langfuse Docker,
   no LangSmith account.
4. **Cron-aware, not request-aware.** Built for agents that wake up Sunday
   morning, not real-time chatbots. Avoids the alert-fatigue anti-pattern.
5. **Solo-founder framing.** "What needs you today" inbox, not enterprise
   team/project hierarchy.

## Quick start

```bash
pip install 'solo-founder-os[anthropic,ui]'

# Schedule the weekly self-improvement loop on launchd:
sfos-cron install
# Sun 08:00  sfos-eval         judge skills with Sonnet
# Sun 08:30  sfos-council      multi-perspective on severe drift
# Sun 09:00  sfos-evolver      Haiku synthesizes PR proposals
# Sun 09:30  sfos-retro        cross-agent weekly digest

# Open the local dashboard:
sfos-ui
# → http://localhost:8501

# Sync state across machines via git:
sfos-sync init git@github.com:you/sfos-state.git
sfos-sync push
```

That's it. No accounts. No cloud. Cost ceiling $0.06/week.

## The 6-layer self-improving loop

```
L1  Reflexion         ─┐  log_outcome(agent, task, FAILED, signal)
L2  Supervisor        ─┤  launchd cron driver
L3  Skills            ─┤  record_example(skill, inputs, output)
L4  Evolver           ─┼─→  Haiku synthesizes PR proposals from
L5  Council           ─┤    L1 patterns + L6 drift, with L5 council
L6  Eval (Sonnet)     ─┘    deliberation injected as prompt context
```

Every Sunday morning the loop runs:

1. **08:00 — `sfos-eval`** judges the last 5 `record_example` rows per skill
   using a 5-axis Sonnet rubric (clarity / specificity / voice / accuracy /
   completeness). Persists per-skill scores. Detects drift > 0.5 vs prior week.
2. **08:30 — `sfos-council --auto-from-drift`** convenes a `BUG_TRIAGE`
   meeting (3 perspectives + 1 synthesis) for any skill whose mean dropped
   > 0.7. Saves notes to `council-meetings/`.
3. **09:00 — `sfos-evolver`** scans reflexion logs for ≥3× recurring failure
   patterns AND reads L6 drift signals. For each, it asks Haiku for a
   concrete fix. **If the L5 council deliberated on this skill, the synthesis
   is injected into the Haiku prompt as additional context** — the patch
   reflects multi-angle reasoning, not one-shot guess. Output: markdown
   artifacts in `evolver-proposals/` (PR-gated; never auto-merged).
4. **09:30 — `sfos-retro`** walks every agent's reflexion / preference / skill
   data and produces ONE markdown digest answering: who's running, what's
   each failing at most, which skills are bubbling up, which variants are
   winning their bandits.

Costs: ~$0.04/week for evals + ~$0.01/week for evolver + ~$0.005/week
for council meetings (when drift is severe). Total **<$0.06/week**.

## ICPL preference learning through the Inbox

The `sfos-ui` Inbox tab is the canonical HITL approval surface. When you
edit a draft before clicking ✅ Approve, the diff is logged as an ICPL
(In-Context Preference Learning) pair to `~/.<agent>/preference-pairs.jsonl`.

Next time the same agent drafts a similar task, `preference_preamble()`
pulls those pairs as few-shot exemplars in the system prompt. The agent
drifts toward your voice without you writing a single new prompt.

Task disambiguation: `task` field in frontmatter wins; falls back to
`platform` (marketing-agent uses this for X / LinkedIn / Reddit), then
`kind`, then `<slug>-draft`.

## CLI suite

| CLI | What it does |
|---|---|
| `sfos-doctor` | Health-check all known agent dirs |
| `sfos-supervisor` | L2 — find work for agents (auto-trigger) |
| `sfos-evolver` | L4 — propose PR-gated patches from reflexion + drift |
| `sfos-council` | L5 — multi-agent deliberation; `--auto-from-drift` mode |
| `sfos-eval` | L6 — Sonnet judges record_example rows |
| `sfos-retro` | Cross-agent weekly digest |
| `sfos-bus` | Cross-terminal markdown broadcast |
| `sfos-inbox` | HITL governance rail (CLI alternative to sfos-ui) |
| `sfos-cron install` | Schedule the 4-job Sunday loop on launchd |
| `sfos-ui` | Local Streamlit dashboard (4 tabs) |
| `sfos-sync` | Multi-machine git-based sync of `~/.solo-founder-os/` |

## The stack (11 agents, 7 canonical layers)

| Layer | Agent |
|---|---|
| 1. Content / marketing | [orallexa-marketing-agent](https://github.com/alex-jb/orallexa-marketing-agent) |
| 2. Customer support | [customer-support-agent](https://github.com/alex-jb/customer-support-agent) |
| 3. Customer discovery | [customer-discovery-agent](https://github.com/alex-jb/customer-discovery-agent) |
| 4. Customer outreach (cold sales) | [customer-outreach-agent](https://github.com/alex-jb/customer-outreach-agent) |
| 5. Investor outreach | [vc-outreach-agent](https://github.com/alex-jb/vc-outreach-agent) |
| 6. Analytics + cost | [funnel-analytics-agent](https://github.com/alex-jb/funnel-analytics-agent), [cost-audit-agent](https://github.com/alex-jb/cost-audit-agent) |
| 7. Monetization | [payments-agent](https://github.com/alex-jb/payments-agent) |
| (cross-cutting) | [build-quality-agent](https://github.com/alex-jb/build-quality-agent), [bilingual-content-sync-agent](https://github.com/alex-jb/bilingual-content-sync-agent) |

Each agent is its own pip package + CLI + optional MCP server. They share
this library for HITL queue, Anthropic client, reflexion logging, skill
distillation, and the L1–L6 loop primitives.

## Test isolation

Set `SFOS_TEST_MODE=1` in your test suite's `conftest.py`:

```python
# tests/conftest.py
import os
os.environ.setdefault("SFOS_TEST_MODE", "1")
```

This gates `log_outcome`, `record_example`, and `log_edit` from writing to
`~/.<agent>/` during pytest. Without it, agent test fixtures pollute
production reflexion data and feed false-positive proposals to L4 evolver.
(All 8 stack agents already adopt this.)

## Privacy

- All state lives in `~/.<agent>/` and `~/.solo-founder-os/`.
- `sfos-sync` ships a default `.gitignore` that excludes `usage.jsonl`,
  `cron-logs/`, `cron/`, `bandit.sqlite` so Stripe-shaped tokens, cost logs,
  and per-machine state never reach a remote git repo.
- The Anthropic client logs cost (input/output tokens) to `usage.jsonl` —
  never the prompt content.
- The L4 evolver's safety gate refuses any patch touching `auth`, `secret`,
  `credential`, `smtp`, `stripe`, `billing`, `anthropic_client`, or
  `migrations` paths even if Haiku tries to suggest them.

## Why these design choices

- **Streamlit, not React/FastAPI** — research showed solo founders use
  ~30 min/day across 2 batches; real-time monitoring is alert-fatigue
  anti-pattern at this volume. `st.fragment(run_every="3s")` gives "live
  feel" via local polling, no second process.
- **Vertical timeline of file events, not chat-bubble metaphor** — SFOS
  agents communicate ASYNCHRONOUSLY via files. Chat bubbles would
  misrepresent the architecture.
- **Default cron threshold 0.7 for council, 0.5 for evolver** — councils
  cost ~5 Haiku calls each. Stricter trigger keeps weekly autonomous spend
  under $0.06 even if drift is constantly firing.
- **PR-gated, never auto-merged** — the evolver writes markdown artifacts
  for review. Hard whitelist + blocklist on what files can be touched.

## Status

v0.26 (2026-05-02). Production loop is live on the maintainer's machine;
first auto-fire 2026-05-04 Sunday. **504 tests** across the library;
**1100+ tests** across the full 11-agent stack.

## License

MIT. Use freely.
