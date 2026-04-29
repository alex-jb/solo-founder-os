"""solo-founder-os — shared base for the Solo Founder OS agent stack.

The library every alex-jb agent depends on. Provides:
- Source / Provider ABCs with `fetch() → Report` contract
- MetricSample + ProviderReport + SourceReport dataclasses
- 7-day rolling baseline with auto-rotation
- HTTP helpers (urlopen_json + with_retry decorator) — pure stdlib, no requests
- AnthropicClient — auto cost log, graceful degrade on missing key
- Notifier ABC + ntfy / Telegram / Slack adapters + fan_out()
- HITL markdown queue (pending/approved/rejected/sent)
- Brief composer — markdown render with severity sections + Claude summary
- pytest helpers (fake_urlopen, fake_anthropic, tmp_baseline, tmp_queue)
- CLI skeleton (--skip / --no-baseline / --notify / --out / --dry-run)

Why this exists: after building 7 agents in 2 days the duplication was
60-70% across them. Every agent reimplemented retry, env loader, JSONL
append, severity ladder, mock urlopen, etc. This library is the place
where one fix benefits all current AND future agents.

Versioning policy: SemVer, but breaking changes only on major bumps.
Internal modules (those starting with `_`) can break in minor versions.
"""
__version__ = "0.1.0"

from .source import (
    Source,
    SourceReport,
    MetricSample,
    SEVERITY_ORDER,
)
from .http import urlopen_json, with_retry, HTTPError
from .baseline import (
    enrich_with_baseline,
    record_samples,
    BASELINE_WINDOW_DAYS,
)
from .usage_log import (
    log_usage,
    usage_report,
    PRICES,
)
from .anthropic_client import (
    AnthropicClient,
    DEFAULT_HAIKU_MODEL,
    DEFAULT_SONNET_MODEL,
)

__all__ = [
    "Source", "SourceReport", "MetricSample", "SEVERITY_ORDER",
    "urlopen_json", "with_retry", "HTTPError",
    "enrich_with_baseline", "record_samples", "BASELINE_WINDOW_DAYS",
    "log_usage", "usage_report", "PRICES",
    "AnthropicClient", "DEFAULT_HAIKU_MODEL", "DEFAULT_SONNET_MODEL",
]
