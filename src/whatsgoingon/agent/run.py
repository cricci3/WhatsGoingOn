from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

import anthropic

from whatsgoingon.agent.loop import run_agent
from whatsgoingon.agent.narrative_store import NarrativeStore
from whatsgoingon.agent.obsidian_export import export_narrative_to_vault
from whatsgoingon.agent.state import SeriesDelta, compute_deltas, format_deltas
from whatsgoingon.agent.tools import build_tool_implementations, build_tools
from whatsgoingon.config import (
    AGENT_MODEL,
    ANTHROPIC_API_KEY,
    ANTHROPIC_WORKSPACE_ID,
    OBSIDIAN_VAULT_PATH,
)
from whatsgoingon.console import use_utf8_output

logger = logging.getLogger(__name__)

FUTURE_MONTH_WARNING_TEMPLATE = (
    "{month} is a future month - there is no real data for it yet. This narrative reflects "
    "the most recently ingested data, not anything that has actually happened in {month}."
)

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


def build_initial_message(month: str, delta_summary: str, similar_narratives: list[dict]) -> str:
    parts = [
        f"Today's date is {date.today().isoformat()}. You are writing the narrative for {month}.",
        "Here is the change in tracked indicators over roughly the last 30 days:",
        delta_summary,
    ]
    if similar_narratives:
        parts.append("\nFor continuity, here are past narratives discussing similar conditions:")
        for item in similar_narratives:
            parts.append(f"\n--- {item['month']} ---\n{item['narrative']}")
    parts.append(
        f"\nWrite this month's narrative for {month}. If you give it a title, base it on {month} - "
        "don't guess or invent a different month or date."
    )
    return "\n".join(parts)


def _previous_month(store: NarrativeStore, month: str) -> str | None:
    earlier_months = [m for m in store.list_months() if m < month]
    return max(earlier_months) if earlier_months else None


def _is_future_month(month: str) -> bool:
    """True if `month` (YYYY-MM) is after the current calendar month. YYYY-MM strings sort
    lexicographically, the same trick NarrativeStore relies on for month ordering."""
    return month > date.today().strftime("%Y-%m")


def _with_future_month_warning(record: dict, month: str) -> dict:
    return {**record, "warning": FUTURE_MONTH_WARNING_TEMPLATE.format(month=month)}


def run_cycle(*, month: str, store: NarrativeStore | None = None, retrieve: bool = False) -> dict:
    store = store or NarrativeStore()
    future_month = _is_future_month(month)
    if future_month:
        logger.warning("narrative requested for future month %s; no real data exists for it yet", month)

    if retrieve:
        cached = store.get_narrative(month)
        if cached is None:
            raise RuntimeError(
                f"--retrieve was set but no cached narrative exists for {month}; "
                "run once without --retrieve first"
            )
        logger.info("retrieved cached narrative for %s (skipped agent call)", month)
        return _with_future_month_warning(cached, month) if future_month else cached

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set; add it to .env")

    deltas: list[SeriesDelta] = compute_deltas()
    if not deltas:
        raise RuntimeError("No data found - run `whatsgoingon --source all` first to ingest data")
    delta_summary = format_deltas(deltas)
    logger.info("computed deltas for %d series", len(deltas))

    similar = store.get_similar_narratives(delta_summary, n_results=3, exclude_month=month)
    logger.info("retrieved %d similar past narratives for context", len(similar))

    default_headers = {"anthropic-workspace-id": ANTHROPIC_WORKSPACE_ID} if ANTHROPIC_WORKSPACE_ID else None
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, default_headers=default_headers)
    narrative = run_agent(
        client,
        model=AGENT_MODEL,
        system=SYSTEM_PROMPT,
        initial_message=build_initial_message(month, delta_summary, similar),
        tools=build_tools(),
        tool_impls=build_tool_implementations(),
    )

    store.add_narrative(month, narrative, delta_summary)
    result = store.get_narrative(month)

    if OBSIDIAN_VAULT_PATH is not None:
        try:
            export_narrative_to_vault(
                result,
                OBSIDIAN_VAULT_PATH,
                previous_month=_previous_month(store, month),
            )
        except Exception:
            logger.warning("failed to export narrative to Obsidian vault", exc_info=True)

    return _with_future_month_warning(result, month) if future_month else result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one cycle of the monthly macro narrative agent")
    parser.add_argument(
        "--month",
        default=date.today().strftime("%Y-%m"),
        help="Month key to store this narrative under, default: current month (YYYY-MM)",
    )
    parser.add_argument(
        "--retrieve",
        action="store_true",
        help="Skip the agent call and reuse the narrative already stored for --month, if any "
        "(saves tokens while developing/testing)",
    )
    args = parser.parse_args(argv)
    use_utf8_output()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result = run_cycle(month=args.month, retrieve=args.retrieve)
    if result.get("warning"):
        print(f"WARNING: {result['warning']}", file=sys.stderr)
    print(result["narrative"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
