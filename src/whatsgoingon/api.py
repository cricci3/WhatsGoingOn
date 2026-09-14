from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date

import uvicorn
from fastapi import FastAPI, HTTPException

from whatsgoingon.agent.narrative_store import NarrativeStore
from whatsgoingon.agent.run import run_cycle
from whatsgoingon.logging_config import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    configure_logging()
    yield


app = FastAPI(title="WhatsGoingOn", description="Monthly macro/markets narrative agent", lifespan=_lifespan)


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
