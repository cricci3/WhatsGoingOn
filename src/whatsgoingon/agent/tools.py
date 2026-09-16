from __future__ import annotations

from collections.abc import Callable
from typing import Any

import requests
import yfinance as yf

from whatsgoingon.config import FRED_API_KEY, NEWSAPI_KEY, YAHOO_TICKERS
from whatsgoingon.db import get_connection, upsert_observations
from whatsgoingon.sources import fred

NEWSAPI_URL = "https://newsapi.org/v2/everything"
YAHOO_TICKER_BY_NAME = {name: ticker for ticker, name in YAHOO_TICKERS.items()}

QUERY_OBSERVATIONS_TOOL = {
    "name": "query_observations",
    "description": (
        "Query locally stored time series observations (FRED macro series or Yahoo Finance "
        "market data) already ingested into SQLite. Use this to look at more history for a "
        "series than the initial summary gave you."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "series_name": {
                "type": "string",
                "description": "Friendly series name, e.g. 'cpi', 'fed_funds_rate', 'sp500', 'wti_oil'.",
            },
            "start_date": {
                "type": "string",
                "description": "ISO date (YYYY-MM-DD), inclusive lower bound. Omit for no lower bound.",
            },
            "end_date": {
                "type": "string",
                "description": "ISO date (YYYY-MM-DD), inclusive upper bound. Omit for no upper bound.",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows to return, most recent first. Default 30.",
            },
        },
        "required": ["series_name"],
        "additionalProperties": False,
    },
}

FETCH_FRED_SERIES_TOOL = {
    "name": "fetch_fred_series",
    "description": (
        "Fetch a FRED series that is not already tracked locally (e.g. a narrower sub-component "
        "like the energy index within CPI) and store it for this and future runs. Use a real "
        "FRED series_id (e.g. 'CPIENGSL' for CPI energy)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "series_id": {"type": "string", "description": "FRED series ID, e.g. 'CPIENGSL'."},
            "name": {
                "type": "string",
                "description": "Short friendly name to store this series under, e.g. 'cpi_energy'.",
            },
        },
        "required": ["series_id", "name"],
        "additionalProperties": False,
    },
}

GET_TICKER_NEWS_TOOL = {
    "name": "get_ticker_news",
    "description": (
        "Fetch recent news headlines for one of the tracked Yahoo Finance tickers (e.g. 'sp500', "
        "'vix', 'gold', 'crude_oil_futures', 'dollar_index') straight from Yahoo Finance. Use this "
        "for context on a notable move in a market series. No API key required."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "series_name": {
                "type": "string",
                "description": "Friendly name of a tracked Yahoo Finance ticker, e.g. 'sp500', 'vix'.",
            },
            "count": {
                "type": "integer",
                "description": "Max number of articles to return. Default 5.",
            },
        },
        "required": ["series_name"],
        "additionalProperties": False,
    },
}

SEARCH_NEWS_TOOL = {
    "name": "search_news",
    "description": (
        "Search recent news headlines for context on a macro/market topic "
        "(e.g. 'Federal Reserve interest rate decision'). Uses NewsAPI's free tier."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def query_observations(
    series_name: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    sql = "SELECT date, value FROM observations WHERE name = ?"
    params: list[Any] = [series_name]
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date)
    sql += " ORDER BY date DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"date": row_date, "value": row_value} for row_date, row_value in rows]


def fetch_fred_series(series_id: str, name: str) -> dict[str, Any]:
    if not FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY not configured; cannot fetch additional FRED series")

    observations = fred.fetch_series(series_id, name, FRED_API_KEY)
    with get_connection() as conn:
        stored = upsert_observations(conn, observations)

    if not observations:
        return {"series_id": series_id, "name": name, "observations_stored": 0}

    latest = observations[-1]
    return {
        "series_id": series_id,
        "name": name,
        "observations_stored": stored,
        "latest_date": latest.date,
        "latest_value": latest.value,
    }


def _article_fields(article: dict[str, Any]) -> dict[str, str]:
    # Yahoo nests most fields under "content" now, but fall back to a flat shape
    # in case an older yfinance version returns one.
    content = article.get("content", article)
    provider = content.get("provider") or {}
    canonical_url = content.get("canonicalUrl") or {}
    return {
        "title": content.get("title", ""),
        "publisher": provider.get("displayName") or content.get("publisher", ""),
        "link": canonical_url.get("url") or content.get("link", ""),
        "published_at": str(content.get("pubDate") or content.get("providerPublishTime", "")),
    }


def get_ticker_news(series_name: str, count: int = 5) -> list[dict[str, str]]:
    ticker = YAHOO_TICKER_BY_NAME.get(series_name)
    if ticker is None:
        raise ValueError(
            f"'{series_name}' is not a tracked Yahoo Finance ticker; "
            f"tracked names: {sorted(YAHOO_TICKER_BY_NAME)}"
        )

    articles = yf.Ticker(ticker).get_news(count=count)
    return [_article_fields(article) for article in articles[:count]]


def search_news(query: str) -> list[dict[str, str]]:
    if not NEWSAPI_KEY:
        raise RuntimeError("NEWSAPI_KEY not configured")

    response = requests.get(
        NEWSAPI_URL,
        params={"q": query, "sortBy": "publishedAt", "pageSize": 5, "language": "en"},
        headers={"X-Api-Key": NEWSAPI_KEY},
        timeout=30,
    )
    response.raise_for_status()
    articles = response.json().get("articles", [])
    return [
        {
            "title": article["title"],
            "source": article["source"]["name"],
            "published_at": article["publishedAt"],
        }
        for article in articles
    ]


def build_tools() -> list[dict]:
    tools = [QUERY_OBSERVATIONS_TOOL, FETCH_FRED_SERIES_TOOL, GET_TICKER_NEWS_TOOL]
    if NEWSAPI_KEY:
        tools.append(SEARCH_NEWS_TOOL)
    return tools


def build_tool_implementations() -> dict[str, Callable[..., Any]]:
    implementations: dict[str, Callable[..., Any]] = {
        "query_observations": query_observations,
        "fetch_fred_series": fetch_fred_series,
        "get_ticker_news": get_ticker_news,
    }
    if NEWSAPI_KEY:
        implementations["search_news"] = search_news
    return implementations
