from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from whatsgoingon.agent.narrative_store import NarrativeStore


class _FakeEmbeddingFunction(EmbeddingFunction[Documents]):
    """Deterministic, offline stand-in for chromadb's default embedding model, so tests
    don't depend on downloading it."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "fake-length-embedding"

    def __call__(self, input: Documents) -> Embeddings:
        return [[float(len(text)), float(text.count("a")), float(text.count("e"))] for text in input]

    @staticmethod
    def build_from_config(config: dict) -> "EmbeddingFunction[Documents]":
        return _FakeEmbeddingFunction()

    def get_config(self) -> dict:
        return {}


def _make_store(tmp_path) -> NarrativeStore:
    return NarrativeStore(path=tmp_path / "chroma", embedding_function=_FakeEmbeddingFunction())


def test_get_similar_narratives_empty_store_returns_empty_list(tmp_path) -> None:
    store = _make_store(tmp_path)

    assert store.get_similar_narratives("anything") == []


def test_add_and_retrieve_narrative(tmp_path) -> None:
    store = _make_store(tmp_path)

    store.add_narrative("2026-07", "CPI rose modestly on energy costs.", "cpi +1%")

    results = store.get_similar_narratives("CPI rose modestly on energy costs.")

    assert len(results) == 1
    assert results[0]["month"] == "2026-07"
    assert results[0]["narrative"] == "CPI rose modestly on energy costs."


def test_get_similar_narratives_excludes_given_month(tmp_path) -> None:
    store = _make_store(tmp_path)
    store.add_narrative("2026-07", "CPI rose modestly on energy costs.", "cpi +1%")
    store.add_narrative("2026-08", "CPI rose again this month.", "cpi +1.2%")

    results = store.get_similar_narratives("CPI rose", exclude_month="2026-08")

    months = [r["month"] for r in results]
    assert "2026-08" not in months
    assert "2026-07" in months


def test_add_narrative_upserts_same_month(tmp_path) -> None:
    store = _make_store(tmp_path)
    store.add_narrative("2026-07", "First draft.", "cpi +1%")
    store.add_narrative("2026-07", "Revised narrative.", "cpi +1%")

    results = store.get_similar_narratives("Revised narrative.")

    assert len(results) == 1
    assert results[0]["narrative"] == "Revised narrative."


def test_get_similar_narratives_respects_n_results(tmp_path) -> None:
    store = _make_store(tmp_path)
    for i in range(5):
        store.add_narrative(f"2026-0{i + 1}", f"Narrative number {i}.", "delta")

    results = store.get_similar_narratives("Narrative", n_results=2)

    assert len(results) == 2


def test_get_narrative_missing_month_returns_none(tmp_path) -> None:
    store = _make_store(tmp_path)
    store.add_narrative("2026-07", "July narrative.", "delta july")

    assert store.get_narrative("2026-08") is None


def test_get_narrative_returns_exact_month(tmp_path) -> None:
    store = _make_store(tmp_path)
    store.add_narrative("2026-07", "July narrative.", "delta july")
    store.add_narrative("2026-08", "August narrative.", "delta august")

    assert store.get_narrative("2026-07") == {
        "month": "2026-07",
        "narrative": "July narrative.",
        "delta_summary": "delta july",
    }


def test_get_latest_narrative_empty_store_returns_none(tmp_path) -> None:
    store = _make_store(tmp_path)

    assert store.get_latest_narrative() is None


def test_get_latest_narrative_returns_max_month(tmp_path) -> None:
    store = _make_store(tmp_path)
    store.add_narrative("2026-06", "June narrative.", "delta june")
    store.add_narrative("2026-08", "August narrative.", "delta august")
    store.add_narrative("2026-07", "July narrative.", "delta july")

    latest = store.get_latest_narrative()

    assert latest == {"month": "2026-08", "narrative": "August narrative.", "delta_summary": "delta august"}
