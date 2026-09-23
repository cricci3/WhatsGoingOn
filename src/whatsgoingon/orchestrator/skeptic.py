from __future__ import annotations

import anthropic

from whatsgoingon.agent.state import format_deltas
from whatsgoingon.logging_config import agent_logger
from whatsgoingon.orchestrator.state import Critique, Draft
from whatsgoingon.orchestrator.structured import call_structured

SYSTEM_PROMPT = """You are the Skeptic in a three-agent macro newsroom (Analyst, Skeptic, Editor). \
The Analyst has written a narrative and tagged each of its assertions as a claim, with the actual \
data each claim rests on attached. The numbers themselves are correct - check what was concluded \
from them. For each claim, look for:
- unsupported claims: the claim says more than its supporting data shows, or states a number with \
no supporting data at all;
- hasty causality: a "causal" claim where the data only shows two things moving together, or \
where an obvious alternative explanation is ignored;
- inflated confidence: "high" confidence on something the data only weakly supports.

Severity:
- high: misleading or wrong enough that it shouldn't be published as-is;
- medium: should be qualified or softened, but the point stands;
- low: minor nitpick.

Don't critique style or length. Raise at most one critique per issue, targeted at one claim id. \
If the draft holds up, submit an empty list - pushing back for its own sake isn't useful.

Submit with the submit_critiques tool."""


def _submit_critiques_tool(claim_ids: list[str]) -> dict:
    claim_id_schema: dict = {"type": "string"}
    if claim_ids:
        claim_id_schema["enum"] = claim_ids
    return {
        "name": "submit_critiques",
        "description": "Submit your critiques of the draft (an empty list if it holds up).",
        "input_schema": {
            "type": "object",
            "properties": {
                "critiques": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim_id": claim_id_schema,
                            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                            "comment": {"type": "string", "description": "What's wrong and why."},
                        },
                        "required": ["claim_id", "severity", "comment"],
                    },
                },
            },
            "required": ["critiques"],
        },
    }


def build_user_message(draft: Draft) -> str:
    parts = ["Narrative:", draft.narrative, "", "Claims, each with the data it rests on:"]
    for claim in draft.claims:
        parts.append(
            f"\n[{claim.id}] ({claim.claim_type}, confidence: {claim.confidence or 'unstated'}) {claim.text}"
        )
        if claim.supporting_deltas:
            parts.append(format_deltas(claim.supporting_deltas))
        else:
            parts.append("(no supporting data)")
    return "\n".join(parts)


def critique_draft(
    client: anthropic.Anthropic,
    *,
    model: str,
    draft: Draft,
) -> list[Critique]:
    """Review `draft` for unsupported claims, hasty causality, and inflated confidence,
    returning one Critique per issue found, each tagged to the claim_id it targets. An
    empty list means the Skeptic found nothing worth pushing back on.

    Each Claim already carries the real SeriesDelta(s) it's based on (claim.supporting_deltas),
    computed once upstream and shared with the Analyst - so this checks what was *concluded*
    from those numbers (causality, confidence, claim_type vs. what the deltas actually support),
    not the arithmetic itself, and never re-queries or re-derives a delta on its own.

    No research tools: the Skeptic judges the draft against the data it cites, nothing else.
    Critiques targeting a claim id that isn't in the draft are dropped and logged.
    """
    log = agent_logger(__name__, agent="skeptic")
    claim_ids = [claim.id for claim in draft.claims]
    output = call_structured(
        client,
        model=model,
        system=SYSTEM_PROMPT,
        user_message=build_user_message(draft),
        output_tool=_submit_critiques_tool(claim_ids),
        log=log,
    )

    critiques = []
    for raw in output.get("critiques", []):
        if raw.get("claim_id") not in claim_ids:
            log.warning("critique targets an unknown claim; dropped", extra={"critique": raw})
            continue
        critiques.append(Critique(claim_id=raw["claim_id"], severity=raw["severity"], comment=raw["comment"]))
    return critiques
