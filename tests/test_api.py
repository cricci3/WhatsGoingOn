from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from orchestrator_fakes import CPI_DELTA, api_error

import whatsgoingon.orchestrator.transcript as transcript_module
from whatsgoingon.api import app
from whatsgoingon.orchestrator.state import Critique, DebateRound, DebateState, Draft, EditorDecision

client = TestClient(app)


def test_index_serves_demo_page() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Generate new narrative" in response.text
    assert 'id="month"' in response.text


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_state_returns_404_when_no_narrative_yet(monkeypatch) -> None:
    fake_store = Mock(get_latest_narrative=Mock(return_value=None))
    monkeypatch.setattr("whatsgoingon.api.NarrativeStore", lambda: fake_store)

    response = client.get("/state")

    assert response.status_code == 404


def test_state_returns_latest_narrative(monkeypatch) -> None:
    latest = {
        "month": "2026-08",
        "narrative": "Markets calm.",
        "delta_summary": "cpi +0.1%",
        "generated_at": "2026-08-01T12:00:00+00:00",
    }
    fake_store = Mock(get_latest_narrative=Mock(return_value=latest))
    monkeypatch.setattr("whatsgoingon.api.NarrativeStore", lambda: fake_store)

    response = client.get("/state")

    assert response.status_code == 200
    assert response.json() == latest


def test_refresh_runs_a_cycle_and_returns_the_narrative(monkeypatch) -> None:
    expected = {
        "month": "2026-08",
        "narrative": "This month, not much changed.",
        "delta_summary": "cpi flat",
        "generated_at": "2026-08-01T12:00:00+00:00",
    }
    fake_run_cycle = Mock(return_value=expected)
    monkeypatch.setattr("whatsgoingon.api.run_cycle", fake_run_cycle)

    response = client.post("/refresh", params={"month": "2026-08"})

    assert response.status_code == 200
    assert response.json() == expected
    fake_run_cycle.assert_called_once_with(month="2026-08")


def test_refresh_defaults_month_to_today(monkeypatch) -> None:
    fake_run_cycle = Mock(side_effect=lambda *, month: {"month": month, "narrative": "narrative"})
    monkeypatch.setattr("whatsgoingon.api.run_cycle", fake_run_cycle)

    response = client.post("/refresh")

    assert response.status_code == 200
    called_month = fake_run_cycle.call_args.kwargs["month"]
    assert response.json()["month"] == called_month


def test_refresh_passes_through_future_month_warning(monkeypatch) -> None:
    """run_cycle() adds a "warning" key when the requested month is in the future (see
    test_agent_run.py); /refresh must forward it as-is so the demo page's JS can pop it up."""
    expected = {
        "month": "2099-01",
        "narrative": "This month, not much changed.",
        "warning": "2099-01 is a future month - there is no real data for it yet.",
    }
    fake_run_cycle = Mock(return_value=expected)
    monkeypatch.setattr("whatsgoingon.api.run_cycle", fake_run_cycle)

    response = client.post("/refresh", params={"month": "2099-01"})

    assert response.status_code == 200
    assert response.json() == expected


def test_refresh_maps_runtime_error_to_503(monkeypatch) -> None:
    fake_run_cycle = Mock(side_effect=RuntimeError("no data found"))
    monkeypatch.setattr("whatsgoingon.api.run_cycle", fake_run_cycle)

    response = client.post("/refresh")

    assert response.status_code == 503
    assert "no data found" in response.json()["detail"]


# --- Phase 2 debate transcript ---


def _debate_state(month: str = "2026-08") -> DebateState:
    state = DebateState(month=month, deltas=[CPI_DELTA], max_rounds=3)
    state.rounds.append(
        DebateRound(
            draft=Draft(narrative="draft", claims=[]),
            critiques=[Critique(claim_id="c1", severity="low", comment="Nitpick.")],
        )
    )
    state.decision = EditorDecision(action="publish", reason="ok", final_narrative="final", changelog="none")
    return state


@pytest.fixture
def debates_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(transcript_module, "DEBATES_PATH", tmp_path)
    return tmp_path


def test_debate_runs_a_cycle_returns_the_transcript_and_saves_it(monkeypatch, debates_dir: Path) -> None:
    fake_run_debate = Mock(return_value=_debate_state())
    monkeypatch.setattr("whatsgoingon.api.run_debate", fake_run_debate)

    response = client.post("/debate", params={"month": "2026-08", "max_rounds": 2, "context": False})

    assert response.status_code == 200
    body = response.json()
    assert body["rounds"][0]["critiques"][0]["comment"] == "Nitpick."
    assert body["final"]["narrative"] == "final"
    fake_run_debate.assert_called_once_with(month="2026-08", max_rounds=2, with_context=False)
    assert (debates_dir / "2026-08.json").exists()
    assert client.get("/debate/2026-08").json() == body
    assert client.get("/debate").json() == body


def test_debate_maps_setup_and_first_draft_failures_to_503(monkeypatch, debates_dir: Path) -> None:
    for error in [RuntimeError("no data found"), api_error(529)]:
        monkeypatch.setattr("whatsgoingon.api.run_debate", Mock(side_effect=error))

        response = client.post("/debate", params={"month": "2026-08"})

        assert response.status_code == 503
    assert list(debates_dir.iterdir()) == []


def test_debate_rejects_bad_parameters_without_running(monkeypatch, debates_dir: Path) -> None:
    fake_run_debate = Mock()
    monkeypatch.setattr("whatsgoingon.api.run_debate", fake_run_debate)

    assert client.post("/debate", params={"month": "08-2026"}).status_code == 422
    assert client.post("/debate", params={"max_rounds": 0}).status_code == 422
    fake_run_debate.assert_not_called()


def test_debate_transcripts_404_until_one_has_run(debates_dir: Path) -> None:
    assert client.get("/debate").status_code == 404
    assert client.get("/debate/2026-08").status_code == 404
    assert client.get("/debate/not-a-month").status_code == 422
