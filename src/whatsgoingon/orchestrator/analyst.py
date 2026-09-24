from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

import anthropic

from whatsgoingon.agent.tools import build_tool_implementations, build_tools
from whatsgoingon.logging_config import agent_logger
from whatsgoingon.orchestrator.budget import AgentBudget
from whatsgoingon.orchestrator.state import (
    Claim,
    DebateState,
    Draft,
    format_draft,
    format_review,
)
from whatsgoingon.orchestrator.structured import call_structured

SYSTEM_PROMPT = """You are the Analyst in a three-agent macro newsroom (Analyst, Skeptic, Editor). \
You write a monthly "what's going on" narrative about macro and market data.

You are given the change in a set of tracked indicators over roughly the last 30 days. You may use \
your research tools when a top-level move looks worth explaining (e.g. more history for a series, \
or news for a ticker) - don't call them by default.

Write a concise, plain-English narrative (200-400 words), then list every numeric or causal \
assertion it makes as a separate claim:
- claim_type "factual": reads a number straight off the data;
- "correlation": two series moved together, without asserting why;
- "causal": one thing drove another. Only use it when you can say why, not just that both moved.
- supporting_series: the names of the tracked series (exactly as listed) the claim rests on; empty \
for purely qualitative claims. Series you looked up with tools but that aren't in the list can't be \
cited here - mention them in the text instead.
- confidence: your honest confidence, low/medium/high.

A Skeptic will check each claim against its supporting data and push back on unsupported claims, \
hasty causality and inflated confidence. When revising after critiques, address every critique \
explicitly - fix, soften, or drop the claim - and keep the same id for claims you carry over so \
the debate can be followed. Give new claims new ids.

Submit with the submit_draft tool."""


def _submit_draft_tool(series_names: list[str]) -> dict:
    series_item: dict[str, Any] = {"type": "string"}
    if series_names:
        series_item["enum"] = series_names
    return {
        "name": "submit_draft",
        "description": "Submit the narrative draft and the claims it makes. Call once, when done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "narrative": {"type": "string", "description": "The narrative, 200-400 words."},
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Stable id, e.g. 'c1'."},
                            "text": {"type": "string"},
                            "claim_type": {"type": "string", "enum": ["factual", "causal", "correlation"]},
                            "supporting_series": {"type": "array", "items": series_item},
                            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                        },
                        "required": ["id", "text", "claim_type", "supporting_series", "confidence"],
                    },
                },
            },
            "required": ["narrative", "claims"],
        },
    }


def build_user_message(state: DebateState) -> str:
    parts = [
        f"Today's date is {date.today().isoformat()}. You are writing the narrative for {state.month}.",
        "Change in tracked indicators over roughly the last 30 days:",
        state.delta_summary,
    ]
    if state.rounds:
        last = state.rounds[-1]
        parts += [
            f"\nThis is round {state.round_number} of at most {state.max_rounds}. Your previous draft:",
            format_draft(last.draft),
            "\nThe Skeptic's critiques of it:",
            format_review(last),
        ]
        if last.editor_decision is not None:
            parts += ["\nThe Editor sent it back for another round:", last.editor_decision.reason]
        parts.append("\nRevise the draft to address these.")
    else:
        parts.append(f"\nWrite the first draft for {state.month}.")
    return "\n".join(parts)


def produce_draft(
    client: anthropic.Anthropic,
    *,
    model: str,
    state: DebateState,
    tools: Sequence[dict] | None = None,
    tool_impls: Mapping[str, Callable[..., Any]] | None = None,
    budget: AgentBudget | None = None,
) -> Draft:
    """Write a new draft (round 1, off state.deltas) or revise the latest one in light of
    state.latest_critiques (later rounds). Tags each numeric/causal assertion as a Claim,
    attaching the actual SeriesDelta(s) from state.deltas it's based on (Claim.supporting_deltas)
    rather than just naming a series - that's what lets the Skeptic check the Analyst's
    reasoning without re-deriving or re-trusting the arithmetic.

    state.deltas is the same list agent/state.py's compute_deltas() produces for the Phase 1
    agent, computed once upstream and shared across the whole cycle - not recomputed here.

    The model only names series; the SeriesDelta objects are looked up here, so a claim can
    never cite a number that isn't in state.deltas (unknown names are dropped and logged).
    `tools`/`tool_impls` default to the Phase 1 agent's research tools. Raises BudgetExceeded
    if `budget` runs out before the draft is submitted.
    """
    log = agent_logger(__name__, agent="analyst", month=state.month, round=state.round_number)
    if tools is None:
        tools = build_tools()
    if tool_impls is None:
        tool_impls = build_tool_implementations()

    deltas_by_name = {d.name: d for d in state.deltas}
    output = call_structured(
        client,
        model=model,
        system=SYSTEM_PROMPT,
        user_message=build_user_message(state),
        output_tool=_submit_draft_tool(list(deltas_by_name)),
        log=log,
        tools=tools,
        tool_impls=tool_impls,
        budget=budget,
    )

    claims = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(output.get("claims", []), start=1):
        claim_id = str(raw.get("id") or "").strip()
        if not claim_id or claim_id in seen_ids:
            # Missing or duplicate ids would make critiques ambiguous - assign a fresh one.
            claim_id = f"c{index}"
            while claim_id in seen_ids:
                claim_id += "'"
        seen_ids.add(claim_id)

        supporting = []
        for name in raw.get("supporting_series", []):
            if name in deltas_by_name:
                supporting.append(deltas_by_name[name])
            else:
                log.warning(
                    "claim cites an untracked series; dropped", extra={"claim_id": claim_id, "series": name}
                )

        claims.append(
            Claim(
                id=claim_id,
                text=raw.get("text", ""),
                claim_type=raw.get("claim_type", "factual"),
                supporting_deltas=supporting,
                confidence=raw.get("confidence"),
            )
        )
    return Draft(narrative=output.get("narrative", ""), claims=claims)
