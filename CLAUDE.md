# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Phase 1 (WhatsGoingOn, single agent)** is functionally complete: FRED + Yahoo Finance ingestion into SQLite, the hand-rolled agent loop with Chroma-backed narrative continuity, the FastAPI wrapper, Docker, structured logging, and CI are all implemented and tested. Runs locally only (`uv run whatsgoingon-api` or `docker run`) — no hosted deploy for Phase 1. The one open item is an honest write-up of the agent's limitations in the README (see TODO.md).

**Phase 2 (multi-agent orchestration)** is in progress (week 1 done): a hand-rolled orchestrator coordinating three specialized agents (Analyst, Skeptic, Editor) instead of one, in the same repo, reusing the Phase 1 data/RAG layer as-is. See TODO.md for the week-by-week plan — this section stays intentionally brief so it doesn't drift out of sync with it.

## Commands

The project uses `uv` for environment/dependency management (run these from PowerShell; `uv`'s stdout ends up on stderr through the Bash tool's wrapper, but exit codes and output are otherwise normal):

- `uv sync` — install/update the environment from `pyproject.toml` / `uv.lock`.
- `uv add <pkg>` / `uv add --dev <pkg>` — add a runtime/dev dependency.
- `uv run pytest -q` — run the test suite. For a single file: `uv run pytest -q tests/test_fred.py`; for a single test: `uv run pytest -q tests/test_fred.py::test_fetch_series_maps_observations`.
- `uv run ruff check .` (`--fix` to auto-fix) — lint.
- `uv run whatsgoingon --source {fred,yahoo,all}` (or `uv run python -m whatsgoingon.ingest --source ...`) — run the ingestion CLI. Defaults to `all`.
- `uv run whatsgoingon-agent [--month YYYY-MM]` — run one cycle of the Phase 1 (single-agent) narrative pipeline against locally ingested data. Defaults to the current month.
- `uv run whatsgoingon-debate [--month YYYY-MM] [--max-rounds N]` — run one Phase 2 Analyst → Skeptic → Editor debate cycle. Prints the readable transcript (drafts, critiques, final, changelog) on stdout and per-agent JSON logs on stderr. Doesn't write to the NarrativeStore yet, so it never overwrites the Phase 1 narrative for a month.
- `uv run whatsgoingon-api` (or `uv run uvicorn whatsgoingon.api:app --reload`) — serve the FastAPI wrapper on `:8000` (`GET /health`, `GET /state`, `POST /refresh?month=YYYY-MM`).
- `docker build -t whatsgoingon .` / `docker run -p 8000:8000 -v wgo_data:/app/data --env-file .env whatsgoingon` — build and run the API in a container; the volume persists the SQLite DB and Chroma index across restarts.

**Testing convention:** data-layer tests mock external API calls (FRED, Yahoo) and validate schema; agent/API tests mock the Anthropic client and `NarrativeStore`/`run_cycle` respectively — don't hit live APIs or the network from tests. Same convention applies to Phase 2: mock each agent (Analyst/Skeptic/Editor) separately and verify the orchestration logic itself, not just the final output.

Requires a `.env` with `FRED_API_KEY` (get a free key at https://fred.stlouisfed.org/docs/api/api_key.html) to run FRED ingestion, and `ANTHROPIC_API_KEY` to run the agent or API. Yahoo Finance ingestion needs no key. Without `FRED_API_KEY` set, `ingest_fred()` logs a warning and skips rather than failing. Other optional vars: `ANTHROPIC_WORKSPACE_ID` (only if your Anthropic key is org-wide), `WGO_AGENT_MODEL` (default `claude-haiku-4-5`), `WGO_ANALYST_MODEL` / `WGO_SKEPTIC_MODEL` / `WGO_EDITOR_MODEL` (per-role Phase 2 models, each defaulting to `WGO_AGENT_MODEL`), `NEWSAPI_KEY` (enables the agent's `search_news` tool), `WGO_DB_PATH` / `WGO_CHROMA_PATH` (override default `data/` locations), `WGO_OBSIDIAN_VAULT_PATH` (enables exporting each narrative as a Markdown note into a local Obsidian vault folder after `run_cycle()`; unset means the export is a no-op).

## Data layer architecture

- `whatsgoingon/config.py` — loads `.env`, defines `DB_PATH` and the two source registries: `FRED_SERIES` (series_id → friendly name) and `YAHOO_TICKERS` (ticker → friendly name). Add a new series/ticker by adding one entry here.
- `whatsgoingon/db.py` — the `Observation` dataclass (source, series_id, name, date, value) is the common row shape both sources map into. `get_connection()` is a context manager over a single SQLite table (`observations`, keyed on `source, series_id, date`); `upsert_observations()` does an `INSERT ... ON CONFLICT DO UPDATE` so re-running ingestion is idempotent.
- `whatsgoingon/sources/fred.py` and `sources/yahoo.py` — each exposes one `fetch_series(...) -> list[Observation]` and returns `Observation` rows; neither touches the DB directly. FRED is called with `requests`; Yahoo Finance ingestion uses the `yfinance` package. `ingest.py` is the only module that wires sources → `db.py` together and is the CLI entry point (`whatsgoingon` / `python -m whatsgoingon.ingest`).

Reused as-is by both Phase 1 and Phase 2 — nothing here changes for the orchestrator work.

## Agent layer architecture (Phase 1 — single agent)

- `whatsgoingon/agent/state.py` — `compute_deltas()` compares each tracked series' latest value in SQLite to its value ~30 days ago; `format_deltas()` renders that as plain text for the model. Also reused by Phase 2's Analyst.
- `whatsgoingon/agent/tools.py` — tool schemas + implementations the agent can call (`query_observations`, `fetch_fred_series`, `get_ticker_news` (Yahoo Finance news for a tracked ticker, no key needed), and `search_news` when `NEWSAPI_KEY` is set).
- `whatsgoingon/agent/loop.py` — `run_agent()` is the hand-rolled tool-calling loop (no framework): call Claude, execute any `tool_use` blocks, feed results back, repeat until `stop_reason != "tool_use"` or `max_iterations` is hit. Logs each tool call/result as structured fields via `extra=`.
- `whatsgoingon/agent/narrative_store.py` — `NarrativeStore` wraps a local Chroma collection keyed by month: `add_narrative()` upserts, `get_similar_narratives()` does semantic search for continuity context, `get_latest_narrative()` returns the most recent month. Shared by Phase 1 and Phase 2.
- `whatsgoingon/agent/run.py` — `run_cycle()` wires deltas → similar-narrative retrieval → `run_agent()` → `store.add_narrative()`, and is the CLI entry point (`whatsgoingon-agent`). If `WGO_OBSIDIAN_VAULT_PATH` is set, it also calls `obsidian_export.export_narrative_to_vault()` afterward; failures there are logged as a warning, never raised, so the cycle's result doesn't depend on the vault being writable.
- `whatsgoingon/agent/obsidian_export.py` — `export_narrative_to_vault()` writes a narrative record as a `<month>.md` note (YAML frontmatter + body) into a local Obsidian vault folder, overwriting any existing note for that month. Frontmatter includes a `previous: [[YYYY-MM]]` wikilink when a prior month's note exists, and tags derived from `delta_summary` for any tracked series (`FRED_SERIES`/`YAHOO_TICKERS`) whose delta exceeds `SIGNIFICANT_PCT_CHANGE_THRESHOLD`, plus a fixed `#macro-narrative` tag — so months link up in Obsidian's graph view.

## Orchestrator layer architecture (Phase 2 — multi-agent, in progress)

Week 1 (sequential orchestrator) is built; this section grows as later weeks ship. Keep
`whatsgoingon/agent/` (Phase 1) untouched so both can run side by side.

- `orchestrator/state.py` — the message-passing contract: `Claim` (carries the real `SeriesDelta`s it cites), `Draft`, `Critique`, `DebateRound` (draft + critiques + the Editor's "revise" note, if any), `EditorDecision`, and `DebateState` (shared across the cycle; `deltas` computed once up front so every agent sees the same snapshot).
- `orchestrator/structured.py` — `call_structured()`: the hand-rolled tool loop for agents whose answer is structured data. `tool_choice` is `any` so every turn is a tool call, and the last iteration forces the output tool so the loop always ends with an answer. Logs every model call/tool call with token counts.
- `orchestrator/analyst.py` — `produce_draft()`: writes/revises the narrative with Phase 1's research tools, submits claims naming tracked series; the code maps names to `SeriesDelta`s (untracked names are dropped and logged, so a claim can't cite a number that isn't in `state.deltas`).
- `orchestrator/skeptic.py` — `critique_draft()`: no tools, judges each claim against its attached data; critiques target claim ids, severity low/medium/high.
- `orchestrator/editor.py` — `decide()`: publish (final narrative + changelog) or revise. On the last round "revise" is removed from the tool schema, so it must publish.
- `orchestrator/orchestrator.py` — `run_debate_cycle()`: at most `max_rounds` Analyst → Skeptic passes; high-severity critiques skip the Editor and go straight back to the Analyst while rounds remain; the last round always publishes.
- `orchestrator/run.py` — `run_debate()` + `whatsgoingon-debate` CLI.
- Logging: `logging_config.agent_logger()` stamps `agent`/`month`/`round` on every record, so the JSON logs can be filtered by who said what.

## API layer architecture

- `whatsgoingon/api.py` — FastAPI app: `GET /health`, `GET /state` (latest narrative via `NarrativeStore.get_latest_narrative()`, 404 if none yet), `POST /refresh?month=YYYY-MM` (runs `run_cycle()` synchronously — no task queue — and maps its `RuntimeError`s to `503`). Entry point (`whatsgoingon-api`) runs it with `uvicorn`. Phase 2 will add an endpoint that also exposes the debate transcript (draft/critique/final/changelog), not just the final narrative.
- `whatsgoingon/logging_config.py` — `configure_logging()` installs a JSON log formatter (used by the API on startup) so the agent's structured tool-call logs stay grep/parseable in production.
- `Dockerfile` — single-stage image built with `uv`; `/app/data` is a volume (SQLite DB + Chroma index persist there). Phase 1: local use only. Phase 2 adds a real hosted deploy (Fly.io/Render) — see TODO.md.

## Roadmap

TODO.md (Italian) is the source of truth for scope and sequencing — reread the relevant section before starting a new phase; details here are intentionally minimal so they don't drift out of sync. README.md is the English-language project overview for outside readers, not the planning doc.

**Phase 1**
1. **Data layer** — FRED + Yahoo macro series into SQLite. *(done)*
2. **Agent loop** — hand-rolled (no agent framework) tool-calling loop comparing current state vs. 30 days ago; minimal RAG via a local vector store (e.g. Chroma) for month-over-month coherence. *(done)*
3. **API** — FastAPI wrapper + Docker, run locally only (no hosted deploy). *(done)*
4. **Polish** — CI, a minimal demo UI, an honest write-up of limitations. *(CI done; limitations write-up open)*

**Phase 2**
5. **Orchestration** — hand-rolled multi-agent orchestrator (Analyst/Skeptic/Editor), concurrency, per-agent budget/cost control, hosted deploy + CD, demo UI showing the debate. *(week 1 done — see TODO.md)*

## Working conventions

- Prefer the simplest infrastructure that satisfies the current phase's goal (SQLite over Timescale, local Chroma over a hosted vector DB) — this project is deliberately scoped to avoid infra complexity that isn't earning its keep for a learning exercise. Phase 1 stayed local-only for the same reason; Phase 2 deliberately adds a real hosted deploy because that's now the thing being learned.
- The agent loop, and the Phase 2 orchestrator, are meant to be hand-rolled (no LangChain/CrewAI/agent framework) so the mechanics of tool calling, state management, and multi-agent coordination stay visible.
- Use git deliberately even solo — structured commits, branches, PRs — the README calls this out explicitly as a habit to build, not a formality.
