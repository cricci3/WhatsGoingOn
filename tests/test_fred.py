from unittest.mock import Mock, patch

import pytest
import requests

from whatsgoingon.sources import fred


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("whatsgoingon.retry.time.sleep", lambda _seconds: None)


def _fake_response(observations: list[dict]) -> Mock:
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {"observations": observations}
    return response


@patch("whatsgoingon.sources.fred.requests.get")
def test_fetch_series_maps_observations(mock_get: Mock) -> None:
    mock_get.return_value = _fake_response(
        [
            {"date": "2026-01-01", "value": "310.3"},
            {"date": "2026-02-01", "value": "311.1"},
        ]
    )

    result = fred.fetch_series("CPIAUCSL", "cpi", api_key="fake-key")

    assert len(result) == 2
    assert result[0].source == "fred"
    assert result[0].series_id == "CPIAUCSL"
    assert result[0].name == "cpi"
    assert result[0].date == "2026-01-01"
    assert result[0].value == 310.3


@patch("whatsgoingon.sources.fred.requests.get")
def test_fetch_series_skips_missing_values(mock_get: Mock) -> None:
    mock_get.return_value = _fake_response(
        [
            {"date": "2026-01-01", "value": "."},
            {"date": "2026-02-01", "value": "311.1"},
        ]
    )

    result = fred.fetch_series("CPIAUCSL", "cpi", api_key="fake-key")

    assert len(result) == 1
    assert result[0].date == "2026-02-01"


@patch("whatsgoingon.sources.fred.requests.get")
def test_fetch_series_raises_on_http_error(mock_get: Mock) -> None:
    mock_get.return_value.raise_for_status.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        fred.fetch_series("CPIAUCSL", "cpi", api_key="fake-key")


@patch("whatsgoingon.sources.fred.requests.get")
def test_fetch_series_retries_on_connection_error_then_succeeds(mock_get: Mock) -> None:
    mock_get.side_effect = [
        requests.exceptions.ConnectionError("DNS blip"),
        _fake_response([{"date": "2026-01-01", "value": "310.3"}]),
    ]

    result = fred.fetch_series("CPIAUCSL", "cpi", api_key="fake-key")

    assert mock_get.call_count == 2
    assert len(result) == 1
    assert result[0].value == 310.3


@patch("whatsgoingon.sources.fred.requests.get")
def test_fetch_series_raises_after_exhausting_retries(mock_get: Mock) -> None:
    mock_get.side_effect = requests.exceptions.ConnectionError("DNS blip")

    with pytest.raises(requests.exceptions.ConnectionError):
        fred.fetch_series("CPIAUCSL", "cpi", api_key="fake-key")

    assert mock_get.call_count == 3
