from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from whatsgoingon.config import DB_PATH, FRED_SERIES, YAHOO_TICKERS
from whatsgoingon.db import get_connection


@dataclass(frozen=True)
class SeriesDelta:
    name: str
    latest_date: str
    latest_value: float
    reference_date: str
    reference_value: float

    @property
    def change(self) -> float:
        return self.latest_value - self.reference_value

    @property
    def pct_change(self) -> float | None:
        if self.reference_value == 0:
            return None
        return (self.change / self.reference_value) * 100


def compute_deltas(*, lookback_days: int = 30, db_path: Path | str = DB_PATH) -> list[SeriesDelta]:
    """Compare the latest observation of each tracked series to its value ~lookback_days ago."""
    names = [*FRED_SERIES.values(), *YAHOO_TICKERS.values()]
    deltas = []
    with get_connection(db_path) as conn:
        for name in names:
            latest = conn.execute(
                "SELECT date, value FROM observations WHERE name = ? ORDER BY date DESC LIMIT 1",
                (name,),
            ).fetchone()
            if latest is None:
                continue
            latest_date, latest_value = latest

            reference_target = (date.fromisoformat(latest_date) - timedelta(days=lookback_days)).isoformat()
            reference = conn.execute(
                """
                SELECT date, value FROM observations
                WHERE name = ? AND date <= ?
                ORDER BY date DESC LIMIT 1
                """,
                (name, reference_target),
            ).fetchone()
            if reference is None:
                continue
            reference_date, reference_value = reference

            deltas.append(
                SeriesDelta(
                    name=name,
                    latest_date=latest_date,
                    latest_value=latest_value,
                    reference_date=reference_date,
                    reference_value=reference_value,
                )
            )
    return deltas


def format_deltas(deltas: list[SeriesDelta]) -> str:
    lines = []
    for d in deltas:
        pct = d.pct_change
        pct_str = f"{pct:+.2f}%" if pct is not None else "n/a"
        lines.append(
            f"- {d.name}: {d.reference_value:.2f} ({d.reference_date}) -> "
            f"{d.latest_value:.2f} ({d.latest_date}), change {d.change:+.2f} ({pct_str})"
        )
    return "\n".join(lines)
