# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Phase 1 (WhatsGoingOn, single agent)** is functionally complete: FRED + Yahoo Finance ingestion into SQLite, the hand-rolled agent loop with Chroma-backed narrative continuity, the FastAPI wrapper, Docker, structured logging, and CI are all implemented and tested. Runs locally only (`uv run whatsgoingon-api` or `docker run`) — no hosted deploy for Phase 1. The one open item is an honest write-up of the agent's limitations in the README (see TODO.md).

**Phase 2 (multi-agent orchestration)** is in progress (weeks 1-2 done): a hand-rolled orchestrator coordinating three specialized agents (Analyst, Skeptic, Editor) instead of one, in the same repo, reusing the Phase 1 data/RAG layer as-is. See TODO.md for the week-by-week plan — this section stays intentionally brief so it doesn't drift out of sync with it.

## Commands

The project uses `uv` for environment/dependency management (run these from PowerShell; `uv`'s stdout ends up on stderr through the Bash tool's wrapper, but exit codes and output are otherwise normal):

- `uv sync` — install/update the environment from `pyproject.toml` / `uv.lock`.
- `uv add <pkg>` / `uv add --dev <pkg>` — add a runtime/dev dependency.
- `uv run pytest -q` — run the test suite. For a single file: `uv run pytest -q tests/test_fred.py`; for a single test: `uv run pytest -q tests/test_fred.py::test_fetch_series_maps_observations`.
- `uv run ruff check .` (`--fix` to auto-fix) — lint.
- `uv run whatsgoingon --source {fred,yahoo,all}` (or `uv run python -m whatsgoingon.ingest --source ...`) — run the ingestion CLI. Defaults to `all`.
- `uv run whatsgoingon-agent [--month YYYY-MM]` — run one cycle of the Phase 1 (single-agent) narrative pipeline against locally ingested data. Defaults to the current month.
- `uv run whatsgoingon-debate [--month YYYY-MM] [--max-rounds N] [--budget-usd X] [--no-context]` — run one Phase 2 Analyst → Skeptic → Editor debate cycle, with the Context agent researching news in parallel with the first draft (`--no-context` skips it). Prints the readable transcript (context brief, drafts, critiques, final, changelog, fallbacks, per-agent tokens/cost/latency) on stdout and per-agent JSON logs on stderr, and saves it as JSON to `data/debates/<month>.json` (what the API's `/debate` endpoints serve). `--budget-usd` overrides the cycle cost cap (`0` disables it); a tiny value is the quickest way to see the fallbacks fire. Doesn't write to the NarrativeStore yet, so it never overwrites the Phase 1 narrative for a month.
- `uv run whatsgoingon-api` (or `uv run uvicorn whatsgoingon.api:app --reload`) — serve the FastAPI wrapper on `:8000` (`GET /health`, `GET /state`, `POST /refresh?month=YYYY-MM`, plus Phase 2's `POST /debate?month=YYYY-MM`, `GET /debate`, `GET /debate/{month}`).
- `docker build -t whatsgoingon .` / `docker run -p 8000:8000 -v wgo_data:/app/data --env-file .env whatsgoingon` — build and run the API in a container; the volume persists the SQLite DB and Chroma index across restarts.

**Testing convention:** data-layer tests mock external API calls (FRED, Yahoo) and validate schema; agent/API tests mock the Anthropic client and `NarrativeStore`/`run_cycle` respectively — don't hit live APIs or the network from tests. Same convention applies to Phase 2: mock each agent (Analyst/Skeptic/Editor) separately and verify the orchestration logic itself, not just the final output.

Requires a `.env` with `FRED_API_KEY` (get a free key at https://fred.stlouisfed.org/docs/api/api_key.html) to run FRED ingestion, and `ANTHROPIC_API_KEY` to run the agent or API. Yahoo Finance ingestion needs no key. Without `FRED_API_KEY` set, `ingest_fred()` logs a warning and skips rather than failing. Other optional vars: `ANTHROPIC_WORKSPACE_ID` (only if your Anthropic key is org-wide), `WGO_AGENT_MODEL` (default `claude-haiku-4-5`), `WGO_ANALYST_MODEL` / `WGO_SKEPTIC_MODEL` / `WGO_EDITOR_MODEL` / `WGO_CONTEXT_MODEL` (per-role Phase 2 models, each defaulting to `WGO_AGENT_MODEL`), `WGO_CYCLE_BUDGET_USD` (default `1.00`) / `WGO_ANALYST_BUDGET_TOKENS` (`200000`) / `WGO_SKEPTIC_BUDGET_TOKENS` (`50000`) / `WGO_EDITOR_BUDGET_TOKENS` (`100000`) / `WGO_ANALYST_TIMEOUT_S` (`180`) / `WGO_SKEPTIC_TIMEOUT_S` (`60`) / `WGO_EDITOR_TIMEOUT_S` (`90`), plus `WGO_CONTEXT_BUDGET_TOKENS` (`100000`) / `WGO_CONTEXT_TIMEOUT_S` (`120`) (Phase 2 budgets and per-turn time limits — set to an empty value to disable a limit), `NEWSAPI_KEY` (enables the agent's `search_news` tool), `WGO_DB_PATH` / `WGO_CHROMA_PATH` / `WGO_DEBATES_PATH` (override default `data/` locations), `WGO_OBSIDIAN_VAULT_PATH` (enables exporting each narrative as a Markdown note into a local Obsidian vault folder after `run_cycle()`; unset means the export is a no-op).

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

Weeks 1-2 are built (orchestrator, budgets, timeouts/retries, asyncio + Context agent); this section grows as later weeks ship. Keep
`whatsgoingon/agent/` (Phase 1) untouched so both can run side by side.

- `orchestrator/state.py` — the message-passing contract: `Claim` (carries the real `SeriesDelta`s it cites), `Draft`, `Critique`, `DebateRound` (draft + critiques + the Editor's "revise" note, if any), `EditorDecision`, and `DebateState` (shared across the cycle; `deltas` computed once up front so every agent sees the same snapshot).
- `orchestrator/structured.py` — `call_structured()`: the hand-rolled tool loop for agents whose answer is structured data. `tool_choice` is `any` so every turn is a tool call, and the last iteration forces the output tool so the loop always ends with an answer. Logs every model call/tool call with token counts. Each model call goes through `_create_with_retries()`: SDK retries are off (`with_options(max_retries=0)`) and transient errors (connection/timeout, 409, 429, 5xx/529) are retried with `retry.py`, which is deadline-aware, so no retry starts past the agent's time limit and each request's HTTP timeout is the time left; the backoff honours `retry-after`/`retry-after-ms`. Research tools run on a daemon thread cut off after `TOOL_TIMEOUT_S` (30s, or the agent's remaining time if shorter) — the model gets an error result and the abandoned call finishes in the background (tools are read-only).
- `orchestrator/analyst.py` — `produce_draft()`: writes/revises the narrative with Phase 1's research tools, submits claims naming tracked series; the code maps names to `SeriesDelta`s (untracked names are dropped and logged, so a claim can't cite a number that isn't in `state.deltas`).
- `orchestrator/context.py` — `gather_context()`: fourth, optional agent. Read-only news/history tools (not `fetch_fred_series`, which writes to SQLite) → `ContextBrief` of events tied to tracked series. Runs in parallel with the Analyst's first draft; the brief goes to the Skeptic and to Analyst revisions.
- `orchestrator/skeptic.py` — `critique_draft()`: no tools, judges each claim against its attached data (and the Context brief, if any); critiques target claim ids, severity low/medium/high.
- `orchestrator/editor.py` — `decide()`: publish (final narrative + changelog) or revise. On the last round "revise" is removed from the tool schema, so it must publish.
- `orchestrator/budget.py` — `CycleBudget`: per-agent token caps + a per-cycle USD cap (costed from a per-model price table; unknown models priced at the highest rate), checked by `call_structured()` before every model call, which raises `BudgetExceeded`. Prices live in `whatsgoingon/pricing.py`, shared with Phase 1's `run_agent()`, which logs its own tokens/cost/latency at the end (the single-agent baseline). Also tracks per-agent latency: `record()` adds each model call's time (`model_latency_s`), the orchestrator's `_attempt()` adds each turn's wall-clock (`latency_s`, failed turns included); the cycle's own wall-clock is `DebateState.elapsed_s`. Overshoot is at most the one call that crossed the limit. Also per-agent wall-clock limits (`agent_timeout_s`), counted per invocation: `for_agent()` starts the clock, `AgentTimeout` when it runs out. Both errors subclass `AgentUnavailable`.
- `orchestrator/orchestrator.py` — `async run_debate_cycle()`: agents stay synchronous and run on worker threads (`asyncio.to_thread`); the only real overlap is round 1, where Analyst and Context run under `asyncio.gather` (so `CycleBudget` is lock-protected). At most `max_rounds` Analyst → Skeptic passes; high-severity critiques skip the Editor and go straight back to the Analyst while rounds remain; the last round always publishes. On `AGENT_FAILURES` (`AgentUnavailable` — budget/timeout — or an `anthropic.APIError` that outlasted the retries; other exceptions are bugs and still crash) it degrades explicitly, recording a `Fallback` on `DebateState.fallbacks` (shown to the Editor and in the transcript): Analyst in round 1 → error (nothing to publish); Analyst later → previous draft; Skeptic → draft goes unreviewed (`DebateRound.review_skipped`); Editor → latest draft published unedited; Context → carry on without the brief. After an Analyst/Skeptic fallback the next Editor pass must publish (a Context fallback doesn't end the debate).
- `orchestrator/run.py` — `run_debate()` (sync entry point, owns the event loop via `asyncio.run`) + `whatsgoingon-debate` CLI.
- `orchestrator/transcript.py` — `transcript_to_dict()` (JSON version of a `DebateState`: deltas, context, rounds, final + changelog, fallbacks, spend) and a one-file-per-month JSON store under `DEBATES_PATH` (`save_transcript()` / `load_transcript()` / `load_latest_transcript()`; months are validated as `YYYY-MM` since they become file names).
- Logging: `logging_config.agent_logger()` stamps `agent`/`month`/`round` on every record, so the JSON logs can be filtered by who said what.

## API layer architecture

- `whatsgoingon/api.py` — FastAPI app: `GET /health`, `GET /state` (latest narrative via `NarrativeStore.get_latest_narrative()`, 404 if none yet), `POST /refresh?month=YYYY-MM` (runs `run_cycle()` synchronously — no task queue — and maps its `RuntimeError`s to `503`), `POST /debate` (runs `run_debate()` synchronously, saves and returns the full transcript; setup errors and an Analyst failure before the first draft → `503`), `GET /debate` / `GET /debate/{month}` (saved transcripts, 404 if none). The demo page at `/` has a single agent / multi-agent debate switch (remembered in `localStorage`) and a time-based progress bar while a run is in flight (the endpoints don't stream progress, so it eases towards 95% around each mode's typical duration). Entry point (`whatsgoingon-api`) runs it with `uvicorn`.
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
