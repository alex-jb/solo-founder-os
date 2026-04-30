# solo-founder-os

**English** | [中文](README.zh-CN.md)

> Shared base library for the Solo Founder OS agent stack. The thing every alex-jb agent depends on. Source/MetricSample/SourceReport ABCs, 7-day baseline with rotation, HTTP retry, Anthropic client with auto cost log, notifiers (ntfy / Telegram / Slack), brief composer, testing helpers.

[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/solo-founder-os.svg)](https://pypi.org/project/solo-founder-os/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](#)

## Why this exists

After [Alex Ji](https://github.com/alex-jb) shipped 7 agents in 2 days for the [VibeXForge](https://github.com/alex-jb/vibex) launch, the duplication was 60-70% across them: every agent reimplemented retry logic, env loading, JSONL append, severity ladders, mock-urlopen patterns. This library is the place where one fix benefits all current AND future agents.

## What ships in v0.1

```
solo_founder_os/
├── source.py              # Source ABC, SourceReport, MetricSample
├── http.py                # urlopen_json, with_retry decorator
├── baseline.py            # 7-day median + auto-rotation to .gz
├── usage_log.py           # JSONL append + cost computation
├── anthropic_client.py    # Wrapped client w/ graceful degrade + auto-log
├── notifier.py            # Ntfy / Telegram / Slack + fan_out()
├── brief.py               # Markdown composer (severity sections + summary)
└── testing.py             # pytest helpers (fake_urlopen, fake_anthropic)
```

## Usage

```python
from solo_founder_os import (
    Source, SourceReport, MetricSample,
    urlopen_json, with_retry,
    AnthropicClient,
    enrich_with_baseline, record_samples,
)

class MyVercelSource(Source):
    name = "vercel"

    @property
    def configured(self) -> bool:
        return bool(os.getenv("VERCEL_TOKEN"))

    @with_retry(times=3)
    def _api(self, path):
        return urlopen_json(f"https://api.vercel.com{path}",
                            headers={"Authorization": f"Bearer {os.getenv('VERCEL_TOKEN')}"})

    def fetch(self) -> SourceReport:
        report = SourceReport(source=self.name, fetched_at=datetime.now(timezone.utc))
        if not self.configured:
            report.error = "missing VERCEL_TOKEN"
            return report
        try:
            data = self._api("/v6/deployments")
        except Exception as e:
            report.error = f"API error: {e}"
            return report
        # ... build metrics ...
        return report
```

## Agents using this

| Agent | Status |
|---|---|
| funnel-analytics-agent | migrated v0.6.0 |
| cost-audit-agent | migrating v0.2.0 |
| vc-outreach-agent | migrating v0.3.0 |
| build-quality-agent | migrating v0.4.0 |
| customer-discovery-agent | migrating v0.2.0 |
| marketing-agent | migrating(parallel session) |
| (future agents) | start here |

## Roadmap

- [x] **v0.1** — Source/MetricSample · baseline w/ rotation · HTTP retry · Anthropic client · notifiers · brief · testing helpers · 57 tests
- [ ] **v0.2** — HITL markdown queue (lift from vc-outreach-agent) · CLI skeleton (common --skip / --no-baseline / --notify / --out flags)
- [ ] **v0.3** — Provider ABC for cost-audit (mirror of Source for billing snapshots) · cron expression generator · launchd plist generator
- [ ] **v0.4** — Claude summarizer (lift from funnel-analytics v0.5)
- [ ] **v0.5** — Cross-agent integration helpers (one agent's report consumed by another's source)

## License

MIT.
