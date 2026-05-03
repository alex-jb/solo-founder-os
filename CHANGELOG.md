# Changelog

All notable changes to `solo-founder-os` from v0.10 onward. Each entry
is one or two sentences; the commit messages are the source of truth.

## v0.26.0 — 2026-05-02

`sfos-sync` for multi-machine git-based sync of `~/.solo-founder-os/`. Per-agent
dirs stay local; shared layer (skills / evals / proposals / council notes /
preferences / retros) follows the operator across machines.

## v0.25.1 — 2026-05-02

Suppress harmless `runpy` RuntimeWarning in cron-logs (`-W ignore::RuntimeWarning:runpy`).
Inbox A/R keyboard shortcuts via `streamlit-shortcuts<0.2`.

## v0.25.0 — 2026-05-02

L5 → L4 text injection: `sfos-evolver` reads `council-meetings/*.md` and
injects the synthesis into the Haiku synthesize prompt for matching drift
patterns. End-to-end loop closed.

## v0.24.1 — 2026-05-02

`sfos-council --auto-from-drift` added to launchd cron at Sunday 08:30,
between `sfos-eval` (08:00) and `sfos-evolver` (09:00).

## v0.24.0 — 2026-05-02

L5 ↔ L6 wire: `convene_drift_council(skill, drift)` + `auto_convene_from_drift`
+ CLI `--auto-from-drift` flag. Default threshold 0.7 (severe drops only).

## v0.23.1 — 2026-05-02

`_scan_bandit()` now scans multiple SQLite locations
(`~/.solo-founder-os/bandit.sqlite`, `~/.marketing_agent/history.db`,
alt paths) so `sfos-retro` sees marketing-agent's bandit data. Each
result tagged with source DB.

## v0.23.0 — 2026-05-02

ICPL preference logging wired through Inbox edit flow. Editing a draft
before approve writes the (original, edited) pair to
`~/.<agent>/preference-pairs.jsonl` with task inferred from frontmatter
(`task` > `platform` > `kind` > `<slug>-draft`).

## v0.22.0 — 2026-05-02

`sfos-ui` v2: research-driven 4-tab redesign (Morning Brief / Inbox /
Stack Flow / Status). Inbox split-pane with Approve / Reject buttons +
`st.fragment(run_every=3)` auto-refresh. Stack Flow timeline replaces
chat-bubble metaphor.

## v0.21.0 — 2026-05-02

`sfos-ui` v1: local Streamlit dashboard. Stack-status badges, activity
timeline, pending HITL, eval quality trends, cron log tail. `[ui]`
optional dep.

## v0.20.3 — 2026-05-02

`SFOS_TEST_MODE=1` umbrella env var gates `log_outcome`, `record_example`,
and `log_edit` writes during pytest. Eight stack agents adopt this in
their `conftest.py` to stop polluting `~/.<agent>/` from test runs.

## v0.20.2 — 2026-05-02

`SFOS_LOG_OUTCOME_SKIP=1` gate on `log_outcome` (later promoted to
`SFOS_TEST_MODE`).

## v0.20.1 — 2026-05-02

CRITICAL fix: drop falsy `system` kwarg before forwarding to Anthropic
API. Without this, every `system=None` caller (e.g. eval judge) was
getting HTTP 400 silently turned into None returns.

## v0.20.0 — 2026-05-02

Register `customer-support-agent` and `customer-outreach-agent` in
`KNOWN_AGENT_DIRS` and `DEFAULT_AGENT_REPOS` so the new agents are
visible to retro and evolver.

## v0.19.0 — 2026-05-02

`sfos-cron install` schedules the weekly self-improvement loop on
launchd: eval (Sun 08:00), evolver (09:00), retro (09:30). Wrapper
scripts source `~/.zshrc` to pick up `ANTHROPIC_API_KEY`.

## v0.18.0 — 2026-05-02

L4 → L6 wire: `find_drift_patterns()` converts L6 `detect_drift` outputs
into `FailurePattern` objects so the evolver synthesizes patches for
quality-drift signals alongside reflexion-driven failures.

## v0.17.0 — 2026-05-02

`sfos-retro`: cross-agent weekly digest. Walks every agent's reflexion /
preference / skill / bandit data; produces ONE markdown for the
operator to read.

## v0.16.0 — 2026-05-02

L6 `sfos-eval`: Sonnet-judge over `record_example` data with 5-axis
rubric (clarity / specificity / voice / accuracy / completeness).
Persists per-skill scores; `detect_drift` flags > 0.5 mean drops.

## v0.15.0 — 2026-05-02

`sfos-inbox`: HITL governance rail consolidating per-agent queue
files into one approval surface with audit log.

## v0.14.0 — 2026-05-02

`sfos-bus`: cross-terminal markdown broadcast for coordinating multiple
Claude Code instances on the same project.

## v0.13.0 — 2026-05-01

Bandit + autopsy primitives promoted from marketing-agent into the
shared library.

## v0.12.0 — 2026-05-01

L5 council: multi-agent meeting module. Predefined `LAUNCH_READINESS`,
`PRICING_DECISION`, `BUG_TRIAGE` councils. ICPL preference learning
helpers (`log_edit`, `recent_edits`, `preference_preamble`).

## v0.11.0 — 2026-05-01

L4 `sfos-evolver`: PR-gated self-improvement. Detects ≥3× recurring
failure patterns in reflexion logs; Haiku synthesizes patches; markdown
artifacts in `~/.solo-founder-os/evolver-proposals/`. Hard whitelist +
blocklist on touchable files.

## v0.10.0 and earlier

L1 reflexion (`log_outcome`), L2 supervisor primitives, L3 skills
(`record_example`, `distill_skill`), HITL queue, scheduler, batch.py,
Anthropic client with auto cost log, notifiers (ntfy / Telegram /
Slack), `sfos-doctor`, brief composer, testing helpers.
