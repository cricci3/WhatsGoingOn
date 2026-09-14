from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.types import EmbeddingFunction

from whatsgoingon.config import CHROMA_PATH

COLLECTION_NAME = "narratives"


class NarrativeStore:
    """Stores each monthly narrative in a local Chroma collection and retrieves
    the most relevant past narratives as context for the next one, so the agent's
    "world model" stays consistent month to month rather than contradicting itself."""

    def __init__(self, path: Path | str = CHROMA_PATH, embedding_function: EmbeddingFunction | None = None):
        self._client = chromadb.PersistentClient(path=str(path))
        kwargs: dict[str, Any] = {"name": COLLECTION_NAME}
        if embedding_function is not None:
            kwargs["embedding_function"] = embedding_function
        self._collection = self._client.get_or_create_collection(**kwargs)

    def add_narrative(self, month: str, narrative: str, delta_summary: str) -> None:
        self._collection.upsert(
            ids=[month],
            documents=[narrative],
            metadatas=[{"month": month, "delta_summary": delta_summary}],
        )

    def get_similar_narratives(
        self, query_text: str, *, n_results: int = 3, exclude_month: str | None = None
    ) -> list[dict[str, Any]]:
        count = self._collection.count()
        if count == 0:
            return []

        result = self._collection.query(
            query_texts=[query_text],
            n_results=min(n_results + (1 if exclude_month else 0), count),
        )

        narratives = []
        for month, narrative, distance in zip(
            result["ids"][0], result["documents"][0], result["distances"][0], strict=True
        ):
            if month == exclude_month:
                continue
            narratives.append({"month": month, "narrative": narrative, "distance": distance})
        return narratives[:n_results]

    def get_latest_narrative(self) -> dict[str, Any] | None:
        """Return the most recently stored narrative (by month key), or None if the
        store is empty. Months sort lexicographically (YYYY-MM), so the max id wins."""
        result = self._collection.get()
        if not result["ids"]:
            return None

        latest_index = max(range(len(result["ids"])), key=lambda i: result["ids"][i])
        return {
            "month": result["ids"][latest_index],
            "narrative": result["documents"][latest_index],
            "delta_summary": result["metadatas"][latest_index].get("delta_summary"),
        }
