from __future__ import annotations

import logging
from dataclasses import asdict, replace

import anthropic

from whatsgoingon.agent.state import SeriesDelta
from whatsgoingon.logging_config import agent_logger
from whatsgoingon.orchestrator.analyst import produce_draft
from whatsgoingon.orchestrator.budget import BudgetExceeded, CycleBudget
from whatsgoingon.orchestrator.editor import decide
from whatsgoingon.orchestrator.skeptic import critique_draft
from whatsgoingon.orchestrator.state import (
    DebateRound,
    DebateState,
    EditorDecision,
    Fallback,
    format_fallbacks,
)

DEFAULT_MAX_ROUNDS = 3


def run_debate_cycle(
    client: anthropic.Anthropic,
    *,
    month: str,
    deltas: list[SeriesDelta],
    analyst_model: str,
    skeptic_model: str,
    editor_model: str,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    budget: CycleBudget | None = None,
) -> DebateState:
    """Sequential orchestration: Analyst drafts -> Skeptic critiques -> if the critiques
    are blocking (high-severity) and rounds remain, Analyst revises and the cycle repeats
    -> Editor decides to publish (or force-publishes once max_rounds is hit). The Editor
    can also send a non-blocked draft back for another round while rounds remain.

    Mirrors agent/loop.py's run_agent(): a plain loop over shared state, no framework,
    with the round counter as the mechanism that guarantees termination - at most
    max_rounds Analyst/Skeptic passes, and the pass that uses up the last round always
    ends in a published decision.

    `deltas` is computed once by the caller (agent/state.py's compute_deltas()) and stored
    on the resulting DebateState as-is, so every agent in the cycle sees the same snapshot -
    see DebateState's docstring for why that matters to the Skeptic.

    `budget` (default: unlimited) caps per-agent tokens and the cycle's cost. When an agent
    hits a limit, the cycle degrades explicitly instead of failing - each fallback is
    recorded in state.fallbacks, logged, and passed to the Editor so the changelog says so:
    - Analyst, round 1: nothing to publish yet, so BudgetExceeded propagates to the caller;
    - Analyst, later rounds: the previous round's draft goes to the Editor as-is;
    - Skeptic: the draft goes to the Editor unreviewed (DebateRound.review_skipped);
    - Editor: the latest draft is published unedited.
    After any fallback no further rounds start: the next Editor pass must publish.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")
    state = DebateState(month=month, deltas=deltas, max_rounds=max_rounds, budget=budget or CycleBudget())

    for round_number in range(1, max_rounds + 1):
        log = agent_logger(__name__, agent="orchestrator", month=month, round=round_number)
        is_last_round = round_number == max_rounds

        try:
            draft = produce_draft(
                client, model=analyst_model, state=state, budget=state.budget.for_agent("analyst")
            )
        except BudgetExceeded as exc:
            if not state.rounds:
                log.error(
                    "analyst out of budget before a first draft; nothing to publish",
                    extra={"budget": state.budget.summary()},
                )
                raise
            _fall_back(
                state, log, exc, round_number, "published the previous round's draft without revising it"
            )
            return _publish(client, state, log, editor_model=editor_model)
        log.info(
            "analyst produced draft",
            extra={
                "speaker": "analyst",
                "narrative": draft.narrative,
                "claims": [asdict(c) for c in draft.claims],
            },
        )

        review_skipped = None
        try:
            critiques = critique_draft(
                client, model=skeptic_model, draft=draft, budget=state.budget.for_agent("skeptic")
            )
        except BudgetExceeded as exc:
            critiques, review_skipped = [], str(exc)
            _fall_back(state, log, exc, round_number, "sent the draft to the editor without a review")
        state.rounds.append(DebateRound(draft=draft, critiques=critiques, review_skipped=review_skipped))
        log.info(
            "skeptic raised critiques",
            extra={
                "speaker": "skeptic",
                "critiques": [asdict(c) for c in critiques],
                "blocking": state.has_blocking_critiques(),
                "review_skipped": review_skipped,
            },
        )

        if state.has_blocking_critiques() and not is_last_round:
            log.info("blocking critiques; sending draft back to the analyst without an editor pass")
            continue

        if is_last_round or state.fallbacks:
            return _publish(client, state, log, editor_model=editor_model)
        decision = _editor_decision(client, state, log, editor_model=editor_model, force_publish=False)
        if decision.action == "publish":
            return _finish(state, log, decision)
        state.rounds[-1] = replace(state.rounds[-1], editor_decision=decision)

    raise AssertionError("unreachable: the last round always publishes")


def _publish(
    client: anthropic.Anthropic, state: DebateState, log: logging.LoggerAdapter, *, editor_model: str
) -> DebateState:
    """Final Editor pass, where "revise" is no longer an option."""
    decision = _editor_decision(client, state, log, editor_model=editor_model, force_publish=True)
    return _finish(state, log, decision)


def _editor_decision(
    client: anthropic.Anthropic,
    state: DebateState,
    log: logging.LoggerAdapter,
    *,
    editor_model: str,
    force_publish: bool,
) -> EditorDecision:
    try:
        decision = decide(
            client,
            model=editor_model,
            state=state,
            force_publish=force_publish,
            budget=state.budget.for_agent("editor"),
        )
    except BudgetExceeded as exc:
        _fall_back(state, log, exc, len(state.rounds), "published the latest draft unedited")
        return _unedited(state, reason=f"editor unavailable: {exc}")

    if decision.action == "revise" and force_publish:
        # decide() doesn't offer "revise" when publishing is forced; this only guards against
        # that contract being broken, so the loop still ends with something published.
        log.warning("editor asked to revise when it had to publish; publishing the latest draft")
        state.fallbacks.append(
            Fallback(
                agent="editor",
                round=len(state.rounds),
                reason=f"asked to revise when it had to publish: {decision.reason}",
                action="published the latest draft unedited",
            )
        )
        decision = _unedited(state, reason=f"no rounds left; editor wanted another one: {decision.reason}")
    log.info(
        "editor decided",
        extra={"speaker": "editor", "action": decision.action, "reason": decision.reason},
    )
    return decision


def _unedited(state: DebateState, *, reason: str) -> EditorDecision:
    """Publish the latest draft verbatim, with a changelog that lists every fallback taken -
    the one place a degraded cycle could otherwise look like a normal one."""
    return EditorDecision(
        action="publish",
        reason=reason,
        final_narrative=state.latest_draft.narrative,
        changelog="(published unedited - the cycle ran degraded)\n" + format_fallbacks(state.fallbacks),
    )


def _fall_back(
    state: DebateState, log: logging.LoggerAdapter, exc: BudgetExceeded, round_number: int, action: str
) -> None:
    fallback = Fallback(agent=exc.agent, round=round_number, reason=str(exc), action=action)
    state.fallbacks.append(fallback)
    log.warning(
        "budget exceeded; falling back",
        extra={"fallback": asdict(fallback), "scope": exc.scope, "budget": state.budget.summary()},
    )


def _finish(state: DebateState, log: logging.LoggerAdapter, decision: EditorDecision) -> DebateState:
    state.decision = decision
    log.info(
        "debate cycle finished",
        extra={
            "rounds_used": len(state.rounds),
            "max_rounds": state.max_rounds,
            "degraded": bool(state.fallbacks),
            "fallbacks": [asdict(f) for f in state.fallbacks],
            "budget": state.budget.summary(),
        },
    )
    return state
