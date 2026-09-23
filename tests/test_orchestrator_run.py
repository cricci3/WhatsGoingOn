import json
import logging

import pytest
from orchestrator_fakes import CPI_DELTA

import whatsgoingon.orchestrator.run as run_module
from whatsgoingon.logging_config import JsonFormatter, agent_logger
from whatsgoingon.orchestrator.state import (
    Critique,
    DebateRound,
    DebateState,
    Draft,
    EditorDecision,
)


def test_format_transcript_shows_every_round_and_the_final_decision() -> None:
    state = DebateState(month="2026-08", deltas=[CPI_DELTA], max_rounds=3)
    state.rounds.append(
        DebateRound(
            draft=Draft(narrative="first draft", claims=[]),
            critiques=[Critique(claim_id="c1", severity="high", comment="Unsupported.")],
        )
    )
    state.rounds.append(
        DebateRound(
            draft=Draft(narrative="second draft", claims=[]),
            critiques=[],
            editor_decision=EditorDecision(action="revise", reason="Tighten the ending."),
        )
    )
    state.decision = EditorDecision(
        action="publish", reason="ok", final_narrative="the final text", changelog="softened c1"
    )

    transcript = run_module.format_transcript(state)

    for expected in ["Unsupported.", "Tighten the ending.", "softened c1"]:
        assert expected in transcript
    positions = [transcript.index(text) for text in ["first draft", "second draft", "the final text"]]
    assert positions == sorted(positions)


def test_run_debate_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_module, "ANTHROPIC_API_KEY", None)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        run_module.run_debate(month="2026-08")


def test_run_debate_requires_ingested_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_module, "ANTHROPIC_API_KEY", "key")
    monkeypatch.setattr(run_module, "compute_deltas", lambda: [])

    with pytest.raises(RuntimeError, match="No data found"):
        run_module.run_debate(month="2026-08")


def test_agent_logger_stamps_context_and_keeps_per_call_extra(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="test.agent_logger"):
        agent_logger("test.agent_logger", agent="skeptic", round=2).info("hi", extra={"input_tokens": 5})

    payload = json.loads(JsonFormatter().format(caplog.records[0]))
    assert payload["agent"] == "skeptic"
    assert payload["round"] == 2
    assert payload["input_tokens"] == 5
