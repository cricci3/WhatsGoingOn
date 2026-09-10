from __future__ import annotations

import sqlite3
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from whatsgoingon.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    source TEXT NOT NULL,
    series_id TEXT NOT NULL,
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, series_id, date)
);
"""


@dataclass(frozen=True)
class Observation:
    source: str
    series_id: str
    name: str
    date: str
    value: float


@contextmanager
def get_connection(db_path: Path | str = DB_PATH) -> Generator[sqlite3.Connection]:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_observations(conn: sqlite3.Connection, observations: Iterable[Observation]) -> int:
    fetched_at = datetime.now(UTC).isoformat()
    rows = [(o.source, o.series_id, o.name, o.date, o.value, fetched_at) for o in observations]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO observations (source, series_id, name, date, value, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, series_id, date) DO UPDATE SET
            value = excluded.value,
            fetched_at = excluded.fetched_at
        """,
        rows,
    )
    return len(rows)
