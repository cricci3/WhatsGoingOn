from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

import anthropic

from whatsgoingon.agent.tools import build_tool_implementations, build_tools
from whatsgoingon.logging_config import agent_logger
from whatsgoingon.orchestrator.budget import AgentBudget
from whatsgoingon.orchestrator.state import ContextBrief, ContextEvent, DebateState
from whatsgoingon.orchestrator.structured import call_structured

# Read-only research: news, plus local history to line an event up with a move. Not
# fetch_fred_series - it writes to SQLite, and the Context agent runs while the Analyst may be
# using the same database.
CONTEXT_TOOL_NAMES = ("get_ticker_news", "search_news", "query_observations")

SYSTEM_PROMPT = """You are the Context researcher in a macro newsroom (Analyst, Skeptic, Editor, \
and you). While the Analyst drafts this month's narrative from the data, you independently find \
what actually happened in the world around the biggest moves in the tracked indicators.

Use your tools to look up news for the largest or most surprising moves. Report events, not \
interpretations: what happened, when, which tracked series it plausibly relates to, and where \
you read it. Say so plainly when you found nothing relevant for a move - an empty result is \
useful to the Skeptic too. Don't write a narrative and don't speculate beyond the sources.

Your briefing goes to the Skeptic, who uses it to check the Analyst's causal claims against real \
events rather than just two series moving together.

Submit with the submit_context tool."""


def _submit_context_tool(series_names: list[str]) -> dict:
    series_item: dict[str, Any] = {"type": "string"}
    if series_names:
        series_item["enum"] = series_names
    return {
        "name": "submit_context",
        "description": "Submit the news context you found (an empty event list if nothing relevant).",
        "input_schema": {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string", "description": "What happened, and when."},
                            "related_series": {"type": "array", "items": series_item},
                            "source": {"type": "string", "description": "Publisher/headline it came from."},
                        },
                        "required": ["summary", "related_series"],
                    },
                },
                "notes": {
                    "type": "string",
                    "description": "Moves you looked into but found no news for, or other caveats.",
                },
            },
            "required": ["events"],
        },
    }


def build_user_message(state: DebateState) -> str:
    return "\n".join(
        [
            f"Today's date is {date.today().isoformat()}. The narrative is for {state.month}.",
            "Change in tracked indicators over roughly the last 30 days:",
            state.delta_summary,
            "\nFind the news context for the moves that most need explaining.",
        ]
    )


def gather_context(
    client: anthropic.Anthropic,
    *,
    model: str,
    state: DebateState,
    tools: Sequence[dict] | None = None,
    tool_impls: Mapping[str, Callable[..., Any]] | None = None,
    budget: AgentBudget | None = None,
) -> ContextBrief:
    """Research real-world events behind the month's moves, independently of the Analyst's
    draft - which is what lets the orchestrator run the two at the same time.

    `tools`/`tool_impls` default to the read-only news/history subset of the Phase 1 tools
    (CONTEXT_TOOL_NAMES; search_news only when NEWSAPI_KEY is set). related_series is
    restricted to tracked names in the schema, and anything else is dropped. Raises
    BudgetExceeded/AgentTimeout like the other agents; the orchestrator treats this agent as
    optional and carries on without it.
    """
    log = agent_logger(__name__, agent="context", month=state.month)
    if tools is None:
        tools = [t for t in build_tools() if t["name"] in CONTEXT_TOOL_NAMES]
    if tool_impls is None:
        tool_impls = {n: f for n, f in build_tool_implementations().items() if n in CONTEXT_TOOL_NAMES}

    tracked = {d.name for d in state.deltas}
    output = call_structured(
        client,
        model=model,
        system=SYSTEM_PROMPT,
        user_message=build_user_message(state),
        output_tool=_submit_context_tool(sorted(tracked)),
        log=log,
        tools=tools,
        tool_impls=tool_impls,
        budget=budget,
    )
    events = [
        ContextEvent(
            summary=raw.get("summary", ""),
            related_series=[name for name in raw.get("related_series", []) if name in tracked],
            source=raw.get("source") or None,
        )
        for raw in output.get("events", [])
    ]
    return ContextBrief(events=events, notes=output.get("notes") or None)
