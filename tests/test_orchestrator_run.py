import json
import logging

import pytest
from orchestrator_fakes import CPI_DELTA, api_error

import whatsgoingon.orchestrator.run as run_module
from whatsgoingon.config import _optional_number
from whatsgoingon.logging_config import JsonFormatter, agent_logger
from whatsgoingon.orchestrator.budget import BudgetExceeded
from whatsgoingon.orchestrator.state import (
    ContextBrief,
    ContextEvent,
    Critique,
    DebateRound,
    DebateState,
    Draft,
    EditorDecision,
    Fallback,
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


def test_format_transcript_shows_fallbacks_and_spend() -> None:
    state = DebateState(month="2026-08", deltas=[CPI_DELTA], max_rounds=3)
    state.rounds.append(
        DebateRound(draft=Draft(narrative="draft", claims=[]), critiques=[], review_skipped="out of tokens")
    )
    state.fallbacks.append(Fallback(agent="skeptic", round=1, reason="out of tokens", action="unreviewed"))
    state.budget.record("analyst", model="claude-haiku-4-5", input_tokens=1000, output_tokens=100)

    transcript = run_module.format_transcript(state)

    assert "NOT REVIEWED - out of tokens" in transcript
    assert "## Fallbacks" in transcript
    assert "round 1, skeptic: out of tokens -> unreviewed" in transcript
    assert "- analyst: 1 calls, 1,100 tokens" in transcript


def test_format_transcript_shows_the_context_brief_first() -> None:
    state = DebateState(month="2026-08", deltas=[CPI_DELTA], max_rounds=3)
    state.context = ContextBrief(events=[ContextEvent(summary="OPEC cut.", related_series=["cpi"])])
    state.rounds.append(DebateRound(draft=Draft(narrative="the draft text", claims=[]), critiques=[]))

    transcript = run_module.format_transcript(state)

    assert transcript.index("[cpi] OPEC cut.") < transcript.index("the draft text")


def test_transcript_of_a_clean_cycle_has_no_fallbacks_section() -> None:
    state = DebateState(month="2026-08", deltas=[CPI_DELTA], max_rounds=3)

    assert "## Fallbacks" not in run_module.format_transcript(state)


def test_build_budget_uses_the_configured_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_module, "AGENT_BUDGET_TOKENS", {"analyst": 10, "skeptic": None, "editor": 30})
    monkeypatch.setattr(run_module, "AGENT_TIMEOUT_S", {"analyst": None, "skeptic": 5.0, "editor": None})

    budget = run_module.build_budget(max_cost_usd=0.25)

    assert budget.max_cost_usd == 0.25
    assert budget.agent_max_tokens == {"analyst": 10, "editor": 30}
    assert budget.agent_timeout_s == {"skeptic": 5.0}


def test_main_reports_a_debate_that_ran_out_of_budget_before_any_draft(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def run_debate(**kwargs):
        raise BudgetExceeded(agent="analyst", scope="cycle", spent=0.02, limit=0.01, unit="usd")

    monkeypatch.setattr(run_module, "run_debate", run_debate)
    monkeypatch.setattr(run_module, "configure_logging", lambda: None)

    assert run_module.main(["--month", "2026-08", "--budget-usd", "0.01"]) == 1
    assert "nothing published" in capsys.readouterr().out


def test_main_reports_an_api_failure_before_any_draft(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def run_debate(**kwargs):
        raise api_error(529)

    monkeypatch.setattr(run_module, "run_debate", run_debate)
    monkeypatch.setattr(run_module, "configure_logging", lambda: None)

    assert run_module.main(["--month", "2026-08"]) == 1
    assert "nothing published" in capsys.readouterr().out


def test_optional_number_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WGO_TEST_BUDGET", raising=False)
    assert _optional_number("WGO_TEST_BUDGET", 5, int) == 5
    monkeypatch.setenv("WGO_TEST_BUDGET", "")
    assert _optional_number("WGO_TEST_BUDGET", 5, int) is None
    monkeypatch.setenv("WGO_TEST_BUDGET", "0.5")
    assert _optional_number("WGO_TEST_BUDGET", 1.0, float) == 0.5


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
