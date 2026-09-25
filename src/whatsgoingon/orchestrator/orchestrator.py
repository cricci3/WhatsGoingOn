from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, replace
from typing import Any

import anthropic

from whatsgoingon.agent.state import SeriesDelta
from whatsgoingon.logging_config import agent_logger
from whatsgoingon.orchestrator.analyst import produce_draft
from whatsgoingon.orchestrator.budget import AgentBudget, AgentUnavailable, CycleBudget
from whatsgoingon.orchestrator.context import gather_context
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
# What an agent call can fail with that the cycle degrades on: one of its own limits (budget,
# time), or an API error that outlasted call_structured()'s retries. Anything else is a bug
# and should crash loudly rather than be dressed up as a fallback.
AGENT_FAILURES = (AgentUnavailable, anthropic.APIError)


async def run_debate_cycle(
    client: anthropic.Anthropic,
    *,
    month: str,
    deltas: list[SeriesDelta],
    analyst_model: str,
    skeptic_model: str,
    editor_model: str,
    context_model: str | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    budget: CycleBudget | None = None,
) -> DebateState:
    """Analyst drafts -> Skeptic critiques -> if the critiques are blocking (high-severity)
    and rounds remain, Analyst revises and the cycle repeats -> Editor decides to publish (or
    force-publishes once max_rounds is hit). The Editor can also send a non-blocked draft back
    for another round while rounds remain.

    Mirrors agent/loop.py's run_agent(): a plain loop over shared state, no framework,
    with the round counter as the mechanism that guarantees termination - at most
    max_rounds Analyst/Skeptic passes, and the pass that uses up the last round always
    ends in a published decision.

    Concurrency: the agents are synchronous (sync Anthropic client, sync tools), so each runs
    on a worker thread via asyncio.to_thread(), and asyncio only decides what overlaps. Almost
    everything here is a strict chain - the Skeptic needs the draft, the Editor needs the
    critiques - so the one place that runs in parallel is round 1: with a `context_model`, the
    Context agent researches the news *while* the Analyst writes the first draft, and the
    Skeptic starts once both are done - waiting max(analyst, context), not their sum. The
    brief then goes to the Skeptic and to the Analyst's revisions. (The Analyst's first draft
    doesn't see it - that's the price of running them side by side.)

    `deltas` is computed once by the caller (agent/state.py's compute_deltas()) and stored
    on the resulting DebateState as-is, so every agent in the cycle sees the same snapshot -
    see DebateState's docstring for why that matters to the Skeptic.

    `budget` (default: unlimited) caps per-agent tokens, the cycle's cost, and each agent
    turn's wall-clock time. When an agent hits a limit or fails with an API error its retries
    couldn't fix (AGENT_FAILURES), the cycle degrades explicitly instead of failing - each
    fallback is recorded in state.fallbacks, logged, and passed to the Editor so the changelog
    says so:
    - Analyst, round 1: nothing to publish yet, so the error propagates to the caller;
    - Analyst, later rounds: the previous round's draft goes to the Editor as-is;
    - Skeptic: the draft goes to the Editor unreviewed (DebateRound.review_skipped);
    - Editor: the latest draft is published unedited;
    - Context: the debate carries on without news context (it's an aid, not a step).
    After an Analyst or Skeptic fallback no further rounds start: the next Editor pass must
    publish.

    Each agent turn's wall-clock time is recorded into the budget next to its tokens and cost
    (per-agent latency), and the cycle's own wall-clock time lands on state.elapsed_s - with
    the parallel phase, that's less than the sum of the agents' latencies.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")
    state = DebateState(month=month, deltas=deltas, max_rounds=max_rounds, budget=budget or CycleBudget())
    must_publish = False

    for round_number in range(1, max_rounds + 1):
        log = agent_logger(__name__, agent="orchestrator", month=month, round=round_number)
        is_last_round = round_number == max_rounds

        analyst = _attempt(
            produce_draft, client, model=analyst_model, state=state, budget=state.budget.for_agent("analyst")
        )
        if round_number == 1 and context_model is not None:
            context = _attempt(
                gather_context,
                client,
                model=context_model,
                state=state,
                budget=state.budget.for_agent("context"),
            )
            started = time.monotonic()
            (draft, analyst_exc, analyst_s), (brief, context_exc, context_s) = await asyncio.gather(
                analyst, context
            )
            log.info(
                "parallel phase finished",
                extra={
                    "agents": ["analyst", "context"],
                    "wall_s": round(time.monotonic() - started, 2),
                    "analyst_s": round(analyst_s, 2),
                    "context_s": round(context_s, 2),
                },
            )
            _take_context(state, log, brief, context_exc)
        else:
            draft, analyst_exc, _ = await analyst

        if analyst_exc is not None:
            if not state.rounds:
                log.error(
                    "analyst failed before a first draft; nothing to publish",
                    extra={"error": _describe(analyst_exc), "budget": state.budget.summary()},
                )
                raise analyst_exc
            _fall_back(
                state,
                log,
                analyst_exc,
                agent="analyst",
                round_number=round_number,
                action="published the previous round's draft without revising it",
            )
            return await _publish(client, state, log, editor_model=editor_model)
        log.info(
            "analyst produced draft",
            extra={
                "speaker": "analyst",
                "narrative": draft.narrative,
                "claims": [asdict(c) for c in draft.claims],
            },
        )

        critiques, skeptic_exc, _ = await _attempt(
            critique_draft,
            client,
            model=skeptic_model,
            draft=draft,
            context=state.context,
            budget=state.budget.for_agent("skeptic"),
        )
        review_skipped = None
        if skeptic_exc is not None:
            critiques, review_skipped, must_publish = [], _describe(skeptic_exc), True
            _fall_back(
                state,
                log,
                skeptic_exc,
                agent="skeptic",
                round_number=round_number,
                action="sent the draft to the editor without a review",
            )
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

        if is_last_round or must_publish:
            return await _publish(client, state, log, editor_model=editor_model)
        decision = await _editor_decision(client, state, log, editor_model=editor_model, force_publish=False)
        if decision.action == "publish":
            return _finish(state, log, decision)
        state.rounds[-1] = replace(state.rounds[-1], editor_decision=decision)

    raise AssertionError("unreachable: the last round always publishes")


async def _attempt(
    agent_fn: Any, /, *args: Any, budget: AgentBudget, **kwargs: Any
) -> tuple[Any, Exception | None, float]:
    """Run one (synchronous) agent call on a worker thread: (result, None, seconds) on
    success, (None, error, seconds) on an AGENT_FAILURES error. Returning the error instead of
    raising lets asyncio.gather() hand back both halves of the parallel phase even when one
    fails, and leaves the fallback decision to the loop. Any other exception propagates.

    The turn's duration is recorded into `budget` either way - a turn that timed out cost
    the cycle its full time limit, and that belongs in the agent's latency too."""
    started = time.monotonic()
    try:
        result = await asyncio.to_thread(agent_fn, *args, budget=budget, **kwargs)
    except AGENT_FAILURES as exc:
        return None, exc, _record_turn(budget, started)
    return result, None, _record_turn(budget, started)


def _record_turn(budget: AgentBudget, started: float) -> float:
    elapsed = time.monotonic() - started
    budget.record_invocation(latency_s=elapsed)
    return elapsed


def _take_context(state: DebateState, log: logging.LoggerAdapter, brief: Any, exc: Exception | None) -> None:
    if exc is not None:
        _fall_back(state, log, exc, agent="context", round_number=1, action="carried on without news context")
        return
    state.context = brief
    log.info(
        "context gathered",
        extra={"speaker": "context", "events": [asdict(e) for e in brief.events], "notes": brief.notes},
    )


async def _publish(
    client: anthropic.Anthropic, state: DebateState, log: logging.LoggerAdapter, *, editor_model: str
) -> DebateState:
    """Final Editor pass, where "revise" is no longer an option."""
    decision = await _editor_decision(client, state, log, editor_model=editor_model, force_publish=True)
    return _finish(state, log, decision)


async def _editor_decision(
    client: anthropic.Anthropic,
    state: DebateState,
    log: logging.LoggerAdapter,
    *,
    editor_model: str,
    force_publish: bool,
) -> EditorDecision:
    decision, exc, _ = await _attempt(
        decide,
        client,
        model=editor_model,
        state=state,
        force_publish=force_publish,
        budget=state.budget.for_agent("editor"),
    )
    if exc is not None:
        _fall_back(
            state,
            log,
            exc,
            agent="editor",
            round_number=len(state.rounds),
            action="published the latest draft unedited",
        )
        return _unedited(state, reason=f"editor unavailable: {_describe(exc)}")

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


def _describe(exc: Exception) -> str:
    """Our own limit errors already read as sentences; for API errors, name the error type."""
    if isinstance(exc, AgentUnavailable):
        return str(exc)
    return f"API error after retries ({type(exc).__name__}: {exc})"


def _fall_back(
    state: DebateState,
    log: logging.LoggerAdapter,
    exc: Exception,
    *,
    agent: str,
    round_number: int,
    action: str,
) -> None:
    fallback = Fallback(agent=agent, round=round_number, reason=_describe(exc), action=action)
    state.fallbacks.append(fallback)
    log.warning(
        "agent failed; falling back",
        extra={
            "fallback": asdict(fallback),
            "error_type": type(exc).__name__,
            "budget": state.budget.summary(),
        },
    )


def _finish(state: DebateState, log: logging.LoggerAdapter, decision: EditorDecision) -> DebateState:
    state.decision = decision
    state.elapsed_s = time.monotonic() - state.started_monotonic
    log.info(
        "debate cycle finished",
        extra={
            "elapsed_s": round(state.elapsed_s, 3),
            "rounds_used": len(state.rounds),
            "max_rounds": state.max_rounds,
            "degraded": bool(state.fallbacks),
            "fallbacks": [asdict(f) for f in state.fallbacks],
            "budget": state.budget.summary(),
        },
    )
    return state
