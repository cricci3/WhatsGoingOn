from unittest.mock import Mock, patch

import pandas as pd
import pytest

from whatsgoingon.sources import yahoo


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("whatsgoingon.retry.time.sleep", lambda _seconds: None)


@patch("whatsgoingon.sources.yahoo.yf.Ticker")
def test_fetch_series_maps_close_prices(mock_ticker_cls: Mock) -> None:
    index = pd.to_datetime(["2026-01-02", "2026-01-05"])
    history = pd.DataFrame({"Close": [4750.5, 4761.2]}, index=index)
    mock_ticker_cls.return_value.history.return_value = history

    result = yahoo.fetch_series("^GSPC", "sp500")

    assert len(result) == 2
    assert result[0].source == "yahoo"
    assert result[0].series_id == "^GSPC"
    assert result[0].name == "sp500"
    assert result[0].date == "2026-01-02"
    assert result[0].value == 4750.5


@patch("whatsgoingon.sources.yahoo.yf.Ticker")
def test_fetch_series_skips_nan_rows(mock_ticker_cls: Mock) -> None:
    index = pd.to_datetime(["2026-01-02", "2026-01-05"])
    history = pd.DataFrame({"Close": [float("nan"), 4761.2]}, index=index)
    mock_ticker_cls.return_value.history.return_value = history

    result = yahoo.fetch_series("^GSPC", "sp500")

    assert len(result) == 1
    assert result[0].date == "2026-01-05"


@patch("whatsgoingon.sources.yahoo.yf.Ticker")
def test_fetch_series_retries_on_empty_history_then_succeeds(mock_ticker_cls: Mock) -> None:
    index = pd.to_datetime(["2026-01-02"])
    filled = pd.DataFrame({"Close": [4750.5]}, index=index)
    mock_ticker_cls.return_value.history.side_effect = [pd.DataFrame({"Close": []}), filled]

    result = yahoo.fetch_series("^GSPC", "sp500")

    assert mock_ticker_cls.return_value.history.call_count == 2
    assert len(result) == 1
    assert result[0].value == 4750.5


@patch("whatsgoingon.sources.yahoo.yf.Ticker")
def test_fetch_series_returns_empty_after_exhausting_retries(mock_ticker_cls: Mock) -> None:
    mock_ticker_cls.return_value.history.return_value = pd.DataFrame({"Close": []})

    result = yahoo.fetch_series("^GSPC", "sp500")

    assert mock_ticker_cls.return_value.history.call_count == 3
    assert result == []
