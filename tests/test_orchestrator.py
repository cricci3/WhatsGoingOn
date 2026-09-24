"""Orchestration logic with each agent mocked separately: who gets called, in what order,
with what state, and that the round counter always ends the cycle."""

from unittest.mock import Mock

import pytest
from orchestrator_fakes import CPI_DELTA

import whatsgoingon.orchestrator.orchestrator as orchestrator_module
from whatsgoingon.orchestrator.budget import BudgetExceeded, CycleBudget
from whatsgoingon.orchestrator.orchestrator import run_debate_cycle
from whatsgoingon.orchestrator.state import Critique, DebateState, Draft, EditorDecision

HIGH = Critique(claim_id="c1", severity="high", comment="Unsupported.")
LOW = Critique(claim_id="c1", severity="low", comment="Nitpick.")
PUBLISH = EditorDecision(action="publish", reason="ok", final_narrative="Final.", changelog="none")
REVISE = EditorDecision(action="revise", reason="Tighten c1.")


def _draft(n: int) -> Draft:
    return Draft(narrative=f"draft {n}", claims=[])


@pytest.fixture
def agents(monkeypatch: pytest.MonkeyPatch) -> dict[str, Mock]:
    mocks = {
        "produce_draft": Mock(side_effect=[_draft(n) for n in range(1, 10)]),
        "critique_draft": Mock(return_value=[]),
        "decide": Mock(return_value=PUBLISH),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(orchestrator_module, name, mock)
    return mocks


def _run(max_rounds: int = 3, budget: CycleBudget | None = None) -> DebateState:
    return run_debate_cycle(
        Mock(),
        month="2026-08",
        deltas=[CPI_DELTA],
        analyst_model="analyst-model",
        skeptic_model="skeptic-model",
        editor_model="editor-model",
        max_rounds=max_rounds,
        budget=budget,
    )


def test_clean_draft_is_published_after_one_round(agents: dict[str, Mock]) -> None:
    state = _run()

    assert len(state.rounds) == 1
    assert state.decision == PUBLISH
    assert agents["produce_draft"].call_count == 1
    skeptic_kwargs = agents["critique_draft"].call_args.kwargs
    assert (skeptic_kwargs["model"], skeptic_kwargs["draft"]) == ("skeptic-model", _draft(1))
    assert agents["decide"].call_args.kwargs["model"] == "editor-model"


def test_analyst_gets_its_own_model_and_the_shared_deltas(agents: dict[str, Mock]) -> None:
    _run()

    analyst_kwargs = agents["produce_draft"].call_args.kwargs
    assert analyst_kwargs["model"] == "analyst-model"
    assert analyst_kwargs["state"].deltas == [CPI_DELTA]


def test_blocking_critiques_skip_the_editor_and_go_back_to_the_analyst(agents: dict[str, Mock]) -> None:
    agents["critique_draft"].side_effect = [[HIGH], [LOW]]

    state = _run()

    assert [r.draft.narrative for r in state.rounds] == ["draft 1", "draft 2"]
    assert agents["decide"].call_count == 1
    assert state.decision == PUBLISH


def test_analyst_revision_sees_the_previous_round(agents: dict[str, Mock]) -> None:
    seen_rounds = []
    drafts = iter([_draft(1), _draft(2)])

    def produce_draft(client, *, model, state, budget):
        seen_rounds.append(list(state.rounds))
        return next(drafts)

    agents["produce_draft"].side_effect = produce_draft
    agents["critique_draft"].side_effect = [[HIGH], []]

    _run()

    assert seen_rounds[0] == []
    assert seen_rounds[1][0].draft == _draft(1)
    assert seen_rounds[1][0].critiques == [HIGH]


def test_editor_revise_is_recorded_on_the_round_and_triggers_another_one(agents: dict[str, Mock]) -> None:
    agents["decide"].side_effect = [REVISE, PUBLISH]

    state = _run()

    assert len(state.rounds) == 2
    assert state.rounds[0].editor_decision == REVISE
    assert state.rounds[1].editor_decision is None
    assert state.decision == PUBLISH


def test_persistent_blocking_critiques_stop_at_max_rounds_and_still_publish(agents: dict[str, Mock]) -> None:
    agents["critique_draft"].return_value = [HIGH]

    state = _run(max_rounds=3)

    assert len(state.rounds) == 3
    assert agents["produce_draft"].call_count == 3
    assert agents["decide"].call_count == 1  # only on the last round, where it must publish
    assert state.decision == PUBLISH


def test_editor_that_keeps_revising_is_overridden_on_the_last_round(agents: dict[str, Mock]) -> None:
    agents["decide"].return_value = REVISE

    state = _run(max_rounds=2)

    assert len(state.rounds) == 2
    assert state.decision.action == "publish"
    assert state.decision.final_narrative == "draft 2"


def test_single_round_goes_straight_to_the_editor_despite_blocking_critiques(agents: dict[str, Mock]) -> None:
    agents["critique_draft"].return_value = [HIGH]

    state = _run(max_rounds=1)

    assert len(state.rounds) == 1
    assert agents["decide"].call_count == 1
    assert state.decision == PUBLISH


def test_max_rounds_below_one_is_rejected(agents: dict[str, Mock]) -> None:
    with pytest.raises(ValueError):
        _run(max_rounds=0)


# --- budget fallbacks ---


def _out_of_budget(agent: str) -> BudgetExceeded:
    return BudgetExceeded(agent=agent, scope="agent", spent=100, limit=100, unit="tokens")


def test_each_agent_gets_its_own_view_of_the_shared_budget(agents: dict[str, Mock]) -> None:
    budget = CycleBudget()

    state = _run(budget=budget)

    assert state.budget is budget
    for mock, agent in [(agents["produce_draft"], "analyst"), (agents["critique_draft"], "skeptic")]:
        view = mock.call_args.kwargs["budget"]
        assert (view.cycle, view.agent) == (budget, agent)
    assert agents["decide"].call_args.kwargs["budget"].agent == "editor"
    assert state.fallbacks == []


def test_analyst_out_of_budget_before_a_first_draft_fails_loudly(agents: dict[str, Mock]) -> None:
    agents["produce_draft"].side_effect = _out_of_budget("analyst")

    with pytest.raises(BudgetExceeded):
        _run()
    agents["critique_draft"].assert_not_called()
    agents["decide"].assert_not_called()


def test_analyst_out_of_budget_on_a_revision_publishes_the_previous_draft(agents: dict[str, Mock]) -> None:
    agents["produce_draft"].side_effect = [_draft(1), _out_of_budget("analyst")]
    agents["critique_draft"].return_value = [HIGH]

    state = _run(max_rounds=3)

    assert [r.draft.narrative for r in state.rounds] == ["draft 1"]
    assert agents["decide"].call_args.kwargs["force_publish"] is True
    assert state.decision == PUBLISH
    [fallback] = state.fallbacks
    assert (fallback.agent, fallback.round) == ("analyst", 2)


def test_skeptic_out_of_budget_sends_the_draft_to_the_editor_unreviewed(agents: dict[str, Mock]) -> None:
    agents["critique_draft"].side_effect = _out_of_budget("skeptic")

    state = _run(max_rounds=3)

    [debate_round] = state.rounds
    assert debate_round.critiques == []
    assert "skeptic's token budget" in debate_round.review_skipped
    assert agents["decide"].call_args.kwargs["force_publish"] is True  # no unreviewed extra rounds
    assert [f.agent for f in state.fallbacks] == ["skeptic"]
    assert state.decision == PUBLISH


def test_editor_out_of_budget_publishes_the_latest_draft_unedited(agents: dict[str, Mock]) -> None:
    agents["decide"].side_effect = _out_of_budget("editor")

    state = _run()

    assert state.decision.action == "publish"
    assert state.decision.final_narrative == "draft 1"
    assert "editor's token budget" in state.decision.changelog
    assert [f.agent for f in state.fallbacks] == ["editor"]


def test_everything_out_of_budget_after_round_one_still_publishes(agents: dict[str, Mock]) -> None:
    agents["produce_draft"].side_effect = [_draft(1), _out_of_budget("analyst")]
    agents["critique_draft"].return_value = [HIGH]
    agents["decide"].side_effect = _out_of_budget("editor")

    state = _run()

    assert state.decision.final_narrative == "draft 1"
    assert [f.agent for f in state.fallbacks] == ["analyst", "editor"]
    assert "analyst" in state.decision.changelog
    assert "editor" in state.decision.changelog
