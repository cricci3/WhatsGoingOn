"""Analyst, Skeptic and Editor each tested on their own against a mocked client: what they
send the model, and how they turn its structured output into shared-state objects."""

import pytest
from orchestrator_fakes import CPI_DELTA, OIL_DELTA, client_with, response, tool_use

from whatsgoingon.orchestrator.analyst import produce_draft
from whatsgoingon.orchestrator.context import gather_context
from whatsgoingon.orchestrator.editor import decide
from whatsgoingon.orchestrator.skeptic import critique_draft
from whatsgoingon.orchestrator.state import (
    Claim,
    ContextBrief,
    ContextEvent,
    Critique,
    DebateRound,
    DebateState,
    Draft,
    EditorDecision,
    Fallback,
)

MODEL = "claude-haiku-4-5"


def _state(max_rounds: int = 3) -> DebateState:
    return DebateState(month="2026-08", deltas=[CPI_DELTA, OIL_DELTA], max_rounds=max_rounds)


def _draft() -> Draft:
    claim = Claim(
        id="c1",
        text="Oil drove CPI up.",
        claim_type="causal",
        supporting_deltas=[CPI_DELTA, OIL_DELTA],
        confidence="high",
    )
    return Draft(narrative="Oil drove CPI up.", claims=[claim])


def _submitted_draft(**overrides) -> dict:
    claim = {
        "id": "c1",
        "text": "CPI rose 3.3%.",
        "claim_type": "factual",
        "supporting_series": ["cpi"],
        "confidence": "high",
    }
    return {"narrative": "CPI rose.", "claims": [{**claim, **overrides}]}


def _analyst_client(submitted: dict):
    return client_with(response(tool_use("submit_draft", submitted)))


# --- Analyst ---


def test_analyst_attaches_real_series_deltas_to_claims() -> None:
    client = _analyst_client(_submitted_draft(supporting_series=["cpi", "wti_oil"]))

    draft = produce_draft(client, model=MODEL, state=_state(), tools=[], tool_impls={})

    assert draft.narrative == "CPI rose."
    assert draft.claims[0].supporting_deltas == [CPI_DELTA, OIL_DELTA]
    assert draft.claims[0].confidence == "high"


def test_analyst_drops_untracked_series_instead_of_inventing_a_delta() -> None:
    client = _analyst_client(_submitted_draft(supporting_series=["cpi", "gdp"]))

    draft = produce_draft(client, model=MODEL, state=_state(), tools=[], tool_impls={})

    assert draft.claims[0].supporting_deltas == [CPI_DELTA]


def test_analyst_restricts_supporting_series_to_tracked_names_in_the_schema() -> None:
    client = _analyst_client(_submitted_draft())

    produce_draft(client, model=MODEL, state=_state(), tools=[], tool_impls={})

    output_tool = client.messages.create.call_args.kwargs["tools"][-1]
    claim_schema = output_tool["input_schema"]["properties"]["claims"]["items"]
    assert claim_schema["properties"]["supporting_series"]["items"]["enum"] == ["cpi", "wti_oil"]


def test_analyst_assigns_fresh_ids_to_missing_or_duplicate_claim_ids() -> None:
    claim = _submitted_draft()["claims"][0]
    claims = [{**claim, "id": "c1"}, {**claim, "id": "c1"}, {**claim, "id": ""}]
    submitted = {"narrative": "...", "claims": claims}

    draft = produce_draft(_analyst_client(submitted), model=MODEL, state=_state(), tools=[], tool_impls={})

    assert len({claim.id for claim in draft.claims}) == 3


def test_analyst_revision_prompt_includes_previous_draft_critiques_and_editor_note() -> None:
    state = _state()
    state.rounds.append(
        DebateRound(
            draft=_draft(),
            critiques=[Critique(claim_id="c1", severity="high", comment="Only co-movement shown.")],
            editor_decision=EditorDecision(action="revise", reason="Soften c1."),
        )
    )
    client = _analyst_client(_submitted_draft())

    produce_draft(client, model=MODEL, state=state, tools=[], tool_impls={})

    message = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "round 2 of at most 3" in message
    assert "Oil drove CPI up." in message
    assert "Only co-movement shown." in message
    assert "Soften c1." in message


# --- Skeptic ---


def test_skeptic_sees_each_claims_supporting_data() -> None:
    client = client_with(response(tool_use("submit_critiques", {"critiques": []})))

    critiques = critique_draft(client, model=MODEL, draft=_draft())

    assert critiques == []
    message = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "[c1] (causal, confidence: high)" in message
    assert "wti_oil: 75.00" in message


def test_skeptic_returns_critiques_and_drops_ones_targeting_unknown_claims() -> None:
    submitted = {
        "critiques": [
            {"claim_id": "c1", "severity": "high", "comment": "Correlation, not causation."},
            {"claim_id": "c9", "severity": "low", "comment": "No such claim."},
        ]
    }
    client = client_with(response(tool_use("submit_critiques", submitted)))

    critiques = critique_draft(client, model=MODEL, draft=_draft())

    assert critiques == [Critique(claim_id="c1", severity="high", comment="Correlation, not causation.")]


def test_skeptic_has_no_research_tools() -> None:
    client = client_with(response(tool_use("submit_critiques", {"critiques": []})))

    critique_draft(client, model=MODEL, draft=_draft())

    assert [t["name"] for t in client.messages.create.call_args.kwargs["tools"]] == ["submit_critiques"]


# --- Editor ---


def _state_with_rounds(n: int, max_rounds: int = 3) -> DebateState:
    state = _state(max_rounds=max_rounds)
    for _ in range(n):
        state.rounds.append(DebateRound(draft=_draft(), critiques=[]))
    return state


def test_editor_publish_carries_final_narrative_and_changelog() -> None:
    submitted = {
        "action": "publish", "reason": "ok", "final_narrative": "Final.", "changelog": "Softened c1."
    }
    client = client_with(response(tool_use("submit_decision", submitted)))

    decision = decide(client, model=MODEL, state=_state_with_rounds(1))

    assert decision == EditorDecision(
        action="publish", reason="ok", final_narrative="Final.", changelog="Softened c1."
    )


def test_editor_revise_has_no_final_fields() -> None:
    submitted = {"action": "revise", "reason": "Fix c1.", "final_narrative": "ignored"}
    client = client_with(response(tool_use("submit_decision", submitted)))

    decision = decide(client, model=MODEL, state=_state_with_rounds(1))

    assert decision == EditorDecision(action="revise", reason="Fix c1.")


def test_editor_cannot_revise_once_max_rounds_is_used_up() -> None:
    submitted = {"action": "revise", "reason": "Still wrong."}
    client = client_with(response(tool_use("submit_decision", submitted)))

    decision = decide(client, model=MODEL, state=_state_with_rounds(3, max_rounds=3))

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tools"][-1]["input_schema"]["properties"]["action"]["enum"] == ["publish"]
    assert "must publish now" in kwargs["messages"][0]["content"]
    assert decision.action == "publish"
    assert decision.final_narrative == "Oil drove CPI up."  # fell back to the latest draft


def test_editor_force_publish_removes_revise_before_max_rounds() -> None:
    client = client_with(response(tool_use("submit_decision", {"action": "publish", "reason": "ok"})))

    decide(client, model=MODEL, state=_state_with_rounds(1, max_rounds=3), force_publish=True)

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tools"][-1]["input_schema"]["properties"]["action"]["enum"] == ["publish"]


def test_editor_is_told_about_unreviewed_drafts_and_fallbacks() -> None:
    state = _state()
    state.rounds.append(DebateRound(draft=_draft(), critiques=[], review_skipped="skeptic out of tokens"))
    state.fallbacks.append(
        Fallback(agent="skeptic", round=1, reason="skeptic out of tokens", action="sent it unreviewed")
    )
    client = client_with(response(tool_use("submit_decision", {"action": "publish", "reason": "ok"})))

    decide(client, model=MODEL, state=state, force_publish=True)

    message = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "NOT REVIEWED - skeptic out of tokens" in message
    assert "The cycle ran degraded" in message
    assert "sent it unreviewed" in message


def test_editor_requires_a_completed_round() -> None:
    with pytest.raises(ValueError):
        decide(client_with(), model=MODEL, state=_state())


# --- Context ---

BRIEF = ContextBrief(
    events=[
        ContextEvent(summary="OPEC announced output cuts.", related_series=["wti_oil"], source="Reuters")
    ],
    notes="No news found for cpi.",
)


def test_context_maps_events_and_drops_untracked_series() -> None:
    submitted = {
        "events": [{"summary": "OPEC cut.", "related_series": ["wti_oil", "gdp"], "source": "Reuters"}],
        "notes": "nothing on cpi",
    }
    client = client_with(response(tool_use("submit_context", submitted)))

    brief = gather_context(client, model=MODEL, state=_state(), tools=[], tool_impls={})

    assert brief == ContextBrief(
        events=[ContextEvent(summary="OPEC cut.", related_series=["wti_oil"], source="Reuters")],
        notes="nothing on cpi",
    )


def test_context_default_tools_are_read_only() -> None:
    client = client_with(response(tool_use("submit_context", {"events": []})))

    gather_context(client, model=MODEL, state=_state())

    names = [t["name"] for t in client.messages.create.call_args.kwargs["tools"]]
    assert "fetch_fred_series" not in names  # writes to SQLite while the Analyst may be using it
    assert {"get_ticker_news", "query_observations", "submit_context"} <= set(names)


def test_skeptic_sees_the_context_briefing_when_given() -> None:
    client = client_with(response(tool_use("submit_critiques", {"critiques": []})))

    critique_draft(client, model=MODEL, draft=_draft(), context=BRIEF)

    message = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "News briefing from the Context researcher" in message
    assert "[wti_oil] OPEC announced output cuts. (source: Reuters)" in message
    assert "Notes: No news found for cpi." in message


def test_analyst_revision_includes_the_context_briefing() -> None:
    state = _state()
    state.context = BRIEF
    state.rounds.append(DebateRound(draft=_draft(), critiques=[]))
    client = _analyst_client(_submitted_draft())

    produce_draft(client, model=MODEL, state=state, tools=[], tool_impls={})

    assert "OPEC announced output cuts." in client.messages.create.call_args.kwargs["messages"][0]["content"]
