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
__version__ = "0.17.0"

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
from .hitl_queue import (
    HitlQueue,
    parse_frontmatter,
    render_frontmatter,
    sanitize_filename_part,
    make_basename,
    PENDING, APPROVED, REJECTED, SENT,
)
from .cli import add_common_args, check_skip, resolve_notify_targets
from .scheduler import (
    build_launchd_plist,
    build_cron_line,
    launch_agent_path,
)
from .batch import (
    batch_request,
    batch_submit,
    batch_status,
    batch_results,
    batch_wait,
)
from .reflection import (
    log_outcome,
    recent_reflections,
    reflections_preamble,
)
from .skills import (
    Skill,
    MissingInputError,
    render_prompt,
    save_skill,
    load_skill,
    list_skills,
    distill_skill,
    record_example,
    load_examples,
)
from .preference import (
    log_edit,
    recent_edits,
    preference_preamble,
)
from .council import (
    CouncilMember,
    Contribution,
    CouncilOutput,
    hold_meeting,
    write_meeting,
    LAUNCH_READINESS_COUNCIL,
    PRICING_DECISION_COUNCIL,
    BUG_TRIAGE_COUNCIL,
)
from .bandit import (
    Bandit,
    squash,
)
from .autopsy import (
    autopsy,
    render_markdown as render_autopsy_markdown,
    MetricSource,
    CriticHook,
    BestTimeHook,
)
from .eval import (
    evaluate_skill,
    write_report as write_eval_report,
    load_recent_reports,
    detect_drift,
    list_skills_with_examples,
    SkillEvalReport,
    ExampleScore,
)
from .cross_agent_report import (
    collect as collect_cross_agent_report,
    render_markdown as render_cross_agent_markdown,
    KNOWN_AGENT_DIRS,
)

__all__ = [
    "Source", "SourceReport", "MetricSample", "SEVERITY_ORDER",
    "urlopen_json", "with_retry", "HTTPError",
    "enrich_with_baseline", "record_samples", "BASELINE_WINDOW_DAYS",
    "log_usage", "usage_report", "PRICES",
    "AnthropicClient", "DEFAULT_HAIKU_MODEL", "DEFAULT_SONNET_MODEL",
    "HitlQueue", "parse_frontmatter", "render_frontmatter",
    "sanitize_filename_part", "make_basename",
    "PENDING", "APPROVED", "REJECTED", "SENT",
    "add_common_args", "check_skip", "resolve_notify_targets",
    "build_launchd_plist", "build_cron_line", "launch_agent_path",
    "batch_request", "batch_submit", "batch_status", "batch_results", "batch_wait",
    "log_outcome", "recent_reflections", "reflections_preamble",
    "Skill", "MissingInputError", "render_prompt",
    "save_skill", "load_skill", "list_skills",
    "distill_skill", "record_example", "load_examples",
    "log_edit", "recent_edits", "preference_preamble",
    "CouncilMember", "Contribution", "CouncilOutput",
    "hold_meeting", "write_meeting",
    "LAUNCH_READINESS_COUNCIL", "PRICING_DECISION_COUNCIL", "BUG_TRIAGE_COUNCIL",
    "Bandit", "squash",
    "autopsy", "render_autopsy_markdown",
    "MetricSource", "CriticHook", "BestTimeHook",
    "evaluate_skill", "write_eval_report", "load_recent_reports",
    "detect_drift", "list_skills_with_examples",
    "SkillEvalReport", "ExampleScore",
    "collect_cross_agent_report", "render_cross_agent_markdown",
    "KNOWN_AGENT_DIRS",
]
