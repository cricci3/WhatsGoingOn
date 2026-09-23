from __future__ import annotations

import anthropic

from whatsgoingon.logging_config import agent_logger
from whatsgoingon.orchestrator.state import DebateState, EditorDecision, format_critiques, format_draft
from whatsgoingon.orchestrator.structured import call_structured

SYSTEM_PROMPT = """You are the Editor in a three-agent macro newsroom (Analyst, Skeptic, Editor). \
You see the whole debate so far: each round's draft from the Analyst and the Skeptic's critiques \
of it. Decide whether to publish the latest draft or send it back for another round.

- Publish when the remaining critiques are low/medium or have been addressed.
- Send it back ("revise") only when a real problem remains that another round could fix - say \
exactly what the Analyst should change in `reason`.

When publishing:
- final_narrative: the latest draft, lightly edited if needed to soften claims that unaddressed \
critiques still apply to. Don't add new claims or numbers.
- changelog: what changed across rounds and why, referring to claim ids and the critiques that \
prompted each change. If nothing changed, say so. If a high-severity critique was never resolved, \
say so explicitly - don't hide it.

Submit with the submit_decision tool."""


def _submit_decision_tool(*, allow_revise: bool) -> dict:
    return {
        "name": "submit_decision",
        "description": "Submit your decision on the latest draft.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["publish", "revise"] if allow_revise else ["publish"]},
                "reason": {"type": "string", "description": "Why - and, for revise, what to change."},
                "final_narrative": {"type": "string", "description": "Required when publishing."},
                "changelog": {"type": "string", "description": "Required when publishing."},
            },
            "required": ["action", "reason"],
        },
    }


def build_user_message(state: DebateState, *, forced: bool) -> str:
    parts = [
        f"Narrative for {state.month}. Change in tracked indicators over roughly the last 30 days:",
        state.delta_summary,
    ]
    for number, debate_round in enumerate(state.rounds, start=1):
        parts += [
            f"\n=== Round {number}: Analyst draft ===",
            format_draft(debate_round.draft),
            f"\n=== Round {number}: Skeptic critiques ===",
            format_critiques(debate_round.critiques),
        ]
        if debate_round.editor_decision is not None:
            parts += [f"\n=== Round {number}: you sent it back ===", debate_round.editor_decision.reason]
    if forced:
        parts.append(
            f"\nThis was the last of {state.max_rounds} rounds: you must publish now. Call out any "
            "unresolved high-severity critique in the changelog."
        )
    else:
        parts.append(f"\n{len(state.rounds)} of at most {state.max_rounds} rounds used. Publish or revise.")
    return "\n".join(parts)


def decide(
    client: anthropic.Anthropic,
    *,
    model: str,
    state: DebateState,
) -> EditorDecision:
    """Decide whether to publish state.latest_draft - producing the final narrative text
    and a changelog of what changed across rounds and why - or send it back for another
    Analyst/Skeptic round. Called once state.max_rounds is hit even if blocking critiques
    remain, so the orchestrator is guaranteed to terminate with a published narrative.

    Once max_rounds is used up, "revise" is removed from the tool schema itself rather than
    only discouraged in the prompt. If a publish decision comes back without a final
    narrative, the latest draft is published unedited (and logged).
    """
    if state.latest_draft is None:
        raise ValueError("decide() needs at least one completed round")
    forced = len(state.rounds) >= state.max_rounds
    log = agent_logger(__name__, agent="editor", month=state.month, round=len(state.rounds))
    output = call_structured(
        client,
        model=model,
        system=SYSTEM_PROMPT,
        user_message=build_user_message(state, forced=forced),
        output_tool=_submit_decision_tool(allow_revise=not forced),
        log=log,
    )

    action = "publish" if forced else output.get("action", "publish")
    reason = output.get("reason", "")
    if action == "revise":
        return EditorDecision(action="revise", reason=reason)

    final_narrative = output.get("final_narrative")
    if not final_narrative:
        log.warning("editor published without a final narrative; using the latest draft as-is")
        final_narrative = state.latest_draft.narrative
    changelog = output.get("changelog") or "(the Editor gave no changelog)"
    return EditorDecision(
        action="publish", reason=reason, final_narrative=final_narrative, changelog=changelog
    )
