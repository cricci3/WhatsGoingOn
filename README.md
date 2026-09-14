# WhatsGoingOn

A small hobby project that watches a handful of US macro/market indicators, figures out
what changed over the last 30 days, and asks Claude to write a plain-English narrative
explaining what's going on — with a hand-rolled tool-calling loop (no agent framework)
and a local vector store so each month's narrative stays consistent with the last.

It's a learning project, not a product: the goal is to build a real (if small) data
pipeline, agent loop, RAG layer, API, and container end-to-end, and to be honest about
where a hobby-scale setup cuts corners a production system wouldn't.

## What it does

1. **Ingest** — pulls a handful of FRED macro series (CPI, GDP, Fed Funds Rate, 10Y
   Treasury yield, unemployment, WTI oil) and Yahoo Finance market data (S&P 500, VIX,
   gold, crude oil futures, dollar index) into a local SQLite database.
2. **Agent** — compares the latest value of each series to ~30 days ago, decides (via
   Claude tool calling) whether anything is worth digging into further — a bigger CPI
   sub-component, more history on one series, a news headline — then writes a short
   narrative. Past narratives are stored in a local Chroma collection and the most
   similar ones are pulled back in as context, so the agent doesn't contradict what it
   said last month.
3. **API** — a thin FastAPI wrapper (`GET /state`, `POST /refresh`) around the agent, so
   it can be triggered and read over HTTP instead of only the CLI.

Everything runs locally (`uv run ...` or `docker run`) — there's no hosted deployment.

## Architecture

- **Data layer** (`whatsgoingon/config.py`, `db.py`, `sources/`) — `sources/fred.py` and
  `sources/yahoo.py` each expose a `fetch_series(...) -> list[Observation]`; `db.py`
  holds the shared `Observation` shape and a single SQLite table with an idempotent
  upsert; `ingest.py` wires the two together and is the CLI entry point.
- **Agent layer** (`whatsgoingon/agent/`) — `state.py` computes the 30-day deltas;
  `tools.py` defines the tools the agent can call (query stored observations, fetch an
  additional FRED series, optionally search news); `loop.py` is the hand-rolled
  tool-calling loop; `narrative_store.py` wraps the Chroma collection used for
  month-over-month continuity; `run.py` wires it all together and is the CLI entry
  point.
- **API layer** (`whatsgoingon/api.py`) — FastAPI app exposing the agent over HTTP, plus
  `logging_config.py` for structured (JSON) logs of the agent's tool-calling decisions.

See [`CLAUDE.md`](CLAUDE.md) for a more detailed file-by-file breakdown, and
[`TODO.md`](TODO.md) for the original week-by-week plan (in Italian) and what's still
open.

## Status

Data ingestion, the agent loop, and the FastAPI wrapper + Dockerfile are built and
tested. CI, a demo UI, and an honest write-up of the agent's limitations are not done
yet — see [`TODO.md`](TODO.md).

## Getting started

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.13+.

```bash
uv sync
cp .env.example .env   # then fill in the keys below
```

Environment variables (`.env`):

| Variable | Required for | Notes |
|---|---|---|
| `FRED_API_KEY` | FRED ingestion | free key at https://fred.stlouisfed.org/docs/api/api_key.html |
| `ANTHROPIC_API_KEY` | the agent / API | |
| `ANTHROPIC_WORKSPACE_ID` | — | only if your Anthropic key is org-wide rather than workspace-scoped |
| `WGO_AGENT_MODEL` | — | defaults to `claude-haiku-4-5` |
| `NEWSAPI_KEY` | — | enables the agent's `search_news` tool |
| `WGO_DB_PATH`, `WGO_CHROMA_PATH` | — | override the default `data/` locations |

Yahoo Finance ingestion needs no key. Without `FRED_API_KEY`, FRED ingestion is skipped
with a warning rather than failing.

```bash
# Pull data into SQLite
uv run whatsgoingon --source all

# Run one agent cycle from the CLI
uv run whatsgoingon-agent

# Or serve it over HTTP
uv run whatsgoingon-api
# then: GET /health, GET /state, POST /refresh?month=YYYY-MM
# interactive docs at http://127.0.0.1:8000/docs

# Or run it in Docker
docker build -t whatsgoingon .
docker run -p 8000:8000 -v wgo_data:/app/data --env-file .env whatsgoingon
```

## Testing

```bash
uv run pytest -q
uv run ruff check .
```

Tests mock external calls (FRED, Yahoo, the Anthropic client) — nothing hits a live API
or the network.
