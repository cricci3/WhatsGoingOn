# WhatsGoingOn

Driven by my endless curiosity to understand *WhatsGoingOn* in the world, this project
tracks a handful of US macro and market indicators, works out what changed over the last
30 days, and asks Claude to write a narrative explaining what's going on.

The narrative can be written in two ways: by a **single agent** (Phase 1) or by a
**debate between several agents** that draft, critique and edit it (Phase 2). Both are
hand-rolled on the Anthropic SDK, with no agent framework, so the mechanics of tool
calling and coordination stay visible. Both read the same data and can run side by side.

## What it does

1. **Ingest.** Pulls FRED macro series (CPI, GDP, Fed Funds Rate, 10Y Treasury yield,
   unemployment, WTI oil) and Yahoo Finance market data (S&P 500, VIX, gold, crude oil
   futures, dollar index) into a local SQLite database.
2. **Compute the deltas.** Compares each series' latest value with its value ~30 days
   earlier. Both architectures below start from this same snapshot.
3. **Write the narrative.** Uses one of the two architectures described in the next section.
4. **Serve it.** A FastAPI app exposes both versions over HTTP. It also serves a demo page
   with a single agent / debate switch, a progress bar while the agents work, and a button
   to generate a new narrative for any month.

   ![WhatsGoingOn demo page showing a monthly narrative with its generated timestamp and a "Generate new narrative" button](docs/screenshot.png)

## Single agent vs. multi-agent debate

| | Single agent (Phase 1) | Multi-agent debate (Phase 2) |
|---|---|---|
| Who writes | One agent does research and writing | Analyst drafts, Skeptic critiques, Editor publishes or sends it back; an optional Context agent researches news |
| Output | Free-text narrative | Narrative split into tagged claims (factual / causal / correlation), each linked to the series it cites |
| Numbers | Taken from the prompt, not checked afterwards | Attached by the code: the model only names a series and the orchestrator adds the real delta, so a claim can't cite a made-up number |
| Checking | None; the narrative is published as written | The Skeptic judges each claim against its data and the news brief (unsupported claim, hasty causality, inflated confidence) |
| Loop | Tool-calling loop until the model stops asking for tools (max 8 iterations) | Up to 3 Analyst → Skeptic rounds; the last round always publishes, so the cycle always ends |
| Memory | Pulls similar past narratives from Chroma for month-to-month continuity | Not yet: each debate starts without past narratives |
| Limits | Iteration cap only | Per-agent token caps, per-cycle USD cap, per-agent time limits, deadline-aware retries |
| When something fails | An API error fails the run; hitting the iteration cap returns a placeholder | Explicit fallback, recorded and shown in the changelog (e.g. Skeptic unavailable → draft published unreviewed, and it says so) |
| Result | Stored in Chroma (served by `GET /state`), optional Obsidian export | Full transcript saved as JSON (served by `GET /debate`): drafts, critiques, final text, changelog, fallbacks, spend |
| Typical cost / time | ~$0.016 / ~20s | $0.19–0.25 / 97–131s (see [Cost](#cost-one-agent-vs-a-debate)) |

### Single agent

A single tool-calling loop (`agent/loop.py`). Claude gets the deltas and the most similar
past narratives. It decides whether a move is worth digging into, calling tools to query
stored history, fetch another FRED series or search news. Then it writes the narrative.

<details>
<summary>Example single-agent output — September 2026</summary>

> ## September 2026: Inflation Concerns Resurface as Energy Surges
>
> The market's mood shifted noticeably this month as the confluence of rising energy
> costs, higher bond yields, and a weakening dollar rattled investor confidence. The
> headline numbers tell a defensive story: equities fell 1.5%, volatility ticked up,
> and safe-haven gold rose, while the dollar index retreated 1%.
>
> The standout mover is oil. WTI crude surged 6.2% month-over-month in the spot
> price, but futures were far more dramatic—up 17.3% from mid-August to early
> September. This sharp move suggests geopolitical tension or supply disruption
> hitting markets hard. The crude price spike is the likely driver behind the
> 15-basis-point rise in the 10-year Treasury yield to 4.80%, as investors price in
> renewed inflation expectations. June-to-July CPI was effectively flat at +0.07%,
> but oil prices moving materially higher now could feed through into broader
> inflation down the road.
>
> The equity selloff appears to be a straightforward response: higher yields
> compressed valuations (7,636 for the S&P 500 on Sept 9 vs. 7,753 in early August),
> and the rising energy costs threaten both corporate margins and consumer
> purchasing power. With the Fed holding rates steady at 3.63% and unemployment
> stable at 4.1%, there's little immediate policy relief on the horizon. Instead, the
> market seems to be repricing duration and inflation risk.
>
> The dollar's weakness (down to 98.81 from 99.82) is noteworthy and somewhat
> counterintuitive against rising Treasury yields, but it may reflect expectations
> that higher commodity prices will eventually force the Fed to move differently
> than markets originally priced in—possibly toward easing if growth slows—or
> simply reflects global risk repricing where dollar-denominated commodities become
> less attractive in dollar terms.
>
> **Confidence level: High on the energy shock and its market transmission; medium
> on whether this reflects temporary disruption or persistent supply issues.** The
> macro backdrop (solid 1.95% GDP growth, contained inflation so far) suggests
> fundamentals remain intact, but tail risks around energy costs have visibly moved
> to the fore.

</details>

### Multi-agent debate

![Debate cycle flowchart: the Analyst drafts while the optional Context agent researches news in round 1; the Skeptic critiques; blocking (high-severity) critiques go straight back to the Analyst; otherwise, on the last round or after a fallback the Editor must publish, else it chooses publish or revise; the result is the final narrative plus changelog and transcript](docs/debate_cycle_flow.png)

One cycle (`orchestrator/orchestrator.py`) works on a shared `DebateState`. The deltas
are computed once, so every agent sees the same snapshot.

1. **Analyst** writes a draft with research tools and submits it as tagged claims. In
   round 1 the **Context** agent researches news at the same time (asyncio); its brief
   goes to the Skeptic and to later revisions.
2. **Skeptic** uses no tools. It critiques specific claims, each with a severity of low,
   medium or high.
3. **High-severity critiques** send the draft straight back to the Analyst while rounds
   remain, without an Editor pass.
4. Otherwise the **Editor** publishes (final text plus a changelog of what changed and
   why) or asks for another round. On the last round, or after a fallback, "revise" is
   removed from its options, so it has to publish.

## Cost: one agent vs. a debate

The same month (September 2026) and data snapshot, run twice with each architecture,
every role on Claude Haiku 4.5. Costs are estimated from token counts at list prices
(`whatsgoingon/pricing.py`):

| | Model calls | Tokens | Cost | Wall-clock |
|---|---:|---:|---:|---:|
| Single agent (run 1 / run 2) | 2 / 2 | 10.4k / 10.5k | $0.016 / $0.016 | 22s / 19s |
| Debate, 3 rounds (run 1 / run 2) | 23 / 29 | 145k / 201k | $0.19 / $0.25 | 97s / 131s |

A debate cycle costs **~12–16x** a single-agent one and takes **~5–6x** as long. Where
the debate's money goes:

- **Analyst: 56–61%.** It goes back to its research tools on every revision (11–12 tool
  calls in round 1, then 2–7 more per revision) instead of only rewriting. In both runs it
  used up all 6 of its round-1 iterations and had to be forced to submit.
- **Context agent: 23–25%.** Five or six news/history lookups ahead of the first draft.
- **Skeptic + Editor: 15–19% together.** They call no tools, so each turn is a single
  model call of about 3–7k input tokens.

Both debates used all 3 rounds: the Skeptic always found something at medium or high
severity. Running Context alongside the first draft saved ~24s per cycle (a ~30s phase
instead of ~54s back to back). Every transcript records per-agent tokens, cost and latency.
Whether the extra spend buys a better narrative is a separate question, and that write-up
is still to come.

## Code layout

- **Data** (`config.py`, `db.py`, `sources/`, `ingest.py`). One `fetch_series()` per
  source, a shared `Observation` row shape, and an idempotent upsert into a single SQLite
  table.
- **Single agent** (`agent/`). `state.py` computes the deltas (shared with the debate),
  `tools.py` holds the research tools, `loop.py` the tool-calling loop,
  `narrative_store.py` the Chroma store, and `run.py` the CLI.
- **Debate** (`orchestrator/`). `state.py` is the message-passing contract, `structured.py`
  the tool loop for structured answers (with retries and timeouts), and there is one module
  per agent (`analyst.py`, `skeptic.py`, `editor.py`, `context.py`). `budget.py` holds the
  limits, `orchestrator.py` the cycle, `transcript.py` the JSON transcripts, and `run.py`
  the CLI.
- **API** (`api.py`). The FastAPI app and the demo page. `logging_config.py` produces
  JSON logs tagged by agent, month and round.

## Status

- **Phase 1 (single agent):** done. Ingestion, agent, API, Docker, demo page and CI are
  built and tested. It runs locally only.
- **Phase 2 (debate):** orchestrator, budgets, timeouts/fallbacks, parallel Context agent,
  transcript API and cost tracking are done.
- **Still open:** a round-by-round debate view in the UI, a hosted deploy, and an honest
  write-up of when the debate actually improved the output.

## Getting started

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.13+.

```bash
uv sync
```

Create a `.env` with:

| Variable | Required for | Notes |
|---|---|---|
| `FRED_API_KEY` | FRED ingestion | free key at https://fred.stlouisfed.org/docs/api/api_key.html |
| `ANTHROPIC_API_KEY` | both architectures / API | |
| `NEWSAPI_KEY` | optional | enables the `search_news` tool |
| `WGO_OBSIDIAN_VAULT_PATH` | optional | folder to also export each single-agent narrative to as an Obsidian note |

Yahoo Finance ingestion needs no key. Without `FRED_API_KEY`, FRED ingestion is skipped
with a warning rather than failing. Models, budgets and time limits for the debate are also
set by environment variables (`WGO_*`, see `whatsgoingon/config.py`).

```bash
# Pull data into SQLite
uv run whatsgoingon --source all

# Write a narrative from the CLI
uv run whatsgoingon-agent  --month 2026-09   # single agent
uv run whatsgoingon-debate --month 2026-09   # multi-agent debate (prints the transcript)

# Or serve both over HTTP -- demo page at http://localhost:8000/
uv run whatsgoingon-api
# single agent: GET /state, POST /refresh?month=YYYY-MM
# debate:       GET /debate, GET /debate/YYYY-MM, POST /debate?month=YYYY-MM
# interactive docs at http://127.0.0.1:8000/docs

# Or run it in Docker
docker build -t whatsgoingon .
docker run -p 8000:8000 -v wgo_data:/app/data --env-file .env whatsgoingon
```
