import pytest

from whatsgoingon.agent.state import SeriesDelta
from whatsgoingon.orchestrator.state import (
    Claim,
    Critique,
    DebateRound,
    DebateState,
    Draft,
    EditorDecision,
)

CPI_DELTA = SeriesDelta(
    name="cpi",
    latest_date="2026-08-01",
    latest_value=310.0,
    reference_date="2026-07-01",
    reference_value=300.0,
)
ENERGY_DELTA = SeriesDelta(
    name="cpi_energy",
    latest_date="2026-08-01",
    latest_value=120.0,
    reference_date="2026-07-01",
    reference_value=100.0,
)


def _claim(**overrides) -> Claim:
    defaults = dict(id="c1", text="CPI rose, driven by energy.", claim_type="causal")
    return Claim(**{**defaults, **overrides})


def _critique(**overrides) -> Critique:
    defaults = dict(claim_id="c1", severity="high", comment="Energy move doesn't explain the whole delta.")
    return Critique(**{**defaults, **overrides})


def test_claim_carries_real_series_deltas_not_just_a_name() -> None:
    claim = _claim(supporting_deltas=[CPI_DELTA, ENERGY_DELTA])

    assert claim.supporting_deltas == [CPI_DELTA, ENERGY_DELTA]
    assert claim.supporting_deltas[0].pct_change == pytest.approx((10.0 / 300.0) * 100)


def test_claim_supporting_deltas_defaults_to_empty_for_qualitative_claims() -> None:
    claim = _claim(text="Markets seem cautious heading into the print.", claim_type="factual")

    assert claim.supporting_deltas == []


def test_debate_state_delta_summary_is_derived_from_deltas_not_stored_separately() -> None:
    state = DebateState(month="2026-08", deltas=[CPI_DELTA], max_rounds=3)

    assert "cpi" in state.delta_summary
    assert "300.00" in state.delta_summary
    assert "310.00" in state.delta_summary


def test_debate_state_delta_summary_reflects_current_deltas_list() -> None:
    state = DebateState(month="2026-08", deltas=[CPI_DELTA], max_rounds=3)
    assert "cpi_energy" not in state.delta_summary

    state.deltas.append(ENERGY_DELTA)

    assert "cpi_energy" in state.delta_summary


def test_debate_state_round_number_starts_at_one_and_tracks_rounds() -> None:
    state = DebateState(month="2026-08", deltas=[CPI_DELTA], max_rounds=3)
    assert state.round_number == 1

    draft = Draft(narrative="...", claims=[_claim()])
    state.rounds.append(DebateRound(draft=draft, critiques=[]))

    assert state.round_number == 2


def test_debate_state_latest_draft_and_critiques_are_none_before_any_round() -> None:
    state = DebateState(month="2026-08", deltas=[CPI_DELTA], max_rounds=3)

    assert state.latest_draft is None
    assert state.latest_critiques == []
    assert state.has_blocking_critiques() is False


def test_debate_state_latest_draft_and_critiques_reflect_last_round() -> None:
    state = DebateState(month="2026-08", deltas=[CPI_DELTA], max_rounds=3)
    first_draft = Draft(narrative="first", claims=[_claim()])
    second_draft = Draft(narrative="second", claims=[_claim()])
    state.rounds.append(DebateRound(draft=first_draft, critiques=[_critique(severity="low")]))
    state.rounds.append(DebateRound(draft=second_draft, critiques=[_critique(severity="medium")]))

    assert state.latest_draft is second_draft
    assert state.latest_critiques == [_critique(severity="medium")]


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        ([], False),
        (["low"], False),
        (["low", "medium"], False),
        (["low", "high"], True),
        (["high"], True),
    ],
)
def test_has_blocking_critiques_true_only_with_a_high_severity_critique(
    severities: list[str], expected: bool
) -> None:
    state = DebateState(month="2026-08", deltas=[CPI_DELTA], max_rounds=3)
    draft = Draft(narrative="...", claims=[_claim()])
    critiques = [_critique(severity=s) for s in severities]
    state.rounds.append(DebateRound(draft=draft, critiques=critiques))

    assert state.has_blocking_critiques() is expected


def test_editor_decision_final_fields_are_optional_and_none_unless_published() -> None:
    revise = EditorDecision(action="revise", reason="claim c1 needs more support")
    publish = EditorDecision(
        action="publish", reason="critiques resolved", final_narrative="...", changelog="tightened claim c1"
    )

    assert revise.final_narrative is None
    assert revise.changelog is None
    assert publish.final_narrative == "..."
    assert publish.changelog == "tightened claim c1"
