# Architecture principles — why the 11-agent stack is shaped this way

**Status**: v0.1 — living document. New principles get added with empirical justification, not by aesthetic argument.
**Audience**: contributors, integrators, and anyone evaluating whether to fork SFOS as a base for their own multi-agent stack.

This is the *why* doc. For the *what* / the graphs / the cron wiring, see [`architecture_diagram.md`](../architecture_diagram.md).

---

## Principle 1 — Hold-as-needed, not hold-always

> *"持有即负担,用完即释放,需要时再 gather."*
> (Holding is a burden. Release on use. Gather on demand.)

The single most load-bearing design principle in SFOS. It's borrowed from a GPU-training optimization (DeepSpeed ZeRO Stage 3), but the underlying axiom is general: **a system that keeps a full local copy of every piece of state every agent might need at any time hits a scale wall that doesn't go away by buying bigger context windows.**

### Where the principle came from — ZeRO Stage 3

In multi-GPU training, each card naively holds three things: optimizer state, gradients, and model parameters. For a 7B-parameter model with fp32 Adam, that's roughly 112 GB per card — single-card training is impossible. The naive distributed-data-parallel fix is to replicate all three on every card, which is wasteful: you've paid 8× the memory for redundant state.

ZeRO Stage 3 shards all three across cards and does on-demand all-gather of parameters when each layer needs them, then immediately releases. The memory cost per card drops from Ψ to Ψ/N, at the price of an all-gather + reduce-scatter per forward / backward step.

The interesting mathematical observation: the communication overhead is real but **bounded** (3× extra per step). The memory savings are linear. Past a certain N, the trade is dominant.

### Why it matters at the multi-agent level

Replace "GPU" with "agent" and "parameter shard" with "context window":

| Anti-pattern | Pattern |
|---|---|
| Every agent keeps its own complete persistent context buffer | Agents hold the minimum needed for the current invocation; everything else lives in a shared store and is gather-on-demand |
| 11 agents × 200KB context each = 2.2 MB of mostly-redundant overlap | One canonical store keyed by domain + ticker + decision-id; agents fetch the shard they need, drop it after |
| Naive RAG: stuff everything into one big context window | Distributed: index lives outside the LLM; selection is a search problem, not a window-stuffing problem |

SFOS implements this concretely through:

- **`HitlQueue`** is the markdown drop-zone — state crosses between agents through one shared filesystem location, not by passing context windows
- **`sfos-bus`** is the inter-agent message bus — agents send what's needed for the receiver to act, not their full state
- **`AnthropicClient`** sets `metadata: {"persist": false}` on calls that don't need it, so the provider-side cache doesn't accumulate either
- The 2026-06 OSS landscape — Elastic's agent-memory paper, the Spatial Cognitive Platform's emerging "outside the context window" search layer — all converge on the same axiom

### Why this principle is in this doc, not just in the code

Because every contributor who adds a 12th agent will face the temptation to give it its own persistent buffer. That's the wrong default. Read this doc first; the answer is almost always "use the shared store, gather on demand, release after."

### What we don't do (corollary)

- We don't pre-load every agent's prompt into every other agent's context "just in case." Agents introspect on demand via `sfos-doctor`.
- We don't cache long histories inside agent processes. The cache lives in JSONL + SQLite on disk; processes are stateless between cron fires.
- We don't run any agent as a long-lived daemon (with the deliberate exception of `vibex-publish-agent` whose retry queue is the explicit reason it's daemon-shaped).

---

## Principle 2 — One canonical interface, many adapters

`Source` ABC + `Notifier` ABC + `LlmAdapter` (added 2026-06-21 in council-diff#1).

The pattern: define the minimum contract once, write the wire-up once, then add adapters as the world changes. The 2026-06-21 OpenAI fallback added to `daily_brief.py` and the distill cron is exactly this — one extra adapter, no rewrites in the consumer code.

---

## Principle 3 — Calibration over confidence

Every prediction layer in SFOS that emits a verdict is Brier-audited at resolution. The Brier loop is non-optional, not a debug feature. This is the same observation Karpathy made publicly in mid-2026 about "the production layer LLM Council was always missing" — calibration is the moat, not the framework.

---

## Principle 4 — HITL by code-level invariant, not by hope

Agents that touch money, public communication, or external commitments emit to a HITL queue. The queue is a literal directory of markdown files Alex reviews. The agents never auto-fire those actions. This is enforced at the code path — there's no "auto-approve" flag, by design.

---

## Principle 5 — Operator-side first, hosted SaaS second (or never)

SFOS is shipped as `pip install solo-founder-os` plus a `~/.local/bin` symlink set. There is no hosted multi-tenant version, no "log into our cloud" flow, no signup. This is deliberate — see [`rules/we-dont-do.md` in alex-brain](https://github.com/alex-jb/alex-brain) for the cost/pool analysis. Hosted SFOS may exist someday; it isn't the v1.

---

## How a contributor uses this doc

If you are proposing a change that:

- Adds persistent in-agent state → re-read Principle 1, justify why the shared store doesn't fit.
- Adds a new external service dependency → re-read Principle 2, propose the adapter shape, not just the SDK import.
- Bypasses Brier scoring for "obvious" predictions → re-read Principle 3, those are exactly the ones to keep scoring (the obvious-then-wrong is the most valuable signal).
- Auto-fires anything visible-to-others → re-read Principle 4, the answer is HITL queue.
- Proposes a cloud-hosted version → re-read Principle 5 + check the brain's we-dont-do ledger before opening the issue.

These principles are not laws. They're priors. When the evidence shifts (a 2027 paper proves persistent context buffers scale; a 2028 customer ask makes a hosted SaaS the right move) the principle gets edited, with the empirical reason in the commit message.

---

## References

- DeepSpeed ZeRO: Rajbhandari et al., "ZeRO: Memory Optimizations Toward Training Trillion Parameter Models" (2020). Stage 1/2/3 partition reference.
- Elastic agent-memory paper (2026-06) — production case study for the "memory layer outside the context window" pattern.
- Karpathy on "council of LLM judges + verifiable + judgment as the new coding skill" (2026-06 public statement).
- Anthropic Skills standard (2026-06-09) — the broader industry convergence on "skill discovery outside the context window."

---

🤖 Initial scaffold 2026-06-22. Surfaced by alex-brain daily-brief 2026-06-20 §6 (Day-40 ZeRO curriculum) as a pattern that needed to be promoted from training-trick to architectural prior.
