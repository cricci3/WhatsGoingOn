from unittest.mock import MagicMock

import pytest

from whatsgoingon.agent.run import run_cycle


def test_run_cycle_retrieve_returns_cached_narrative_without_calling_agent() -> None:
    store = MagicMock()
    cached = {
        "month": "2026-07",
        "narrative": "Cached narrative text.",
        "delta_summary": "cpi +1%",
        "generated_at": "2026-07-01T00:00:00+00:00",
    }
    store.get_narrative.return_value = cached

    result = run_cycle(month="2026-07", store=store, retrieve=True)

    assert result == cached
    store.get_narrative.assert_called_once_with("2026-07")
    store.get_similar_narratives.assert_not_called()
    store.add_narrative.assert_not_called()


def test_run_cycle_retrieve_raises_if_nothing_cached() -> None:
    store = MagicMock()
    store.get_narrative.return_value = None

    with pytest.raises(RuntimeError, match="no cached narrative"):
        run_cycle(month="2026-07", store=store, retrieve=True)
