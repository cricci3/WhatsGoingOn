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

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
# Only needed if your API key is org-wide rather than scoped to one workspace -
# the API then requires this header (Anthropic console -> Settings -> Workspaces).
ANTHROPIC_WORKSPACE_ID = os.getenv("ANTHROPIC_WORKSPACE_ID")
AGENT_MODEL = os.getenv("WGO_AGENT_MODEL", "claude-haiku-4-5")

# Optional: enables the search_news tool when set.
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")

CHROMA_PATH = Path(os.getenv("WGO_CHROMA_PATH", PROJECT_ROOT / "data" / "chroma"))

# Optional: local Obsidian vault folder to export each narrative to as a Markdown note.
# Export is a no-op when unset.
_obsidian_vault_path = os.getenv("WGO_OBSIDIAN_VAULT_PATH")
OBSIDIAN_VAULT_PATH = Path(_obsidian_vault_path) if _obsidian_vault_path else None
