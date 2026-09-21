from unittest.mock import Mock

from fastapi.testclient import TestClient

from whatsgoingon.api import app

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
