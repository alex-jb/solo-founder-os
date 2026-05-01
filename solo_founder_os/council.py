"""Council — multi-agent meeting / debate over a topic.

When you have a decision that benefits from multiple perspectives — "should
we ship Hero Card skins or featured slots first?" — invite the relevant
agents into a virtual meeting:

    council = [
        CouncilMember(".vc-outreach-agent",
                       role="fundraising perspective",
                       system_prompt="You weigh investor signaling..."),
        CouncilMember(".funnel-analytics-agent",
                       role="user traction perspective",
                       system_prompt="You weigh real signups + plays..."),
        CouncilMember(".cost-audit-agent",
                       role="cost discipline perspective",
                       system_prompt="You weigh API + Stripe + dev time..."),
    ]
    out = hold_meeting(
        topic="Q3 paid SKU choice",
        question="Hero Card skins ($3) or Featured slots ($20)?",
        members=council,
    )
    print(out.synthesis)

Each member gets ONE Claude call with:
  1. Past reflections from their own agent (auto-prepended via L1)
  2. Their role-specific system prompt
  3. The topic + question

After all contributions land, a synthesizer (1 more call) merges them
into a final decision/recommendation. Total: N+1 Haiku calls per meeting.

Storage: meetings are markdown in ~/.solo-founder-os/council-meetings/
<YYYY-MM-DD-slug>.md. Predefined councils (LAUNCH_READINESS,
PRICING_DECISION, BUG_TRIAGE) make common meetings 1-command.

Why not Claude Code Agent Teams: that's for parallel CODING work
(coordinated subagents claiming tasks from a shared list, Opus 4.6+).
This is for MEETINGS — bounded, deliberative, multi-perspective. Different
shape, different tool. Use Agent Teams for "let's parallelize building";
use Council for "let's decide which thing to build."
"""
from __future__ import annotations
import pathlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .anthropic_client import AnthropicClient, DEFAULT_HAIKU_MODEL
from .reflection import reflections_preamble


def _extract_text(resp) -> str:
    """Local copy of AnthropicClient.extract_text. Inlined so tests that
    monkey-patch the AnthropicClient *constructor* with a lambda don't
    lose access to the static method."""
    if resp is None:
        return ""
    try:
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception:
        return ""


COUNCIL_DIR = (pathlib.Path.home() / ".solo-founder-os"
               / "council-meetings")
COUNCIL_USAGE_LOG = (pathlib.Path.home() / ".solo-founder-os"
                      / "usage.jsonl")


@dataclass
class CouncilMember:
    agent_name: str  # e.g. ".vc-outreach-agent"
    role: str         # human-readable label, e.g. "fundraising perspective"
    system_prompt: str
    # Optional: pull recent reflections from this agent for the named task type
    reflections_task: Optional[str] = "council"


@dataclass
class Contribution:
    member: CouncilMember
    body: str
    raw: dict = field(default_factory=dict)


@dataclass
class CouncilOutput:
    topic: str
    question: str
    members: list[CouncilMember]
    contributions: list[Contribution]
    synthesis: str
    generated_at: str = ""


# ── Predefined councils for common decisions ────────────────────


LAUNCH_READINESS_COUNCIL: list[CouncilMember] = [
    CouncilMember(
        agent_name=".funnel-analytics-agent",
        role="ops + observability",
        system_prompt=(
            "You are the ops perspective. You care about: are alerts wired? "
            "is ntfy receiving? are baselines in place? are the 8 sources "
            "all configured? You DON'T care about content tone or fundraising. "
            "Be concrete: name the missing wires, not vibes."
        ),
    ),
    CouncilMember(
        agent_name=".orallexa-marketing-agent",
        role="distribution + narrative",
        system_prompt=(
            "You are the marketing perspective. You care about: is the launch "
            "narrative sharp? are platforms scheduled? is timing right for "
            "the algorithm? You DON'T care about technical readiness. "
            "Be concrete: name the distribution gaps, not vibes."
        ),
    ),
    CouncilMember(
        agent_name=".build-quality-agent",
        role="code quality + risk",
        system_prompt=(
            "You are the code-quality perspective. You care about: any "
            "uncommitted changes? any failing tests? any unrotated keys? "
            "any unapplied migrations? Be specific: name the risk by file, "
            "not vibes."
        ),
    ),
]

PRICING_DECISION_COUNCIL: list[CouncilMember] = [
    CouncilMember(
        agent_name=".cost-audit-agent",
        role="margin + cost",
        system_prompt=(
            "You are the cost discipline perspective. You weigh API + Stripe "
            "fees + infrastructure cost vs. proposed price. You name the "
            "minimum price that clears unit economics, and the contribution "
            "margin at the proposed price."
        ),
    ),
    CouncilMember(
        agent_name=".funnel-analytics-agent",
        role="willingness-to-pay signal",
        system_prompt=(
            "You are the user-data perspective. From observed traction "
            "(plays, upvotes, stage distribution) you estimate which segment "
            "is most likely to pay and at what tier. Be specific: cite "
            "stage counts where useful."
        ),
    ),
    CouncilMember(
        agent_name=".vc-outreach-agent",
        role="investor signaling",
        system_prompt=(
            "You are the investor-signaling perspective. You weigh how a "
            "given price/SKU choice reads to a seed VC: 'this founder has "
            "pricing discipline' vs 'this founder priced too cheap'. Avoid "
            "platitudes; cite specific signaling effects."
        ),
    ),
]

BUG_TRIAGE_COUNCIL: list[CouncilMember] = [
    CouncilMember(
        agent_name=".build-quality-agent",
        role="code-level diagnosis",
        system_prompt=(
            "You are the code-level perspective. From the bug report, infer "
            "likely files / functions implicated. You DON'T speculate about "
            "user impact or business risk. Be specific: name files and "
            "functions where possible."
        ),
    ),
    CouncilMember(
        agent_name=".funnel-analytics-agent",
        role="user-impact + scope",
        system_prompt=(
            "You are the user-impact perspective. From the bug report, "
            "estimate how many users hit it (signup flow? play flow? edge "
            "case?). Cite metrics if you have them."
        ),
    ),
    CouncilMember(
        agent_name=".customer-discovery-agent",
        role="precedent + similar reports",
        system_prompt=(
            "You are the precedent perspective. Have similar bugs been "
            "reported in pain-point clusters? Is this a recurring shape "
            "(auth flow / 5xx / forge-stuck)?"
        ),
    ),
]


# ── Core ─────────────────────────────────────────────────────────


def _ask_member(
    member: CouncilMember,
    *,
    topic: str,
    question: str,
    client: AnthropicClient,
    model: str,
) -> Contribution:
    """One Claude call asking this member for their input."""
    prefix = ""
    if member.reflections_task:
        try:
            prefix = reflections_preamble(member.agent_name,
                                            member.reflections_task)
        except Exception:
            prefix = ""
    system = (prefix + member.system_prompt) if prefix else member.system_prompt

    user = (
        f"Topic: {topic}\n\n"
        f"Question: {question}\n\n"
        f"From your perspective ({member.role}), give your input.\n"
        "Constraints:\n"
        "  - 200 words max\n"
        "  - Be concrete; cite specifics from your domain\n"
        "  - End with: 'Recommendation: <one-line stance>'"
    )
    resp, err = client.messages_create(
        model=model,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if err is not None or resp is None:
        return Contribution(
            member=member,
            body=f"(unavailable: {err or 'no response'})",
            raw={"error": err},
        )
    text = _extract_text(resp)
    return Contribution(member=member, body=text or "(empty)",
                          raw={"resp": str(resp)[:500]})


SYNTHESIS_PROMPT = """You are the council moderator. You just heard from N members,
each giving their domain perspective on the same topic. Your job:

  1. Identify the points where members AGREED (high-confidence signals).
  2. Identify the points where members DISAGREED (decision pivots).
  3. Surface any blind spots — what nobody mentioned that probably matters.
  4. Output a final recommendation: a 2-3 sentence synthesis that respects
     the disagreements (don't paper over them) and gives the human a clear
     "do X" or "decide between A and B based on Y" framing.

Output structure:
## Agreements
- ...
## Disagreements
- ...
## Blind spots
- ...
## Recommendation
<2-3 sentences>
"""


def _synthesize(
    topic: str,
    question: str,
    contributions: list[Contribution],
    *,
    client: AnthropicClient,
    model: str,
) -> str:
    """One Claude call to merge contributions into a decision."""
    body_parts = [f"Topic: {topic}", f"Question: {question}", ""]
    for c in contributions:
        body_parts.append(f"## From {c.member.role} ({c.member.agent_name})")
        body_parts.append(c.body)
        body_parts.append("")
    user = "\n".join(body_parts)
    resp, err = client.messages_create(
        model=model,
        max_tokens=600,
        system=SYNTHESIS_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    if err is not None or resp is None:
        return f"(synthesis unavailable: {err})"
    return _extract_text(resp) or "(empty synthesis)"


def hold_meeting(
    topic: str,
    question: str,
    *,
    members: list[CouncilMember],
    client: Optional[AnthropicClient] = None,
    model: str = DEFAULT_HAIKU_MODEL,
) -> CouncilOutput:
    """Hold a multi-agent meeting; return CouncilOutput with contributions
    + final synthesis. Never raises — degrades to per-member error messages."""
    if client is None:
        client = AnthropicClient(usage_log_path=COUNCIL_USAGE_LOG)

    if not client.configured:
        return CouncilOutput(
            topic=topic, question=question, members=members,
            contributions=[Contribution(member=m,
                                          body="(no ANTHROPIC_API_KEY)")
                            for m in members],
            synthesis="(no ANTHROPIC_API_KEY — meeting cannot proceed)",
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    contributions = [
        _ask_member(m, topic=topic, question=question,
                     client=client, model=model)
        for m in members
    ]
    synthesis = _synthesize(topic, question, contributions,
                              client=client, model=model)
    return CouncilOutput(
        topic=topic, question=question, members=members,
        contributions=contributions, synthesis=synthesis,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Persistence ─────────────────────────────────────────────────


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "meeting"


def render_meeting_md(output: CouncilOutput) -> str:
    parts = [
        "---",
        f"topic: {output.topic}",
        f"question: {output.question}",
        f"generated_at: {output.generated_at or 'now'}",
        f"members: [{', '.join(m.agent_name for m in output.members)}]",
        "---",
        "",
        f"# Council meeting: {output.topic}",
        "",
        f"**Question:** {output.question}",
        "",
        "## Contributions",
        "",
    ]
    for c in output.contributions:
        parts.append(f"### {c.member.role} (`{c.member.agent_name}`)")
        parts.append("")
        parts.append(c.body)
        parts.append("")
    parts += ["## Synthesis", "", output.synthesis, ""]
    return "\n".join(parts)


def write_meeting(
    output: CouncilOutput,
    *,
    base: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    base = base or COUNCIL_DIR
    base.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = base / f"{today}-{_slug(output.topic)}.md"
    if path.exists():
        for i in range(2, 100):
            cand = base / f"{today}-{_slug(output.topic)}-{i}.md"
            if not cand.exists():
                path = cand
                break
    path.write_text(render_meeting_md(output), encoding="utf-8")
    return path


# ── CLI ──────────────────────────────────────────────────────────


COUNCIL_REGISTRY = {
    "launch-readiness": LAUNCH_READINESS_COUNCIL,
    "pricing": PRICING_DECISION_COUNCIL,
    "bug-triage": BUG_TRIAGE_COUNCIL,
}


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import os
    import sys

    p = argparse.ArgumentParser(
        prog="sfos-council",
        description="Hold a multi-agent meeting; write markdown notes.",
    )
    p.add_argument("topic", help="Meeting topic (short).")
    p.add_argument("question",
                    help="Specific question for the council to answer.")
    p.add_argument("--council", choices=sorted(COUNCIL_REGISTRY.keys()),
                    default="launch-readiness",
                    help="Predefined council (default: launch-readiness).")
    p.add_argument("--dry-run", action="store_true",
                    help="Print to stdout, don't write file.")
    args = p.parse_args(argv)

    if os.getenv("COUNCIL_SKIP") == "1":
        return 0

    members = COUNCIL_REGISTRY[args.council]
    out = hold_meeting(args.topic, args.question, members=members)
    md = render_meeting_md(out)
    if args.dry_run:
        print(md)
        return 0
    path = write_meeting(out)
    print(f"✓ meeting notes written to {path}", file=sys.stderr)
    print(md)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
