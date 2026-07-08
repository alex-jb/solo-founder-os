# SFOS Onboarding by Audience

Solo Founder OS ships 11 agents. Installing all 11 on day one is overwhelming. This document forks new users into 4 audience tracks so each user installs only the agents that match their job-to-be-done. The pattern is borrowed from Injective's dev-onboarding fork (EVM / CosmWasm / DeFi / Native modules), adapted to indie-tooling.

Pick the track that matches your current work, install the 3-5 named agents, ship for two weeks, then come back for the rest. The stack is designed so you can add agents later without re-installing the shared library.

## Track 1: Indie hacker

You are a solo founder building for money. You care about distribution, funnel visibility, and burn control. You do not care yet about client accounting or bilingual OSS hygiene.

**Install these 4 agents:**

- `marketing-agent` — auto-promo across 12 platforms + proactive loop
- `funnel-analytics-agent` — daily brief + real-time alerts + PH-day kit
- `cost-audit-agent` — monthly bill audit across 6 providers
- `vc-outreach-agent` (in `--mode customer` for early paying customers)

**Install command:**

```bash
pip install solo-founder-os marketing-agent funnel-analytics-agent cost-audit-agent vc-outreach-agent
```

**What to skip for now:** payments-agent, customer-support-agent, bilingual-content-sync-agent, customer-discovery-agent, build-quality-agent. Come back for these after you have 5 paying customers.

**Time-to-first-value:** two weeks. First marketing draft ships within an hour; funnel dashboards populate after your first cron fire; cost-audit lands the first monthly brief on the 1st.

## Track 2: OSS maintainer

You are shipping open source and care about community, credibility, and cross-language reach. You do not care yet about payments or funnel dashboards.

**Install these 4 agents:**

- `build-quality-agent` — pre-push diff reviewer + local build runner
- `marketing-agent` — auto-promo (English + Chinese) for release announcements
- `bilingual-content-sync-agent` — EN/ZH i18n diff + Claude translate
- `customer-support-agent` — triage GitHub issues + draft replies

**Install command:**

```bash
pip install solo-founder-os build-quality-agent marketing-agent bilingual-content-sync-agent customer-support-agent
```

**What to skip for now:** payments-agent, funnel-analytics-agent, cost-audit-agent, vc-outreach-agent, customer-discovery-agent. Come back for these when you have paying enterprise users or a formal sales lane.

**Time-to-first-value:** one week. Build-quality-agent catches your next pre-push regression; marketing-agent drafts your next release post; bilingual-sync keeps your zh-CN README from drifting.

## Track 3: Freelance dev

You are billing hourly or by project. You care about payments discipline and client communications. You do not care yet about OSS community or English-only marketing.

**Install these 4 agents:**

- `payments-agent` — overdue-invoice reminder drafter with money-safety guarantees
- `customer-support-agent` — triage client emails + draft replies
- `cost-audit-agent` — monthly bill audit (protect your margin)
- `marketing-agent` — portfolio-building drafts (LinkedIn + dev.to)

**Install command:**

```bash
pip install solo-founder-os payments-agent customer-support-agent cost-audit-agent marketing-agent
```

**What to skip for now:** funnel-analytics-agent, vc-outreach-agent, customer-discovery-agent, build-quality-agent, bilingual-content-sync-agent. Come back for these when you productize a service offering.

**Time-to-first-value:** immediate. Payments-agent surfaces the next overdue invoice on install; customer-support drafts your next reply within a day; cost-audit lands the first bill review on the 1st.

## Track 4: Research lab

You are producing research output (papers, benchmarks, datasets) with a small team. You care about output distribution, cross-language reach, and reproducibility. You do not care yet about payments or funnel visibility.

**Install these 5 agents:**

- `customer-discovery-agent` — Reddit pain-point scraper + Claude clustering
- `bilingual-content-sync-agent` — EN/ZH i18n for paper abstracts + docs
- `build-quality-agent` — reproducibility guard for eval + benchmark repos
- `marketing-agent` — announcements for paper releases + dataset drops
- `funnel-analytics-agent` — track paper citations + benchmark leaderboard positions

**Install command:**

```bash
pip install solo-founder-os customer-discovery-agent bilingual-content-sync-agent build-quality-agent marketing-agent funnel-analytics-agent
```

**What to skip for now:** payments-agent, cost-audit-agent, vc-outreach-agent, customer-support-agent. Come back for these when your research spins out into a product.

**Time-to-first-value:** one week. Customer-discovery surfaces the pain-cluster that a next paper could target; marketing-agent drafts your paper-release thread; funnel tracks citation growth over time.

## Not sure which track fits?

Ask yourself which of these is your biggest current problem, right now, today:

- "I can't get in front of enough people" → **Indie hacker (Track 1)**
- "I need my OSS repo to feel more polished + more international" → **OSS maintainer (Track 2)**
- "I keep chasing invoices + losing time to client email" → **Freelance dev (Track 3)**
- "I need my research to reach more eyes without hiring a marketing person" → **Research lab (Track 4)**

Still not sure? Install the shared library only:

```bash
pip install solo-founder-os
```

Then run `sfos-doctor` to get a per-agent recommendation based on the git repos + Anthropic key setup already on your machine.

## Adding more agents later

The stack is designed so you can add agents incrementally without re-installing the shared library. When you outgrow your starting track, install the next agent by name:

```bash
pip install <new-agent-name>
```

All 11 agents share the same `solo_founder_os` library — reflection logs, ICPL preferences, and evolver proposals compose across agents even if you install them at different times.

## Full 11-agent list

See `README.md` §"The stack (11 agents, 7 canonical layers)" for the complete list plus the 7 canonical one-person-company layers each agent covers.

## Related documents

- `README.md` — SFOS overview + Quick start
- `README.zh-CN.md` — Chinese-language overview
- `CONTRIBUTING.md` — how to add a 12th agent (spoiler: talk to Alex first)
- `docs/architecture-principles.md` — 5 architectural priors driving these choices
