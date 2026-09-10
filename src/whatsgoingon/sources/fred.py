from __future__ import annotations

import requests

from whatsgoingon.db import Observation
from whatsgoingon.retry import retry

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# FRED's marker for a missing observation value.
MISSING_VALUE = "."


def fetch_series(series_id: str, name: str, api_key: str) -> list[Observation]:
    """Fetch all observations for a FRED series and map them to Observation rows."""

    def _fetch() -> list[Observation]:
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
        }
        response = requests.get(FRED_BASE_URL, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()

        observations = []
        for row in payload.get("observations", []):
            if row["value"] == MISSING_VALUE:
                continue
            observations.append(
                Observation(
                    source="fred",
                    series_id=series_id,
                    name=name,
                    date=row["date"],
                    value=float(row["value"]),
                )
            )
        return observations

    return retry(_fetch, exceptions=(requests.RequestException,))
