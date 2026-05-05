# Contributing

Thanks for considering a contribution. **This is the shared library**
that the [11-agent Solo Founder OS stack](README.md#the-stack-11-agents-7-canonical-layers)
depends on. A change here can affect every downstream agent — please
read this doc before opening a PR.

## Quick start

```bash
git clone https://github.com/alex-jb/solo-founder-os.git
cd solo-founder-os
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,anthropic,ui]"

pytest -q          # 512+ tests; should run < 2s
ruff check .       # ruff is the linter and formatter
```

If anything in those commands fails on a fresh clone, that's a bug.
Open an issue.

## What's a good first PR

- **Bug fixes** with a failing test — these merge fastest.
- **Per-agent home-dir registration** — when a new agent ships, it must
  be added to both `cross_agent_report.KNOWN_AGENT_DIRS` and
  `evolver.DEFAULT_AGENT_REPOS` or it's invisible to retro and evolver.
- **Better error messages** — anything that says "unhandled: <stacktrace>"
  can probably say something more useful, especially in the cron
  wrapper / pre-flight paths where the operator is debugging blind.
- **Doc clarity** — the README and CHANGELOG are the contract for
  downstream agents; ambiguity costs everyone.
- **New L1-L6 sources** — the L4 evolver currently consumes reflexion
  patterns + L6 drift. Adding e.g. cost-spike or HITL-rejection-rate
  as additional pattern sources is a clean expansion.

## What's a hard sell

- **Heavy new dependencies.** SFOS is intentionally light: stdlib +
  `anthropic` + optional `streamlit` for the UI. New runtime deps need
  a strong justification.
- **Auto-merging anything.** The L4 evolver writes markdown PR proposals;
  it never merges. Anything that bypasses HITL (auto-apply, auto-send,
  auto-publish) needs explicit operator opt-in via env flag AND the
  flag MUST default off.
- **Loosening the L4 safety gate.** `is_safe_path()` and the blocklist
  are deliberately strict. Any change that lets the evolver propose
  edits to `auth/`, `secret`, `credential`, `smtp`, `stripe`,
  `billing`, `anthropic_client`, `migrations`, or `.env` paths needs
  to go through a security review.
- **Cosmetic refactors without tests.** `tests/` is the contract; PRs
  without test changes are usually rejected.

## Test discipline

- The `tests/` dir mirrors the package layout (`tests/test_eval.py`
  tests `solo_founder_os/eval.py`, etc).
- Pytest takes < 2 seconds for the full suite. Don't write tests that
  hit the network or sleep.
- **Sterile-CWD discipline.** Tests that monkeypatch `pathlib.Path.home`
  must do so at the function level. Several modules (skills.py,
  preference.py, reflection.py) had module-level constants that
  captured `Path.home()` at import time, which made tests "pass" by
  writing to the real `~/.<agent>/` dirs. Look for this pattern.
- **`SFOS_TEST_MODE=1`** umbrella guards `log_outcome`, `record_example`,
  `log_edit` from writing to disk during tests. The downstream agents
  set this in their `conftest.py`. SFOS's own tests don't need to set
  it because they all use `monkeypatch.setattr(pathlib.Path, "home",
  lambda: tmp_path)` directly.

## Commit style

Conventional-Commits-ish:
- `feat: <short>` — new capability
- `fix: <short>` — bug fix
- `docs: <short>` — README / docstring / CHANGELOG
- `refactor: <short>` — internal change, no behavior diff
- `chore: <short>` — version bumps, config, lint
- `ci: <short>` — workflow changes

Body leads with **why**, then **what changed**, then **testing notes**
or breaking-change callouts. The 2026-05 sprint commits are the
reference style — they're long because the changes touch the whole
loop.

## Versioning policy

SemVer. **Breaking changes only on major bumps.** Internal modules
(those starting with `_`) can break in minor versions.

The agent stack pins `solo-founder-os>=X` in their `pyproject.toml`.
A breaking change here cascades: every downstream agent's CI fails
until they bump their dep. Don't break wantonly.

## CHANGELOG

Update `CHANGELOG.md` for every minor and patch release. The format
is one-or-two-sentences-per-version with the exact commit messages
as the source of truth. Don't write a release announcement; write a
diff summary.

## CI

PRs trigger:
- `test.yml` — pytest across Python 3.9-3.12
- `lint.yml` — `ruff check .`

Both must be green before merge. The release workflow (`release.yml`)
fires on `v*` tags and publishes to PyPI via Trusted Publishing.

## License

By contributing, you agree your changes are licensed under MIT (the
same license as this repo). No CLA required for small changes; for
substantial contributions, the maintainer may ask for explicit
confirmation in the PR thread.
