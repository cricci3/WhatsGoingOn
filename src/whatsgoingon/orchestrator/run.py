from __future__ import annotations

import argparse
import logging
from datetime import date

import anthropic

from whatsgoingon.agent.state import compute_deltas
from whatsgoingon.config import (
    ANALYST_MODEL,
    ANTHROPIC_API_KEY,
    ANTHROPIC_WORKSPACE_ID,
    EDITOR_MODEL,
    SKEPTIC_MODEL,
)
from whatsgoingon.logging_config import configure_logging
from whatsgoingon.orchestrator.orchestrator import DEFAULT_MAX_ROUNDS, run_debate_cycle
from whatsgoingon.orchestrator.state import DebateState, format_critiques, format_draft

logger = logging.getLogger(__name__)


def run_debate(*, month: str, max_rounds: int = DEFAULT_MAX_ROUNDS) -> DebateState:
    """Compute the shared delta snapshot once and run one Analyst/Skeptic/Editor cycle over it.

    Unlike the Phase 1 run_cycle(), nothing is written to the NarrativeStore yet, so running
    this never overwrites the single-agent narrative for the same month."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set; add it to .env")

    deltas = compute_deltas()
    if not deltas:
        raise RuntimeError("No data found - run `whatsgoingon --source all` first to ingest data")
    logger.info("computed deltas for %d series", len(deltas))

    default_headers = {"anthropic-workspace-id": ANTHROPIC_WORKSPACE_ID} if ANTHROPIC_WORKSPACE_ID else None
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, default_headers=default_headers)
    return run_debate_cycle(
        client,
        month=month,
        deltas=deltas,
        analyst_model=ANALYST_MODEL,
        skeptic_model=SKEPTIC_MODEL,
        editor_model=EDITOR_MODEL,
        max_rounds=max_rounds,
    )


def format_transcript(state: DebateState) -> str:
    """Human-readable transcript of the whole debate: every draft, critique and editor note,
    then the published narrative and changelog."""
    parts = [f"# Debate for {state.month} ({len(state.rounds)} of at most {state.max_rounds} rounds)"]
    for number, debate_round in enumerate(state.rounds, start=1):
        parts += [
            f"\n## Round {number} - Analyst draft\n",
            format_draft(debate_round.draft),
            f"\n## Round {number} - Skeptic critiques\n",
            format_critiques(debate_round.critiques),
        ]
        if debate_round.editor_decision is not None:
            parts += [f"\n## Round {number} - Editor sent it back\n", debate_round.editor_decision.reason]
    if state.decision is not None:
        parts += [
            "\n## Editor - published\n",
            state.decision.reason,
            "\n## Final narrative\n",
            state.decision.final_narrative or "",
            "\n## Changelog\n",
            state.decision.changelog or "",
        ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one Analyst -> Skeptic -> Editor debate cycle over the locally ingested data"
    )
    parser.add_argument(
        "--month",
        default=date.today().strftime("%Y-%m"),
        help="Month the narrative is for, default: current month (YYYY-MM)",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=DEFAULT_MAX_ROUNDS,
        help=f"Max Analyst/Skeptic rounds before the Editor must publish (default {DEFAULT_MAX_ROUNDS})",
    )
    args = parser.parse_args(argv)

    # JSON logs on stderr (per-agent model calls, tool calls, tokens, decisions); the
    # readable transcript on stdout, so the two can be redirected separately.
    configure_logging()

    state = run_debate(month=args.month, max_rounds=args.max_rounds)
    print(format_transcript(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
