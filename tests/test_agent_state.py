import pytest

from whatsgoingon.agent.state import compute_deltas, format_deltas
from whatsgoingon.config import FRED_SERIES
from whatsgoingon.db import Observation, get_connection, upsert_observations

CPI_NAME = FRED_SERIES["CPIAUCSL"]


def test_compute_deltas_finds_reference_on_or_before_lookback_window(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    observations = [
        Observation(source="fred", series_id="CPIAUCSL", name=CPI_NAME, date="2026-07-01", value=300.0),
        Observation(source="fred", series_id="CPIAUCSL", name=CPI_NAME, date="2026-08-01", value=310.0),
    ]
    with get_connection(db_path) as conn:
        upsert_observations(conn, observations)

    deltas = compute_deltas(lookback_days=30, db_path=db_path)

    delta = next(d for d in deltas if d.name == CPI_NAME)
    assert delta.latest_date == "2026-08-01"
    assert delta.latest_value == 310.0
    assert delta.reference_date == "2026-07-01"
    assert delta.reference_value == 300.0
    assert delta.change == 10.0
    assert delta.pct_change == pytest.approx((10.0 / 300.0) * 100)


def test_compute_deltas_skips_series_without_enough_history(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    observations = [
        Observation(source="fred", series_id="CPIAUCSL", name=CPI_NAME, date="2026-08-01", value=310.0),
    ]
    with get_connection(db_path) as conn:
        upsert_observations(conn, observations)

    deltas = compute_deltas(db_path=db_path)

    assert deltas == []


def test_format_deltas_renders_readable_lines(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    observations = [
        Observation(source="fred", series_id="CPIAUCSL", name=CPI_NAME, date="2026-07-01", value=300.0),
        Observation(source="fred", series_id="CPIAUCSL", name=CPI_NAME, date="2026-08-01", value=310.0),
    ]
    with get_connection(db_path) as conn:
        upsert_observations(conn, observations)

    deltas = compute_deltas(db_path=db_path)
    text = format_deltas(deltas)

    assert CPI_NAME in text
    assert "300.00" in text
    assert "310.00" in text
