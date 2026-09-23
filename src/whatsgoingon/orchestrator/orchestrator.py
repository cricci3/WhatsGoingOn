from __future__ import annotations

from dataclasses import asdict, replace

import anthropic

from whatsgoingon.agent.state import SeriesDelta
from whatsgoingon.logging_config import agent_logger
from whatsgoingon.orchestrator.analyst import produce_draft
from whatsgoingon.orchestrator.editor import decide
from whatsgoingon.orchestrator.skeptic import critique_draft
from whatsgoingon.orchestrator.state import DebateRound, DebateState, EditorDecision

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
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")
    state = DebateState(month=month, deltas=deltas, max_rounds=max_rounds)

    for round_number in range(1, max_rounds + 1):
        log = agent_logger(__name__, agent="orchestrator", month=month, round=round_number)
        is_last_round = round_number == max_rounds

        draft = produce_draft(client, model=analyst_model, state=state)
        log.info(
            "analyst produced draft",
            extra={
                "speaker": "analyst",
                "narrative": draft.narrative,
                "claims": [asdict(c) for c in draft.claims],
            },
        )

        critiques = critique_draft(client, model=skeptic_model, draft=draft)
        state.rounds.append(DebateRound(draft=draft, critiques=critiques))
        log.info(
            "skeptic raised critiques",
            extra={
                "speaker": "skeptic",
                "critiques": [asdict(c) for c in critiques],
                "blocking": state.has_blocking_critiques(),
            },
        )

        if state.has_blocking_critiques() and not is_last_round:
            log.info("blocking critiques; sending draft back to the analyst without an editor pass")
            continue

        decision = decide(client, model=editor_model, state=state)
        if decision.action == "revise" and is_last_round:
            # decide() doesn't offer "revise" on the last round; this only guards against that
            # contract being broken, so the loop still ends with something published.
            log.warning("editor asked to revise after the last round; publishing the latest draft")
            decision = EditorDecision(
                action="publish",
                reason=f"max_rounds reached; editor wanted another round: {decision.reason}",
                final_narrative=draft.narrative,
                changelog="(published unedited: max_rounds reached)",
            )
        log.info(
            "editor decided",
            extra={"speaker": "editor", "action": decision.action, "reason": decision.reason},
        )

        if decision.action == "publish":
            state.decision = decision
            log.info("debate cycle finished", extra={"rounds_used": round_number, "max_rounds": max_rounds})
            return state
        state.rounds[-1] = replace(state.rounds[-1], editor_decision=decision)

    raise AssertionError("unreachable: the last round always publishes")
