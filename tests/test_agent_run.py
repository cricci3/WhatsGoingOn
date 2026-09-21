from datetime import date
from unittest.mock import MagicMock

import pytest

import whatsgoingon.agent.run as run_module
from whatsgoingon.agent.run import run_cycle


def _future_month() -> str:
    """A month guaranteed to be in the future regardless of when the suite runs."""
    return f"{date.today().year + 1}-01"


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


def _mock_full_cycle(monkeypatch) -> MagicMock:
    """Mock every external call in the non-`--retrieve` path of run_cycle (Anthropic
    client, the agent loop, and the data layer) so only the Obsidian-export wiring is
    under test."""
    monkeypatch.setattr(run_module, "ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(run_module, "compute_deltas", lambda: [MagicMock()])
    monkeypatch.setattr(run_module, "format_deltas", lambda deltas: "delta text")
    monkeypatch.setattr(run_module, "run_agent", lambda *args, **kwargs: "Narrative text.")
    monkeypatch.setattr(run_module.anthropic, "Anthropic", lambda **kwargs: MagicMock())

    mock_export = MagicMock()
    monkeypatch.setattr(run_module, "export_narrative_to_vault", mock_export)
    return mock_export


def test_run_cycle_exports_to_obsidian_vault_when_configured(monkeypatch, tmp_path) -> None:
    mock_export = _mock_full_cycle(monkeypatch)
    monkeypatch.setattr(run_module, "OBSIDIAN_VAULT_PATH", tmp_path)

    cached = {
        "month": "2026-08",
        "narrative": "Narrative text.",
        "delta_summary": "delta text",
        "generated_at": "2026-08-01T00:00:00+00:00",
    }
    store = MagicMock()
    store.get_similar_narratives.return_value = []
    store.get_narrative.return_value = cached
    store.list_months.return_value = ["2026-06", "2026-07"]

    result = run_cycle(month="2026-08", store=store)

    mock_export.assert_called_once_with(cached, tmp_path, previous_month="2026-07")
    assert result == cached


def test_run_cycle_skips_export_when_vault_not_configured(monkeypatch) -> None:
    mock_export = _mock_full_cycle(monkeypatch)
    monkeypatch.setattr(run_module, "OBSIDIAN_VAULT_PATH", None)

    store = MagicMock()
    store.get_similar_narratives.return_value = []
    store.get_narrative.return_value = {"month": "2026-08", "narrative": "Narrative text."}

    run_cycle(month="2026-08", store=store)

    mock_export.assert_not_called()


def test_run_cycle_does_not_fail_when_export_raises(monkeypatch, tmp_path) -> None:
    mock_export = _mock_full_cycle(monkeypatch)
    mock_export.side_effect = OSError("disk full")
    monkeypatch.setattr(run_module, "OBSIDIAN_VAULT_PATH", tmp_path)

    cached = {"month": "2026-08", "narrative": "Narrative text."}
    store = MagicMock()
    store.get_similar_narratives.return_value = []
    store.get_narrative.return_value = cached
    store.list_months.return_value = []

    result = run_cycle(month="2026-08", store=store)

    assert result == cached


def test_run_cycle_warns_when_month_is_in_the_future(monkeypatch) -> None:
    _mock_full_cycle(monkeypatch)
    monkeypatch.setattr(run_module, "OBSIDIAN_VAULT_PATH", None)
    month = _future_month()

    cached = {"month": month, "narrative": "Narrative text."}
    store = MagicMock()
    store.get_similar_narratives.return_value = []
    store.get_narrative.return_value = cached

    result = run_cycle(month=month, store=store)

    assert month in result["warning"]
    assert "future" in result["warning"]
    # the underlying stored record (and what add_narrative persisted) stays clean
    store.add_narrative.assert_called_once_with(month, "Narrative text.", "delta text")


def test_run_cycle_does_not_warn_for_a_past_month(monkeypatch) -> None:
    _mock_full_cycle(monkeypatch)
    monkeypatch.setattr(run_module, "OBSIDIAN_VAULT_PATH", None)

    cached = {"month": "2026-08", "narrative": "Narrative text."}
    store = MagicMock()
    store.get_similar_narratives.return_value = []
    store.get_narrative.return_value = cached

    result = run_cycle(month="2026-08", store=store)

    assert "warning" not in result


def test_run_cycle_retrieve_warns_when_month_is_in_the_future() -> None:
    month = _future_month()
    store = MagicMock()
    cached = {"month": month, "narrative": "Cached narrative text."}
    store.get_narrative.return_value = cached

    result = run_cycle(month=month, store=store, retrieve=True)

    assert month in result["warning"]
    assert cached == {"month": month, "narrative": "Cached narrative text."}  # not mutated in place


def test_main_prints_warning_to_stderr_for_future_month(monkeypatch, capsys) -> None:
    month = _future_month()
    fake_result = {"month": month, "narrative": "Narrative text.", "warning": f"{month} is a future month."}
    monkeypatch.setattr(run_module, "run_cycle", lambda **kwargs: fake_result)

    exit_code = run_module.main(["--month", month])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "WARNING" in captured.err
    assert month in captured.err
    assert captured.out.strip() == "Narrative text."


def test_main_prints_no_warning_for_a_past_month(monkeypatch, capsys) -> None:
    fake_result = {"month": "2026-08", "narrative": "Narrative text."}
    monkeypatch.setattr(run_module, "run_cycle", lambda **kwargs: fake_result)

    run_module.main(["--month", "2026-08"])

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.strip() == "Narrative text."
