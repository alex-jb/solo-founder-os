# solo-founder-os-evals — methodology

**HuggingFace dataset:** `alexji/solo-founder-os-evals`
**License (data):** CC-BY-4.0
**License (code that produced it):** MIT (this repo, [solo-founder-os](https://github.com/alex-jb/solo-founder-os))
**Maintainer:** Alex Ji ([@alexji](https://github.com/alex-jb))
**Last updated:** 2026-06-14

---

## What this is

This dataset is a public dump of the eval traces from
[Solo Founder OS](https://github.com/alex-jb/solo-founder-os) — an
11-agent stack that one operator (Alex Ji) runs on launchd cron to
automate marketing, customer discovery, VC outreach, build review,
bilingual translation, and operational ops.

Every Sunday a job called `sfos-eval` samples the most recent
examples each agent produced, asks `claude-sonnet-4-6` to score each
one on a fixed 5-dimension 1-to-5 rubric, and persists the result as
JSON to `~/.solo-founder-os/evals/`. This dataset is the union of
those JSON files, flattened to one row per (run, example).

## Why it exists

Andrej Karpathy's "jagged intelligence + verification loops" thesis
is now mainstream — agents are usable in production but only if you
ship the eval loop alongside them. The artifact most people don't
have is **what an honest, single-operator eval trace looks like over
months**: drift, regressions, judge variance, sparse weeks, every
warts-and-all bit.

This dump is offered as one data point against which others can
calibrate their own agent-eval systems. It is also the public
backbone of three downstream things we built:

- [polymarket-brier-skill](https://github.com/alex-jb/polymarket-brier-skill) — calibration scoring
- [council-diff](https://github.com/alex-jb/council-diff) — multi-voice judge
- [claude-md-directory](https://github.com/alex-jb/claude-md-directory) — prompt-library audit

## How the underlying evals were produced

For each skill that had at least one recorded example:

1. Sample the last N=5 (configurable) rows from
   `~/.solo-founder-os/examples/<skill>.jsonl`.
2. Send `(inputs, output, rubric)` to `claude-sonnet-4-6` with a
   structured-output JSON schema requiring integer scores 1-5 on
   five dimensions:
   - **clarity** — would a reader understand without context?
   - **specificity** — concrete details vs generic platitudes
   - **voice** — does it sound like a real person, not a bot?
   - **accuracy** — does it reflect the inputs without inventing?
   - **completeness** — does it cover what's asked without padding?
3. `overall = round(mean(5 dims), 1)` per example.
4. Aggregate `mean_overall`, `p50_overall`, `p10_overall` over the
   sampled rows for that run.

Source: [`solo_founder_os/eval.py`](https://github.com/alex-jb/solo-founder-os/blob/main/solo_founder_os/eval.py).

Cost per Sunday run: ~$0.04 across the whole stack. Cheap on purpose
— the eval has to keep running for years.

## Schema

The dataset is a single newline-delimited JSON file
(`public-eval.jsonl`). One row = one judged example.

| column | type | notes |
|---|---|---|
| `skill` | str | slug (`draft-x`, `review-diff-pass`, ...) |
| `ts` | str | ISO 8601 UTC, timestamp of the eval run |
| `eval_run_id` | str | source JSON filename stem, unique per run |
| `example_index` | int | position within the run's sampled examples |
| `clarity` | int | 1-5 |
| `specificity` | int | 1-5 |
| `voice` | int | 1-5 |
| `accuracy` | int | 1-5 |
| `completeness` | int | 1-5 |
| `overall` | float | 1-decimal mean of the 5 dims |
| `notes` | str | 1-2 judge sentences (PII-sanitized) |
| `run_mean` | float | run-level mean_overall (same for every row in run) |
| `run_p50` | float | run-level p50_overall |
| `run_p10` | float | run-level p10_overall |
| `run_n_examples` | int | how many examples landed in this run |
| `judge_model` | str | `claude-sonnet-4-6` for this dump |
| `agent_name` | str | downstream agent that owns this skill |
| `schema_version` | str | `v1` |
| `pii_flag` | bool | true if heuristics matched and `notes` was redacted |

## PII / safety handling

The only field that contains free-form text is `notes` (the judge's
1-2 sentence verbal summary). Everything else is integer / float /
enum / timestamp.

Before publishing we run a regex/heuristic scan over `notes` for:

- email addresses
- known API-key prefixes (`sk-ant-`, `sk_live_`, `sk_test_`, `sk-proj-`)
- 32+ character base64-shaped tokens
- explicit personal-name strings

If any pattern matches, the row's `notes` is replaced with
`[redacted-PII]` and `pii_flag = true` — the *scores* are preserved
because the scores are the data of interest; only the verbal
summary is redacted. No real secrets are present in the source JSONs
either — the matches we see (5 of 34 rows at first publication) are
all judges discussing example security-review outputs that contain
the literal *strings* `sk-ant-` etc. as topic vocabulary.

## Known limitations — read these before using

1. **Single operator.** This is one person (Alex Ji)'s stack. Skill
   distribution reflects his work, not a representative sample of
   solo-founder workflows. `review-diff-pass` dominates the dataset
   because it runs on every git push.

2. **Single judge model.** The full dataset is scored by one
   `claude-sonnet-4-6` snapshot. No cross-judge agreement numbers.
   A council-of-judges variant is in development at
   [council-diff](https://github.com/alex-jb/council-diff) but not
   reflected here.

3. **Volume is small.** First publication: ~34 rows across 7 skills,
   spanning roughly 6 weeks of real cron fires (2026-05-01 →
   2026-06-14). The launchd cron silently de-loaded for stretches
   in May, so this is **not** a clean weekly cadence. See
   `~/Desktop/Interview-Prep/Projects/alex-brain/` post-vacation
   audit (2026-05-27) for the cron forensics.

4. **Recent examples bias.** The eval samples the *latest* N rows
   each Sunday, so high-volume agents are over-represented in
   recent runs and low-volume agents may only have 1 example per
   run.

5. **Judge-written notes are interpretive.** The `notes` text
   reflects Sonnet's narrative about the strongest/weakest
   dimension — useful as qualitative signal, not as labeled data.

6. **The rubric is opinionated.** "Voice" in particular encodes a
   solo-founder bias toward direct, founder-toned writing. Other
   organizations would weight differently.

## How to cite

```bibtex
@dataset{ji2026sfosevals,
  author       = {Ji, Alex},
  title        = {{solo-founder-os-evals: six weeks of single-operator
                   agent eval traces}},
  year         = {2026},
  publisher    = {Hugging Face},
  url          = {https://huggingface.co/datasets/alexji/solo-founder-os-evals}
}
```

## Reproducing or extending

The export script lives in this repo at
[`scripts/export_public_evals.py`](scripts/export_public_evals.py).
To re-export from a fresh `~/.solo-founder-os/evals/` directory:

```sh
python3 scripts/export_public_evals.py
# writes ~/.solo-founder-os/public-eval.jsonl
```

If you have your own SFOS install and want to contribute a parallel
dataset under a different namespace, drop your `evals/` JSONs into
the same directory shape and the script will work as-is. The
`SKILL_TO_AGENT` mapping at the top of the script is the only thing
that's Alex-specific.
