"""Health check for the Solo Founder OS agent stack.

Run-once, no-side-effects audit answering "is everything wired right
for tomorrow morning?" — designed specifically for Alex's PH-day eve.

Checks per agent:
  • Console-script reachable on PATH (`<agent>-name` resolves)
  • ~/.<agent>-dir/ exists (usage log, baseline, queue all live there)
  • Required env vars set (per-agent register)
  • macOS launchd plist installed (if applicable)

Cross-stack checks:
  • At least one notifier configured (NTFY_TOPIC / TELEGRAM_BOT_TOKEN /
    SLACK_WEBHOOK_URL) — silent agents on PH-day = blind agents
  • ANTHROPIC_API_KEY set (the agents that need it)

Output is plain text with green/red bullets. Exit code 0 if every
required check passes, 1 if any required check fails (so cron can use
it). Soft warnings don't bump exit code.

Usage:
    sfos-doctor
    sfos-doctor --json    # machine-readable
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


# Per-agent contracts. `home_dir` is relative to ~. `required_env` are
# vars the agent absolutely needs to do anything; `optional_env` is
# graceful-degradation fields. `console_script` is what setuptools
# entry-points will install on PATH.
AGENT_CHECKS: list[dict] = [
    {
        "name": "build-quality-agent",
        "home_dir": ".build-quality-agent",
        "console_script": "build-quality-agent",
        "required_env": ["ANTHROPIC_API_KEY"],
        "optional_env": [],
        "launchd_label": None,  # pre-push hook, no cron
    },
    {
        "name": "customer-discovery-agent",
        "home_dir": ".customer-discovery-agent",
        "console_script": "customer-discovery-agent",
        "required_env": [],  # works heuristically without Claude
        "optional_env": ["ANTHROPIC_API_KEY", "REDDIT_CLIENT_ID"],
        "launchd_label": None,
    },
    {
        "name": "funnel-analytics-agent",
        "home_dir": ".funnel-analytics-agent",
        "console_script": "funnel-analytics-agent",
        "required_env": [],
        "optional_env": ["ANTHROPIC_API_KEY", "VERCEL_TOKEN", "PH_DEV_TOKEN",
                          "PH_LAUNCH_SLUG", "SUPABASE_PERSONAL_ACCESS_TOKEN",
                          "SUPABASE_PROJECT_REF", "VIBEX_PROJECT_REF"],
        "launchd_label": "com.alex.funnel-analytics.brief",
    },
    {
        "name": "vc-outreach-agent",
        "home_dir": ".vc-outreach-agent",
        "console_script": "vc-outreach-agent",
        "required_env": [],
        "optional_env": ["ANTHROPIC_API_KEY", "SMTP_HOST", "SMTP_USER"],
        "launchd_label": None,
    },
    {
        "name": "cost-audit-agent",
        "home_dir": ".cost-audit-agent",
        "console_script": "cost-audit-agent",
        "required_env": [],
        "optional_env": ["VERCEL_TOKEN", "ANTHROPIC_ADMIN_KEY",
                          "ANTHROPIC_ORG_ID", "GITHUB_TOKEN"],
        "launchd_label": None,
    },
    {
        "name": "bilingual-content-sync-agent",
        "home_dir": ".bilingual-content-sync-agent",
        "console_script": "bilingual-sync",
        "required_env": [],
        "optional_env": ["ANTHROPIC_API_KEY"],
        "launchd_label": None,
    },
    {
        "name": "marketing-agent",
        "home_dir": ".orallexa-marketing-agent",
        "console_script": "marketing-agent",
        "required_env": [],
        "optional_env": ["ANTHROPIC_API_KEY", "X_API_KEY",
                          "LINKEDIN_ACCESS_TOKEN"],
        "launchd_label": None,
    },
]


@dataclass
class CheckResult:
    label: str
    ok: bool
    detail: str = ""
    severity: str = "required"  # "required" or "warn"


@dataclass
class AgentReport:
    name: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed_required(self) -> bool:
        return all(c.ok or c.severity == "warn" for c in self.checks)


def _check_console_script(name: str) -> CheckResult:
    found = shutil.which(name)
    if found:
        return CheckResult(f"console script `{name}`", True, found)
    return CheckResult(
        f"console script `{name}`", False,
        f"not on PATH — run `pip install -e {name}/` from the repo")


def _check_home_dir(home_dir: str) -> CheckResult:
    p = pathlib.Path.home() / home_dir
    if p.exists():
        # Note size of usage log if present
        log = p / "usage.jsonl"
        size = log.stat().st_size if log.exists() else 0
        return CheckResult(
            f"~/{home_dir}/", True,
            f"exists (usage log {size:,} bytes)" if size else "exists")
    return CheckResult(
        f"~/{home_dir}/", True,  # OK — created on first run
        "not yet created (will be on first agent run)",
        severity="warn")


def _check_env(env_var: str, *, required: bool) -> CheckResult:
    val = os.getenv(env_var)
    if val:
        masked = (val[:6] + "…" + val[-4:]) if len(val) > 12 else "***"
        return CheckResult(f"${env_var}", True, masked)
    return CheckResult(
        f"${env_var}", not required,
        "unset" + ("" if required else " (optional)"),
        severity="required" if required else "warn")


def _check_launchd(label: str) -> CheckResult:
    if sys.platform != "darwin":
        return CheckResult(
            f"launchd `{label}`", True,
            "not macOS — launchd N/A", severity="warn")
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True,
            timeout=5)
    except Exception as e:
        return CheckResult(f"launchd `{label}`", False, str(e))
    if label in (out.stdout or ""):
        return CheckResult(f"launchd `{label}`", True, "loaded")
    return CheckResult(
        f"launchd `{label}`", True,
        "not loaded — run `launchctl load ~/Library/LaunchAgents/<label>.plist`",
        severity="warn")


def check_agent(spec: dict) -> AgentReport:
    name = spec["name"]
    rpt = AgentReport(name=name)
    rpt.checks.append(_check_console_script(spec["console_script"]))
    rpt.checks.append(_check_home_dir(spec["home_dir"]))
    for env in spec.get("required_env", []):
        rpt.checks.append(_check_env(env, required=True))
    for env in spec.get("optional_env", []):
        rpt.checks.append(_check_env(env, required=False))
    if spec.get("launchd_label"):
        rpt.checks.append(_check_launchd(spec["launchd_label"]))
    return rpt


def check_stack_wide() -> list[CheckResult]:
    """Cross-cutting checks not bound to one agent."""
    out: list[CheckResult] = []

    # Sterile-env import check — catches the v0.26.2 production cron
    # bug where solo-founder-os "worked in dev" because shell CWD was
    # in the repo, but launchd's neutral CWD couldn't import it.
    out.append(_check_sterile_import())

    notifiers = {
        "ntfy": os.getenv("NTFY_TOPIC"),
        "telegram": (os.getenv("TELEGRAM_BOT_TOKEN")
                     and os.getenv("TELEGRAM_CHAT_ID")),
        "slack": os.getenv("SLACK_WEBHOOK_URL"),
    }
    configured = [name for name, val in notifiers.items() if val]
    if configured:
        out.append(CheckResult(
            "notifier configured (ntfy/telegram/slack)",
            True, f"{', '.join(configured)} ready"))
    else:
        out.append(CheckResult(
            "notifier configured (ntfy/telegram/slack)",
            False,
            "no notifier configured — agents will write briefs but never page you. "
            "Set NTFY_TOPIC for the simplest path.",
            severity="warn"))
    return out


def _check_sterile_import() -> CheckResult:
    """Run `python -c 'import solo_founder_os'` from a sterile CWD
    (one that has no `solo_founder_os/` subdir). If it fails, the
    package isn't pip-installed and any launchd / cron / sudo /
    cross-machine invocation will silently break with ModuleNotFoundError.

    The fix is always: `pip install --user -e <path-to-repo>` (editable
    if you want dev-tree changes to apply) or `pip install solo-founder-os`
    once PyPI publish is configured.
    """
    import subprocess
    name = "solo_founder_os importable from neutral CWD"
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             "import solo_founder_os; print(solo_founder_os.__version__)"],
            cwd="/",  # /  has no solo_founder_os/ subdir to shadow site-packages
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        return CheckResult(name, False, f"sub-import failed: {e}",
                              severity="alert")
    if r.returncode == 0:
        ver = r.stdout.strip()
        return CheckResult(name, True, f"v{ver} resolves cleanly")
    err = (r.stderr or r.stdout or "").strip().splitlines()[-1:]
    return CheckResult(
        name, False,
        f"package not pip-installed — launchd cron WILL fail. "
        f"Fix: `pip install --user -e ~/Desktop/solo-founder-os`. "
        f"Last error: {err[0] if err else '(empty)'}",
        severity="alert",
    )


def render_text(reports: list[AgentReport],
                stack_wide: list[CheckResult]) -> tuple[str, bool]:
    """Render the report. Returns (text, all_required_passed)."""
    lines: list[str] = ["# Solo Founder OS — Doctor", ""]
    all_ok = True

    lines.append("## Stack-wide")
    for c in stack_wide:
        if c.ok:
            lines.append(f"  ✅ {c.label} — {c.detail}")
        elif c.severity == "warn":
            lines.append(f"  ⚠️  {c.label} — {c.detail}")
        else:
            lines.append(f"  ❌ {c.label} — {c.detail}")
            all_ok = False
    lines.append("")

    for rpt in reports:
        agent_passed = rpt.passed_required
        marker = "✅" if agent_passed else "❌"
        lines.append(f"## {marker} {rpt.name}")
        for c in rpt.checks:
            if c.ok:
                lines.append(f"  ✅ {c.label} — {c.detail}")
            elif c.severity == "warn":
                lines.append(f"  ⚠️  {c.label} — {c.detail}")
            else:
                lines.append(f"  ❌ {c.label} — {c.detail}")
                all_ok = False
        lines.append("")

    lines.append("---")
    lines.append("Status: " + ("**ALL GREEN** — stack ready." if all_ok
                                else "**FIXES NEEDED** before shipping."))
    return "\n".join(lines), all_ok


def render_json(reports: list[AgentReport],
                stack_wide: list[CheckResult]) -> tuple[str, bool]:
    all_ok = True
    out = {"stack_wide": [], "agents": []}
    for c in stack_wide:
        out["stack_wide"].append(
            {"label": c.label, "ok": c.ok, "detail": c.detail,
             "severity": c.severity})
        if not c.ok and c.severity != "warn":
            all_ok = False
    for rpt in reports:
        agent_blob: dict = {"name": rpt.name, "checks": []}
        for c in rpt.checks:
            agent_blob["checks"].append(
                {"label": c.label, "ok": c.ok, "detail": c.detail,
                 "severity": c.severity})
            if not c.ok and c.severity != "warn":
                all_ok = False
        out["agents"].append(agent_blob)
    out["all_required_passed"] = all_ok
    return json.dumps(out, indent=2), all_ok


def _summary_for_push(reports: list[AgentReport],
                       stack_wide: list[CheckResult],
                       all_ok: bool) -> tuple[str, str, str]:
    """Build a (title, body, priority) tuple suited for ntfy/Telegram/Slack
    push. Push body should be short — phones truncate. We surface only
    failed agents + the stack-wide notifier line."""
    if all_ok:
        return (
            "🟢 sfos-doctor: all green",
            ("All 8 agents wired correctly. "
             "Notifier configured. Ready to ship."),
            "default",
        )
    failed = [r.name for r in reports if not r.passed_required]
    stack_failures = [c.label for c in stack_wide
                       if not c.ok and c.severity != "warn"]
    lines = ["sfos-doctor failed:"]
    if stack_failures:
        lines.append("Stack: " + ", ".join(stack_failures))
    if failed:
        lines.append("Agents: " + ", ".join(failed))
    return ("🔴 sfos-doctor: fixes needed",
            "\n".join(lines), "high")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="sfos-doctor",
        description="Audit the Solo Founder OS agent stack.")
    p.add_argument("--json", action="store_true",
                    help="Emit JSON instead of human-readable text.")
    p.add_argument("--notify", default=None,
                    help="Comma-separated notifiers (ntfy,telegram,slack). "
                         "Sends a one-line summary to your phone after the "
                         "audit runs. Empty/unset → no push.")
    args = p.parse_args(argv)

    reports = [check_agent(spec) for spec in AGENT_CHECKS]
    stack_wide = check_stack_wide()
    text, all_ok = (render_json if args.json else render_text)(
        reports, stack_wide)
    print(text)

    # Optional push (after the local print so cron logs still capture text)
    if args.notify:
        try:
            from .notifier import fan_out
        except ImportError:
            pass
        else:
            targets = [n.strip() for n in args.notify.split(",") if n.strip()]
            if targets:
                title, body, priority = _summary_for_push(
                    reports, stack_wide, all_ok)
                fan_out(targets, body, title=title, priority=priority)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
