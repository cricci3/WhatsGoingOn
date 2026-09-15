from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from whatsgoingon.agent.narrative_store import NarrativeStore
from whatsgoingon.agent.run import run_cycle
from whatsgoingon.logging_config import configure_logging

logger = logging.getLogger(__name__)

_INDEX_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>WhatsGoingOn</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem; }
  body { color: #1a1a1a; }
  h1 { font-size: 1.4rem; }
  #meta { color: #666; font-size: 0.9rem; margin-bottom: 1rem; }
  #narrative { white-space: pre-wrap; line-height: 1.5; }
  #message { color: #a33; margin-top: 1rem; }
  .controls { margin: 1rem 0; display: flex; gap: 0.5rem; align-items: center; }
  button { cursor: pointer; }
  button:disabled { cursor: default; opacity: 0.6; }
</style>
</head>
<body>
<h1>WhatsGoingOn</h1>
<div class="controls">
  <input type="month" id="month">
  <button id="refresh">Refresh</button>
</div>
<div id="meta"></div>
<div id="narrative">Loading...</div>
<div id="message"></div>
<script>
  const monthInput = document.getElementById("month");
  const refreshBtn = document.getElementById("refresh");
  const metaEl = document.getElementById("meta");
  const narrativeEl = document.getElementById("narrative");
  const messageEl = document.getElementById("message");

  monthInput.value = new Date().toISOString().slice(0, 7);

  function showNarrative(data) {
    metaEl.textContent = "Month: " + data.month;
    narrativeEl.textContent = data.narrative;
    messageEl.textContent = "";
  }

  async function loadState() {
    const res = await fetch("/state");
    if (res.status === 404) {
      narrativeEl.textContent = "";
      messageEl.textContent = "No narrative yet — run Refresh.";
      return;
    }
    showNarrative(await res.json());
  }

  refreshBtn.addEventListener("click", async () => {
    refreshBtn.disabled = true;
    refreshBtn.textContent = "Refreshing…";
    messageEl.textContent = "";
    try {
      const res = await fetch("/refresh?month=" + encodeURIComponent(monthInput.value), { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        messageEl.textContent = data.detail || "Refresh failed.";
      } else {
        showNarrative(data);
      }
    } catch (err) {
      messageEl.textContent = "Refresh failed: " + err;
    } finally {
      refreshBtn.disabled = false;
      refreshBtn.textContent = "Refresh";
    }
  });

  loadState();
</script>
</body>
</html>
"""


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    configure_logging()
    yield


app = FastAPI(title="WhatsGoingOn", description="Monthly macro/markets narrative agent", lifespan=_lifespan)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Minimal demo page: shows the latest narrative and a Refresh button that
    triggers a new cycle for a chosen month."""
    return _INDEX_HTML


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/state")
def state() -> dict[str, str | None]:
    """Return the most recent narrative the agent has produced."""
    latest = NarrativeStore().get_latest_narrative()
    if latest is None:
        raise HTTPException(status_code=404, detail="No narrative has been generated yet")
    return latest


@app.post("/refresh")
def refresh(month: str | None = None) -> dict[str, str | None]:
    """Trigger a new agent cycle (fetches deltas, calls tools as needed, writes a new
    narrative) and return it. Synchronous: this can take from several seconds up to
    roughly the iteration budget's worth of Claude API calls."""
    month = month or date.today().strftime("%Y-%m")
    try:
        narrative = run_cycle(month=month)
    except RuntimeError as exc:
        logger.warning("refresh failed", extra={"month": month, "error": str(exc)})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"month": month, "narrative": narrative}


def main() -> int:
    uvicorn.run("whatsgoingon.api:app", host="0.0.0.0", port=8000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
