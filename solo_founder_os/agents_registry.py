"""Single source of truth for the SFOS agent stack.

Before this module existed, five files (governance.py, supervisor.py,
evolver.py, ui.py, cross_agent_report.py) each hardcoded their own
agent list. Three agents (customer-support, customer-outreach,
payments) were added to ui.py and cross_agent_report.py but the
governance + supervisor lists drifted behind — those three were
invisible to `sfos-inbox` and `sfos-supervisor`.

Anything that needs to enumerate the agent stack now imports
`KNOWN_AGENT_DIRS` from here. If you ship a new agent, add it once,
in one place, and every part of the stack picks it up.

Naming convention: dotfile-prefixed directory under $HOME (so
~/.<agent>/queue/pending lives next to ~/.<agent>/reflections.jsonl).
The dotfile prefix keeps the home dir clean and matches what each
agent's own __main__ writes to.
"""
from __future__ import annotations


# The canonical 11-agent stack. Order is roughly "earliest shipped first"
# so morning-brief / cross_agent_report tables render in a stable order.
KNOWN_AGENT_DIRS: list[str] = [
    ".build-quality-agent",
    ".customer-discovery-agent",
    ".funnel-analytics-agent",
    ".vc-outreach-agent",
    ".cost-audit-agent",
    ".bilingual-content-sync-agent",
    ".orallexa-marketing-agent",
    ".customer-support-agent",
    ".customer-outreach-agent",   # console_script alias preserved post v0.9.0 merge
    ".payments-agent",
    ".vibex-publish-agent",
]


def is_known_agent(agent_dir: str) -> bool:
    """Cheap membership check used by inbox/supervisor filtering."""
    return agent_dir in KNOWN_AGENT_DIRS
