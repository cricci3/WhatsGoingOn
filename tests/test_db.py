from whatsgoingon.db import Observation, get_connection, upsert_observations


def test_upsert_inserts_new_rows() -> None:
    observations = [
        Observation(source="fred", series_id="CPIAUCSL", name="cpi", date="2026-01-01", value=310.3),
        Observation(source="fred", series_id="CPIAUCSL", name="cpi", date="2026-02-01", value=311.1),
    ]

    with get_connection(":memory:") as conn:
        inserted = upsert_observations(conn, observations)
        rows = conn.execute("SELECT date, value FROM observations ORDER BY date").fetchall()

    assert inserted == 2
    assert rows == [("2026-01-01", 310.3), ("2026-02-01", 311.1)]


def test_upsert_updates_existing_row_on_conflict() -> None:
    original = Observation(source="fred", series_id="CPIAUCSL", name="cpi", date="2026-01-01", value=310.3)
    revised = Observation(source="fred", series_id="CPIAUCSL", name="cpi", date="2026-01-01", value=310.9)

    with get_connection(":memory:") as conn:
        upsert_observations(conn, [original])
        upsert_observations(conn, [revised])
        rows = conn.execute("SELECT value FROM observations").fetchall()

    assert rows == [(310.9,)]


def test_upsert_empty_list_is_a_noop() -> None:
    with get_connection(":memory:") as conn:
        inserted = upsert_observations(conn, [])
        count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

    assert inserted == 0
    assert count == 0
