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
# Phase 2 orchestrator: one model per role, each defaulting to the Phase 1 agent's model.
ANALYST_MODEL = os.getenv("WGO_ANALYST_MODEL", AGENT_MODEL)
SKEPTIC_MODEL = os.getenv("WGO_SKEPTIC_MODEL", AGENT_MODEL)
EDITOR_MODEL = os.getenv("WGO_EDITOR_MODEL", AGENT_MODEL)
CONTEXT_MODEL = os.getenv("WGO_CONTEXT_MODEL", AGENT_MODEL)


def _optional_number[T: (int, float)](name: str, default: T, cast: type[T]) -> T | None:
    """Env var as a number; unset means `default`, set-but-empty means no limit."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return cast(raw) if raw.strip() else None


# Phase 2 budgets: generous guards against runaway loops, not yet calibrated on real runs
# (that's week 3's cost logging). An empty value disables a limit.
CYCLE_BUDGET_USD = _optional_number("WGO_CYCLE_BUDGET_USD", 1.00, float)
AGENT_BUDGET_TOKENS: dict[str, int | None] = {
    "analyst": _optional_number("WGO_ANALYST_BUDGET_TOKENS", 200_000, int),
    "skeptic": _optional_number("WGO_SKEPTIC_BUDGET_TOKENS", 50_000, int),
    "editor": _optional_number("WGO_EDITOR_BUDGET_TOKENS", 100_000, int),
    "context": _optional_number("WGO_CONTEXT_BUDGET_TOKENS", 100_000, int),
}
# Wall-clock seconds per agent turn (one role, one round), including retries. The Analyst gets
# the most because it may run several research-tool iterations.
AGENT_TIMEOUT_S: dict[str, float | None] = {
    "analyst": _optional_number("WGO_ANALYST_TIMEOUT_S", 180.0, float),
    "skeptic": _optional_number("WGO_SKEPTIC_TIMEOUT_S", 60.0, float),
    "editor": _optional_number("WGO_EDITOR_TIMEOUT_S", 90.0, float),
    "context": _optional_number("WGO_CONTEXT_TIMEOUT_S", 120.0, float),
}

# Optional: enables the search_news tool when set.
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")

CHROMA_PATH = Path(os.getenv("WGO_CHROMA_PATH", PROJECT_ROOT / "data" / "chroma"))

# Optional: local Obsidian vault folder to export each narrative to as a Markdown note.
# Export is a no-op when unset.
_obsidian_vault_path = os.getenv("WGO_OBSIDIAN_VAULT_PATH")
OBSIDIAN_VAULT_PATH = Path(_obsidian_vault_path) if _obsidian_vault_path else None
