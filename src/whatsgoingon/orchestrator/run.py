from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date

import anthropic

from whatsgoingon.agent.state import compute_deltas
from whatsgoingon.config import (
    AGENT_BUDGET_TOKENS,
    AGENT_TIMEOUT_S,
    ANALYST_MODEL,
    ANTHROPIC_API_KEY,
    ANTHROPIC_WORKSPACE_ID,
    CONTEXT_MODEL,
    CYCLE_BUDGET_USD,
    EDITOR_MODEL,
    SKEPTIC_MODEL,
)
from whatsgoingon.logging_config import configure_logging
from whatsgoingon.orchestrator.budget import CycleBudget
from whatsgoingon.orchestrator.orchestrator import AGENT_FAILURES, DEFAULT_MAX_ROUNDS, run_debate_cycle
from whatsgoingon.orchestrator.state import (
    DebateState,
    format_context,
    format_draft,
    format_fallbacks,
    format_review,
)

logger = logging.getLogger(__name__)


def build_budget(*, max_cost_usd: float | None = CYCLE_BUDGET_USD) -> CycleBudget:
    """The cycle budget from config (WGO_CYCLE_BUDGET_USD, WGO_<ROLE>_BUDGET_TOKENS,
    WGO_<ROLE>_TIMEOUT_S), with an optional override of the cycle's cost cap (None: no cap)."""
    return CycleBudget(
        max_cost_usd=max_cost_usd,
        agent_max_tokens={agent: limit for agent, limit in AGENT_BUDGET_TOKENS.items() if limit is not None},
        agent_timeout_s={agent: limit for agent, limit in AGENT_TIMEOUT_S.items() if limit is not None},
    )


def run_debate(
    *,
    month: str,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    budget: CycleBudget | None = None,
    with_context: bool = True,
) -> DebateState:
    """Compute the shared delta snapshot once and run one Analyst/Skeptic/Editor cycle over it
    (plus the Context agent in parallel with the first draft, unless with_context is False).

    Unlike the Phase 1 run_cycle(), nothing is written to the NarrativeStore yet, so running
    this never overwrites the single-agent narrative for the same month. `budget` defaults to
    build_budget(), i.e. the limits configured in the environment. The orchestrator is async;
    this is the synchronous entry point that owns the event loop (asyncio.run)."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set; add it to .env")

    deltas = compute_deltas()
    if not deltas:
        raise RuntimeError("No data found - run `whatsgoingon --source all` first to ingest data")
    logger.info("computed deltas for %d series", len(deltas))

    default_headers = {"anthropic-workspace-id": ANTHROPIC_WORKSPACE_ID} if ANTHROPIC_WORKSPACE_ID else None
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, default_headers=default_headers)
    return asyncio.run(
        run_debate_cycle(
            client,
            month=month,
            deltas=deltas,
            analyst_model=ANALYST_MODEL,
            skeptic_model=SKEPTIC_MODEL,
            editor_model=EDITOR_MODEL,
            context_model=CONTEXT_MODEL if with_context else None,
            max_rounds=max_rounds,
            budget=budget or build_budget(),
        )
    )


def format_transcript(state: DebateState) -> str:
    """Human-readable transcript of the whole debate: every draft, critique and editor note,
    then the published narrative and changelog."""
    parts = [f"# Debate for {state.month} ({len(state.rounds)} of at most {state.max_rounds} rounds)"]
    if state.context is not None:
        parts += ["\n## Context - news gathered alongside the first draft\n", format_context(state.context)]
    for number, debate_round in enumerate(state.rounds, start=1):
        parts += [
            f"\n## Round {number} - Analyst draft\n",
            format_draft(debate_round.draft),
            f"\n## Round {number} - Skeptic critiques\n",
            format_review(debate_round),
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
    if state.fallbacks:
        parts += ["\n## Fallbacks (the cycle ran degraded)\n", format_fallbacks(state.fallbacks)]
    parts += ["\n## Spend\n", format_spend(state.budget)]
    return "\n".join(parts)


def format_spend(budget: CycleBudget) -> str:
    lines = []
    for agent, spent in budget.spend.items():
        limit = budget.agent_max_tokens.get(agent)
        cap = f" of {limit:,}" if limit is not None else ""
        lines.append(
            f"- {agent}: {spent.calls} calls, {spent.total_tokens:,}{cap} tokens, ~${spent.cost_usd:.4f}"
        )
    cap = f" of ${budget.max_cost_usd:.2f}" if budget.max_cost_usd is not None else ""
    lines.append(f"- cycle: {budget.total_tokens:,} tokens, ~${budget.cost_usd:.4f}{cap}")
    return "\n".join(lines)


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
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="Cost cap for this cycle in USD, overriding WGO_CYCLE_BUDGET_USD (0 disables it)",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Skip the Context agent (news research run in parallel with the first draft)",
    )
    args = parser.parse_args(argv)

    # JSON logs on stderr (per-agent model calls, tool calls, tokens, decisions); the
    # readable transcript on stdout, so the two can be redirected separately.
    configure_logging()

    budget = build_budget() if args.budget_usd is None else build_budget(max_cost_usd=args.budget_usd or None)
    try:
        state = run_debate(
            month=args.month, max_rounds=args.max_rounds, budget=budget, with_context=not args.no_context
        )
    except AGENT_FAILURES as exc:
        # Only reachable when the Analyst fails before a first draft: nothing to publish.
        print(f"Debate aborted, nothing published: {exc}")
        print(format_spend(budget))
        return 1
    print(format_transcript(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
