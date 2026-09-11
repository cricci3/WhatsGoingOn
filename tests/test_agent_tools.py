from unittest.mock import Mock, patch

import pytest

from whatsgoingon.agent import tools
from whatsgoingon.db import Observation, get_connection, upsert_observations


def test_query_observations_filters_by_name_and_date_range(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "test.sqlite3"
    observations = [
        Observation(source="fred", series_id="CPIAUCSL", name="cpi", date="2026-06-01", value=305.0),
        Observation(source="fred", series_id="CPIAUCSL", name="cpi", date="2026-07-01", value=308.0),
        Observation(source="fred", series_id="CPIAUCSL", name="cpi", date="2026-08-01", value=310.0),
        Observation(source="fred", series_id="GDP", name="gdp", date="2026-08-01", value=20000.0),
    ]
    with get_connection(db_path) as conn:
        upsert_observations(conn, observations)
    monkeypatch.setattr(tools, "get_connection", lambda: get_connection(db_path))

    result = tools.query_observations("cpi", start_date="2026-07-01")

    assert result == [
        {"date": "2026-08-01", "value": 310.0},
        {"date": "2026-07-01", "value": 308.0},
    ]


def test_query_observations_respects_limit(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "test.sqlite3"
    observations = [
        Observation(source="fred", series_id="CPIAUCSL", name="cpi", date=f"2026-0{i}-01", value=float(i))
        for i in range(1, 6)
    ]
    with get_connection(db_path) as conn:
        upsert_observations(conn, observations)
    monkeypatch.setattr(tools, "get_connection", lambda: get_connection(db_path))

    result = tools.query_observations("cpi", limit=2)

    assert len(result) == 2
    assert result[0]["date"] == "2026-05-01"


@patch("whatsgoingon.agent.tools.fred.fetch_series")
def test_fetch_fred_series_stores_and_summarizes(
    mock_fetch_series: Mock, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.sqlite3"
    mock_fetch_series.return_value = [
        Observation(source="fred", series_id="CPIENGSL", name="cpi_energy", date="2026-07-01", value=200.0),
        Observation(source="fred", series_id="CPIENGSL", name="cpi_energy", date="2026-08-01", value=210.0),
    ]
    monkeypatch.setattr(tools, "FRED_API_KEY", "fake-key")
    monkeypatch.setattr(tools, "get_connection", lambda: get_connection(db_path))

    result = tools.fetch_fred_series("CPIENGSL", "cpi_energy")

    assert result == {
        "series_id": "CPIENGSL",
        "name": "cpi_energy",
        "observations_stored": 2,
        "latest_date": "2026-08-01",
        "latest_value": 210.0,
    }
    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM observations WHERE name = 'cpi_energy'").fetchone()[0]
    assert count == 2


def test_fetch_fred_series_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "FRED_API_KEY", None)

    with pytest.raises(RuntimeError):
        tools.fetch_fred_series("CPIENGSL", "cpi_energy")


@patch("whatsgoingon.agent.tools.requests.get")
def test_search_news_maps_articles(mock_get: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "NEWSAPI_KEY", "fake-key")
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {
        "articles": [
            {
                "title": "Fed holds rates steady",
                "source": {"name": "Reuters"},
                "publishedAt": "2026-08-15T12:00:00Z",
            },
        ]
    }
    mock_get.return_value = response

    result = tools.search_news("Federal Reserve")

    assert result == [
        {"title": "Fed holds rates steady", "source": "Reuters", "published_at": "2026-08-15T12:00:00Z"},
    ]


def test_search_news_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "NEWSAPI_KEY", None)

    with pytest.raises(RuntimeError):
        tools.search_news("Federal Reserve")


def test_build_tools_excludes_news_tool_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "NEWSAPI_KEY", None)

    names = {tool["name"] for tool in tools.build_tools()}

    assert names == {"query_observations", "fetch_fred_series"}
    assert "search_news" not in tools.build_tool_implementations()


def test_build_tools_includes_news_tool_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "NEWSAPI_KEY", "fake-key")

    names = {tool["name"] for tool in tools.build_tools()}

    assert names == {"query_observations", "fetch_fred_series", "search_news"}
    assert "search_news" in tools.build_tool_implementations()
