from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("WGO_DB_PATH", PROJECT_ROOT / "data" / "whatsgoingon.sqlite3"))

FRED_API_KEY = os.getenv("FRED_API_KEY")

# FRED series_id -> human-readable name
FRED_SERIES: dict[str, str] = {
    "CPIAUCSL": "cpi",
    "GDP": "gdp",
    "FEDFUNDS": "fed_funds_rate",
    "DGS10": "treasury_10y",
    "UNRATE": "unemployment_rate",
    "DCOILWTICO": "wti_oil",
}

# Yahoo Finance ticker -> human-readable name
YAHOO_TICKERS: dict[str, str] = {
    "^GSPC": "sp500",
    "^VIX": "vix",
    "GC=F": "gold",
    "CL=F": "crude_oil_futures",
    "DX-Y.NYB": "dollar_index",
}
