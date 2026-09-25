# WhatsGoingOn

Driven by my endless curiosity to understand *WhatsGoingOn* in the world, this project
tracks a handful of US macro and market indicators, works out what changed over the last
30 days, and asks Claude to write a narrative explaining what's going on.

The narrative can be written in two ways: by a **single agent** or by a
**debate between several agents** that draft, critique and edit it. Both are
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

| | Single agent | Multi-agent debate |
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
