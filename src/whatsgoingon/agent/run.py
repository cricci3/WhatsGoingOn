from __future__ import annotations

import argparse
import logging
from datetime import date

import anthropic

from whatsgoingon.agent.loop import run_agent
from whatsgoingon.agent.narrative_store import NarrativeStore
from whatsgoingon.agent.state import SeriesDelta, compute_deltas, format_deltas
from whatsgoingon.agent.tools import build_tool_implementations, build_tools
from whatsgoingon.config import AGENT_MODEL, ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a macro/markets analyst producing a monthly "what's going on" narrative.

You are given the change in a set of tracked indicators (macro data from FRED, market data from \
Yahoo Finance) over roughly the last 30 days. Decide whether any move is significant enough to dig \
deeper using your tools (e.g. a big move in CPI might warrant checking its energy sub-component via \
fetch_fred_series, or query_observations for more history on a series). Only call tools when the \
top-level numbers actually suggest something worth explaining - don't call them by default.

Then write a concise, plain-English narrative (200-400 words) of what's going on and why it matters. \
State your confidence honestly and note when you are speculating vs. reading directly from the data. \
If past narratives are provided for continuity, stay consistent with them unless the new data clearly \
contradicts what was said before - in that case, say so explicitly rather than quietly ignoring it."""


def build_initial_message(delta_summary: str, similar_narratives: list[dict]) -> str:
    parts = [
        "Here is the change in tracked indicators over roughly the last 30 days:",
        delta_summary,
    ]
    if similar_narratives:
        parts.append("\nFor continuity, here are past narratives discussing similar conditions:")
        for item in similar_narratives:
            parts.append(f"\n--- {item['month']} ---\n{item['narrative']}")
    parts.append("\nWrite this month's narrative.")
    return "\n".join(parts)


def run_cycle(*, month: str, store: NarrativeStore | None = None) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set; add it to .env")

    deltas: list[SeriesDelta] = compute_deltas()
    if not deltas:
        raise RuntimeError("No data found - run `whatsgoingon --source all` first to ingest data")
    delta_summary = format_deltas(deltas)
    logger.info("computed deltas for %d series", len(deltas))

    store = store or NarrativeStore()
    similar = store.get_similar_narratives(delta_summary, n_results=3, exclude_month=month)
    logger.info("retrieved %d similar past narratives for context", len(similar))

    default_headers = {"anthropic-workspace-id": ANTHROPIC_WORKSPACE_ID} if ANTHROPIC_WORKSPACE_ID else None
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, default_headers=default_headers)
    narrative = run_agent(
        client,
        model=AGENT_MODEL,
        system=SYSTEM_PROMPT,
        initial_message=build_initial_message(delta_summary, similar),
        tools=build_tools(),
        tool_impls=build_tool_implementations(),
    )

    store.add_narrative(month, narrative, delta_summary)
    return narrative


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one cycle of the monthly macro narrative agent")
    parser.add_argument(
        "--month",
        default=date.today().strftime("%Y-%m"),
        help="Month key to store this narrative under, default: current month (YYYY-MM)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    narrative = run_cycle(month=args.month)
    print(narrative)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
