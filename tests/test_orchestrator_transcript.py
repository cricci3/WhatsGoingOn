import json

import pytest
from orchestrator_fakes import CPI_DELTA, OIL_DELTA

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
from whatsgoingon.orchestrator.transcript import (
    load_latest_transcript,
    load_transcript,
    save_transcript,
    transcript_to_dict,
)


def _state(month: str = "2026-08") -> DebateState:
    state = DebateState(month=month, deltas=[CPI_DELTA, OIL_DELTA], max_rounds=3)
    state.context = ContextBrief(events=[ContextEvent(summary="OPEC cut.", related_series=["wti_oil"])])
    claim = Claim(
        id="c1", text="Oil drove CPI.", claim_type="causal", supporting_deltas=[CPI_DELTA, OIL_DELTA]
    )
    state.rounds.append(
        DebateRound(
            draft=Draft(narrative="first", claims=[claim]),
            critiques=[Critique(claim_id="c1", severity="medium", comment="Timing?")],
            editor_decision=EditorDecision(action="revise", reason="Check the timing."),
        )
    )
    state.rounds.append(
        DebateRound(draft=Draft(narrative="second", claims=[claim]), critiques=[], review_skipped="timed out")
    )
    state.fallbacks.append(Fallback(agent="skeptic", round=2, reason="timed out", action="unreviewed"))
    state.decision = EditorDecision(
        action="publish", reason="ok", final_narrative="final", changelog="c1 softened"
    )
    state.budget.record(
        "analyst", model="claude-haiku-4-5", input_tokens=1000, output_tokens=100, latency_s=2.0
    )
    state.budget.record_invocation("analyst", latency_s=3.0)
    state.elapsed_s = 5.0
    return state


def test_transcript_has_every_round_the_final_and_the_spend() -> None:
    transcript = transcript_to_dict(_state())

    assert (transcript["month"], transcript["rounds_used"], transcript["degraded"]) == ("2026-08", 2, True)
    first, second = transcript["rounds"]
    assert first["draft"]["claims"][0]["supporting_series"] == ["cpi", "wti_oil"]
    assert first["critiques"] == [{"claim_id": "c1", "severity": "medium", "comment": "Timing?"}]
    assert first["editor_note"] == "Check the timing."
    assert (second["review_skipped"], second["editor_note"]) == ("timed out", None)
    assert transcript["final"] == {"narrative": "final", "reason": "ok", "changelog": "c1 softened"}
    assert transcript["context"]["events"][0]["related_series"] == ["wti_oil"]
    assert transcript["deltas"][0]["name"] == "cpi"
    assert transcript["deltas"][0]["pct_change"] == pytest.approx(10 / 3)
    analyst = transcript["spend"]["agents"]["analyst"]
    assert (analyst["latency_s"], analyst["model_latency_s"]) == (3.0, 2.0)
    assert transcript["spend"]["elapsed_s"] == 5.0
    json.dumps(transcript)  # must be JSON-serializable as-is


def test_save_and_load_round_trip(tmp_path) -> None:
    transcript = transcript_to_dict(_state())

    path = save_transcript(transcript, tmp_path)

    assert path.name == "2026-08.json"
    assert load_transcript("2026-08", tmp_path) == transcript
    assert load_transcript("2026-07", tmp_path) is None


def test_latest_is_the_most_recent_month(tmp_path) -> None:
    for month in ["2026-07", "2026-09", "2026-08"]:
        save_transcript(transcript_to_dict(_state(month)), tmp_path)

    assert load_latest_transcript(tmp_path)["month"] == "2026-09"
    assert load_latest_transcript(tmp_path / "missing") is None


@pytest.mark.parametrize("month", ["../secrets", "2026-8", "2026-08.json"])
def test_month_must_be_yyyy_mm(tmp_path, month: str) -> None:
    with pytest.raises(ValueError):
        load_transcript(month, tmp_path)
