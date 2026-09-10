from __future__ import annotations

import argparse
import logging

from whatsgoingon.config import FRED_API_KEY, FRED_SERIES, YAHOO_TICKERS
from whatsgoingon.db import get_connection, upsert_observations
from whatsgoingon.sources import fred, yahoo

logger = logging.getLogger(__name__)


def ingest_fred() -> int:
    if not FRED_API_KEY:
        logger.warning("FRED_API_KEY not set; skipping FRED ingestion")
        return 0

    total = 0
    with get_connection() as conn:
        for series_id, name in FRED_SERIES.items():
            observations = fred.fetch_series(series_id, name, FRED_API_KEY)
            total += upsert_observations(conn, observations)
            logger.info("FRED %s (%s): %d observations", series_id, name, len(observations))
    return total


def ingest_yahoo() -> int:
    total = 0
    with get_connection() as conn:
        for ticker, name in YAHOO_TICKERS.items():
            observations = yahoo.fetch_series(ticker, name)
            total += upsert_observations(conn, observations)
            logger.info("Yahoo %s (%s): %d observations", ticker, name, len(observations))
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch macro/market data and store it in SQLite")
    parser.add_argument("--source", choices=["fred", "yahoo", "all"], default="all")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    total = 0
    if args.source in ("fred", "all"):
        total += ingest_fred()
    if args.source in ("yahoo", "all"):
        total += ingest_yahoo()

    logger.info("Done: %d observations upserted", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
