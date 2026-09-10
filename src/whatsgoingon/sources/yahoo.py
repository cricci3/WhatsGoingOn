from __future__ import annotations

import math

import yfinance as yf

from whatsgoingon.db import Observation
from whatsgoingon.retry import retry


def fetch_series(ticker: str, name: str, *, period: str = "5y") -> list[Observation]:
    """Fetch daily close prices for a Yahoo Finance ticker and map them to Observation rows.

    yfinance swallows transient failures internally (a flaky cookie/crumb handshake, a rate
    limit) and just returns an empty DataFrame instead of raising, so retry on emptiness rather
    than on exceptions.
    """
    history = retry(lambda: yf.Ticker(ticker).history(period=period), is_success=lambda df: not df.empty)

    observations = []
    for date, row in history.iterrows():
        close = float(row["Close"])
        if math.isnan(close):
            continue
        observations.append(
            Observation(
                source="yahoo",
                series_id=ticker,
                name=name,
                date=date.strftime("%Y-%m-%d"),
                value=close,
            )
        )
    return observations
