# Security policy

## Reporting a vulnerability

Email **alex@vibexforge.com** with subject prefix `[security] solo-founder-os`.
Do NOT open a public GitHub issue for security findings. Initial
response within 72 hours; high-severity patches aim for 7 days.

## Why this repo's threat model is different

SFOS is the shared library 11 agents depend on. A vulnerability here
cascades: a flaw in the L4 evolver's safety gate could let it propose
patches to billing code; a leak in `usage.jsonl` could expose token
counts that reveal customer activity; a bypass in `SFOS_TEST_MODE`
could let test fixtures pollute production reflexion data and feed
the evolver bad signal. The blast radius is large.

## In-scope concerns

1. **L4 evolver safety-gate bypasses.** The `is_safe_path()` whitelist
   permits only `drafter.py / translator.py / cluster.py / summarizer.py
   / prompts.py / templates.py` and `prompts/ / templates/` dirs. The
   `_BLOCKED_PATTERNS` blocklist refuses anything matching `auth /
   secret / credential / smtp / stripe / billing / anthropic_client /
   migrations / .env / .git`. If you find a Haiku response that gets
   past these, that's a security bug.

2. **HITL queue tampering.** `HitlQueue.move()` validates status
   transitions. If you find a way to skip pending → approved review
   (e.g. by exploiting filename collision, race conditions, or
   path-traversal in a frontmatter field), that's a security bug.

3. **Sync-side data leakage.** `sfos-sync` ships a default
   `.gitignore` that excludes `usage.jsonl / cron-logs/ / cron/ /
   bandit.sqlite`. If you find a path where API tokens, prompt
   content, or per-machine secrets get pushed to a remote git, that's
   high severity.

4. **Test-mode bypass.** `SFOS_TEST_MODE=1` is the only thing keeping
   agent test suites from polluting production state. If you find a
   write path that ignores it, that's a security bug — it would let
   a malicious or buggy test fixture poison the L4 evolver's input.

5. **Anthropic credential exfiltration.** The client is designed to
   never log prompt content. `usage.jsonl` records token counts and
   model name, never the prompt or response. If you find a code path
   that violates this, that's high severity.

6. **Prompt-injection through reflexion signals.** L4 evolver feeds
   the `verbatim_signal` field directly into a Haiku prompt. A
   malicious agent (or compromised customer-controlled string in a
   real agent's reflexion) could try to inject instructions. The L4
   gate's hard rules (file whitelist, size limits, must-be-diff
   format) are first defense; report if you find a way around them.

## Out of scope

- Third-party dependencies (`anthropic`, `streamlit`, `pydantic`,
  etc.) — report upstream.
- Issues that require an attacker who already controls the operator's
  shell, filesystem, or `~/.zshrc`.
- The downstream agent repos — report at their respective GitHub
  issues (or to the maintainer email above for cross-stack issues).
- The vibexforge.com web app — report at github.com/alex-jb/vibex.

## Hygiene practices in this repo

- **Never log prompt content.** `AnthropicClient.usage_log` records
  tokens + cost, never input/output text.
- **PR-gated, never auto-merged.** L4 evolver writes markdown
  artifacts only. Operator-driven `gh pr create` is required.
- **Hard whitelist + blocklist** on what the evolver can touch (see
  `evolver._SAFE_NAME_PATTERNS / _BLOCKED_PATTERNS`).
- **Confidence-banded routing** in HITL queue: > 90% auto-acted,
  70-90% queued for review, < 70% reject-with-reason.
- **Sterile-import pre-flight** in `sfos-cron install` refuses to
  schedule jobs that would fail silently every Sunday.
- **`SFOS_TEST_MODE=1` umbrella** in every agent's conftest prevents
  test fixtures from writing to `~/.<agent>/`.

If you find a deviation from any of the above, treat it as a security
issue and report.

## Defense-in-depth choices already made

| Choice | Why |
|---|---|
| L4 hard whitelist + blocklist | Even if the LLM is jailbroken, it can't propose touching billing code. |
| HITL queue is filesystem-backed | Markdown files are inspectable; binary protocols aren't. |
| `usage.jsonl` excludes prompt content | Even if the file leaks, no customer data goes with it. |
| `sfos-sync` `.gitignore` excludes secrets + per-machine state | Multi-machine sync via private GitHub repo can't accidentally publish credentials. |
| Streamlit dashboard is local-only by default | No `--host 0.0.0.0` flag, no auth-less network exposure. |
| Reflexion + eval cost is bounded | Sonnet-as-judge at 5 rows/skill × 5 skills = ~$0.04/week. Hard cap on auto-spend. |
