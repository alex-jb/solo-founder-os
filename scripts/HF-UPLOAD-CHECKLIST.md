# HuggingFace upload checklist — solo-founder-os-evals

One-time steps to publish the JSONL produced by
`scripts/export_public_evals.py` to
[huggingface.co/datasets/alexji/solo-founder-os-evals](https://huggingface.co/datasets/alexji/solo-founder-os-evals).

## 0. Prereqs (one-time)

```sh
# install CLI if not already
pip install -U "huggingface_hub[cli]"

# log in with the HF token (Settings → Access Tokens → "write" scope)
huggingface-cli login
```

## 1. Re-run the export to make sure JSONL is fresh

```sh
cd ~/Desktop/solo-founder-os
python3 scripts/export_public_evals.py
# expect: wrote N rows across M skills, K flagged for PII
```

Output path: `~/.solo-founder-os/public-eval.jsonl`

## 2. Create the dataset repo (first time only)

```sh
huggingface-cli repo create solo-founder-os-evals --type dataset
# answer: y to confirm
```

## 3. Upload the JSONL + methodology

```sh
huggingface-cli upload \
  alexji/solo-founder-os-evals \
  ~/.solo-founder-os/public-eval.jsonl \
  public-eval.jsonl \
  --repo-type dataset \
  --commit-message "Initial publication — 6 weeks of single-operator eval traces"

huggingface-cli upload \
  alexji/solo-founder-os-evals \
  ~/Desktop/solo-founder-os/HUGGINGFACE-DATASET-METHODOLOGY.md \
  README.md \
  --repo-type dataset \
  --commit-message "Add methodology as README"
```

`README.md` on HF datasets renders on the dataset landing page — that
is why we upload the methodology MD as `README.md` on the HF side.

## 4. (Optional) Add HF dataset metadata YAML

After the first upload, edit `README.md` on the HF web UI and add this
YAML front-matter so the dataset shows up in search filters:

```yaml
---
license: cc-by-4.0
task_categories:
  - text-classification
language:
  - en
  - zh
size_categories:
  - n<1K
tags:
  - agents
  - eval
  - llm-judge
  - solo-founder
  - claude-sonnet
pretty_name: Solo Founder OS — single-operator agent eval traces
---
```

## 5. Verify

```sh
# anyone can now do:
pip install datasets
python3 -c "
from datasets import load_dataset
d = load_dataset('alexji/solo-founder-os-evals', data_files='public-eval.jsonl')
print(d)
print(d['train'][0])
"
```

## 6. Announce — draft tweet @karpathy

> Six weeks of LLM-judge eval traces from a 1-operator 11-agent stack,
> public on HuggingFace under CC-BY:
> https://huggingface.co/datasets/alexji/solo-founder-os-evals
>
> Not a benchmark. A trace. Single judge, single operator, every
> warts-and-all drift week the launchd cron silently de-loaded.
> Offered as one data point for jagged-intelligence + verification-loop
> calibration. /cc @karpathy

Alternative one-liner if Twitter feels too noisy:

> Public dump: 34 rows × 7 skills × 6 weeks of `claude-sonnet-4-6`
> judging my own agent stack. CC-BY-4.0.
> huggingface.co/datasets/alexji/solo-founder-os-evals

## 7. Add to repo README

After upload, add a line to `~/Desktop/solo-founder-os/README.md`
under a new section (e.g. "Public artifacts"):

```md
- Public eval traces: [huggingface.co/datasets/alexji/solo-founder-os-evals](https://huggingface.co/datasets/alexji/solo-founder-os-evals) — CC-BY-4.0
```
