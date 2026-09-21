"""End-to-end test of the full ingest -> agent -> store -> API cycle.

Only external network calls are mocked (FRED, Yahoo Finance, the Anthropic client, and
Chroma's default embedding model download); everything else - SQLite upsert, delta
computation, the hand-rolled agent loop, Chroma persistence, and the FastAPI wrapper -
runs for real against a tmp_path sandbox. See test_agent_loop.py for tool_use round-trip
coverage and test_agent_narrative_store.py for NarrativeStore's own unit tests; this file
only has to prove the pieces fit together.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from fastapi.testclient import TestClient

import whatsgoingon.agent.run as run_module
import whatsgoingon.api as api_module
import whatsgoingon.db as db_module
import whatsgoingon.ingest as ingest_module
import whatsgoingon.sources.fred as fred_module
import whatsgoingon.sources.yahoo as yahoo_module
from whatsgoingon.agent.narrative_store import NarrativeStore
from whatsgoingon.agent.run import run_cycle
from whatsgoingon.agent.state import compute_deltas
from whatsgoingon.db import Observation, get_connection

NARRATIVE_TEXT = "Prices and equities both moved up over the past month."
REFERENCE_DATE = "2026-07-01"
LATEST_DATE = "2026-08-01"

# name -> (value ~30 days ago, latest value). A couple of tracked series is enough to
# produce a non-trivial delta end to end; compute_deltas() just skips any tracked series
# left unseeded (no stored observations), so the other FRED/Yahoo series are fine as-is.
_MOCK_SERIES_VALUES: dict[str, tuple[float, float]] = {
    "cpi": (300.00, 310.00),  # +3.33%
    "sp500": (5000.00, 5500.00),  # +10.00%
}


class _FakeEmbeddingFunction(EmbeddingFunction[Documents]):
    """Deterministic, offline stand-in for chromadb's default embedding model (same
    approach as test_agent_narrative_store.py) so this test never hits the network."""

    @staticmethod
    def name() -> str:
        return "fake-length-embedding"

    def __call__(self, input: Documents) -> Embeddings:
        return [[float(len(text)), float(text.count("a")), float(text.count("e"))] for text in input]

    @staticmethod
    def build_from_config(config: dict) -> EmbeddingFunction[Documents]:
        return _FakeEmbeddingFunction()

    def get_config(self) -> dict:
        return {}


def _fake_fred_fetch_series(series_id: str, name: str, api_key: str) -> list[Observation]:
    if name not in _MOCK_SERIES_VALUES:
        return []
    reference_value, latest_value = _MOCK_SERIES_VALUES[name]
    return [
        Observation(
            source="fred", series_id=series_id, name=name, date=REFERENCE_DATE, value=reference_value
        ),
        Observation(source="fred", series_id=series_id, name=name, date=LATEST_DATE, value=latest_value),
    ]


def _fake_yahoo_fetch_series(ticker: str, name: str, *, period: str = "5y") -> list[Observation]:
    if name not in _MOCK_SERIES_VALUES:
        return []
    reference_value, latest_value = _MOCK_SERIES_VALUES[name]
    return [
        Observation(source="yahoo", series_id=ticker, name=name, date=REFERENCE_DATE, value=reference_value),
        Observation(source="yahoo", series_id=ticker, name=name, date=LATEST_DATE, value=latest_value),
    ]


def _end_turn_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


def _wire_e2e_environment(monkeypatch, tmp_path: Path) -> SimpleNamespace:
    """Isolate the whole ingest -> agent -> store -> API cycle from the real filesystem
    and the network, mocking only external calls (FRED, Yahoo Finance, Anthropic).

    Why not just monkeypatch config.DB_PATH / config.CHROMA_PATH and move on: both are
    resolved once, at *def-time*, into other functions' default arguments -
    db.get_connection(db_path=DB_PATH), agent/state.compute_deltas(db_path=DB_PATH) - and
    those call sites (ingest_fred/ingest_yahoo, run_cycle's `compute_deltas()` call) never
    pass db_path explicitly. Patching config.DB_PATH after the fact wouldn't touch
    defaults already baked into those function objects.

    The two clean ways to fix that are (a) monkeypatch the env var and importlib.reload
    every module in the import chain (config -> db -> ingest/state -> run), or (b)
    override the two defaults directly. (a) is fragile here: reload only rebinds names in
    the reloaded module itself, so every downstream `from x import name` site needs
    reloading too (ingest.py's `get_connection`, run.py's `compute_deltas`, ...), and a
    forgotten one silently keeps hitting the real data/ path. It also mutates global
    module state for the rest of the test session unless carefully reloaded back after
    each test. (b) is a couple of direct, single-purpose edits that `monkeypatch` reverts
    on its own - so that's what this uses.

    One wrinkle: db.get_connection is wrapped by @contextmanager, whose wrapper takes
    `*args, **kwds` and has no defaults of its own - the real default lives on
    `get_connection.__wrapped__` (the undecorated generator function), not on
    `get_connection` itself.

    NarrativeStore is simpler: its `path` argument is already accepted explicitly by the
    constructor, so this just constructs a real one against tmp_path and monkeypatches
    the two call sites that build a bare `NarrativeStore()` (agent.run and api) to return
    that same instance, the same way test_api.py already patches
    `whatsgoingon.api.NarrativeStore` for its own tests.
    """
    db_path = tmp_path / "whatsgoingon.sqlite3"
    monkeypatch.setattr(db_module.get_connection.__wrapped__, "__defaults__", (str(db_path),))

    new_kwdefaults = dict(compute_deltas.__kwdefaults__)
    new_kwdefaults["db_path"] = str(db_path)
    monkeypatch.setattr(compute_deltas, "__kwdefaults__", new_kwdefaults)

    monkeypatch.setattr(ingest_module, "FRED_API_KEY", "fake-fred-key")
    monkeypatch.setattr(fred_module, "fetch_series", _fake_fred_fetch_series)
    monkeypatch.setattr(yahoo_module, "fetch_series", _fake_yahoo_fetch_series)

    store = NarrativeStore(path=tmp_path / "chroma", embedding_function=_FakeEmbeddingFunction())
    monkeypatch.setattr(run_module, "NarrativeStore", lambda: store)
    monkeypatch.setattr(api_module, "NarrativeStore", lambda: store)

    monkeypatch.setattr(run_module, "ANTHROPIC_API_KEY", "fake-anthropic-key")
    fake_client = Mock()
    fake_client.messages.create = Mock(return_value=_end_turn_response(NARRATIVE_TEXT))
    monkeypatch.setattr(run_module.anthropic, "Anthropic", lambda **kwargs: fake_client)

    return SimpleNamespace(store=store, db_path=db_path, chroma_path=tmp_path / "chroma")


def test_full_cycle_ingest_through_api(monkeypatch, tmp_path) -> None:
    env = _wire_e2e_environment(monkeypatch, tmp_path)

    inserted = ingest_module.ingest_fred() + ingest_module.ingest_yahoo()
    assert inserted == 2 * len(_MOCK_SERIES_VALUES)

    month = "2026-08"
    result = run_cycle(month=month)

    assert result["narrative"] == NARRATIVE_TEXT
    assert "cpi" in result["delta_summary"]
    assert "sp500" in result["delta_summary"]

    # Reread through a brand-new NarrativeStore pointed at the same on-disk Chroma
    # collection, to prove the narrative was actually persisted rather than just held
    # in the in-memory `store` object.
    reread_store = NarrativeStore(path=env.chroma_path, embedding_function=_FakeEmbeddingFunction())
    assert reread_store.get_narrative(month) == result
    assert reread_store.get_latest_narrative() == result

    client = TestClient(api_module.app)
    refresh_response = client.post("/refresh", params={"month": month})
    assert refresh_response.status_code == 200
    refreshed = refresh_response.json()
    assert refreshed["narrative"] == NARRATIVE_TEXT

    state_response = client.get("/state")
    assert state_response.status_code == 200
    assert state_response.json() == refreshed


def test_ingest_is_idempotent(monkeypatch, tmp_path) -> None:
    env = _wire_e2e_environment(monkeypatch, tmp_path)

    ingest_module.ingest_fred()
    ingest_module.ingest_yahoo()
    with get_connection(str(env.db_path)) as conn:
        first_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

    ingest_module.ingest_fred()
    ingest_module.ingest_yahoo()
    with get_connection(str(env.db_path)) as conn:
        second_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

    assert first_count > 0
    assert second_count == first_count
