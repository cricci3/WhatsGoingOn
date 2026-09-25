from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from whatsgoingon.config import DEBATES_PATH
from whatsgoingon.orchestrator.state import Claim, DebateState

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def transcript_to_dict(state: DebateState) -> dict[str, Any]:
    """JSON-friendly version of the whole debate - what the API serves and what gets saved:
    the data snapshot, the Context brief, every round (draft, critiques, editor note), the
    published narrative + changelog, the fallbacks, and the spend (tokens, cost, latency per
    agent). Claims cite their series by name; the numbers are in `deltas`, listed once."""
    return {
        "month": state.month,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "max_rounds": state.max_rounds,
        "rounds_used": len(state.rounds),
        "degraded": bool(state.fallbacks),
        "deltas": [{**asdict(d), "pct_change": d.pct_change} for d in state.deltas],
        "context": asdict(state.context) if state.context is not None else None,
        "rounds": [
            {
                "round": number,
                "draft": {
                    "narrative": r.draft.narrative,
                    "claims": [_claim_to_dict(c) for c in r.draft.claims],
                },
                "critiques": [asdict(c) for c in r.critiques],
                "review_skipped": r.review_skipped,
                "editor_note": r.editor_decision.reason if r.editor_decision is not None else None,
            }
            for number, r in enumerate(state.rounds, start=1)
        ],
        "final": (
            {
                "narrative": state.decision.final_narrative,
                "reason": state.decision.reason,
                "changelog": state.decision.changelog,
            }
            if state.decision is not None
            else None
        ),
        "fallbacks": [asdict(f) for f in state.fallbacks],
        "spend": {
            **state.budget.summary(),
            "elapsed_s": round(state.elapsed_s, 3) if state.elapsed_s is not None else None,
        },
    }


def _claim_to_dict(claim: Claim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "text": claim.text,
        "claim_type": claim.claim_type,
        "confidence": claim.confidence,
        "supporting_series": [d.name for d in claim.supporting_deltas],
    }


def _path(month: str, directory: Path) -> Path:
    # The month becomes a file name, so it must be exactly YYYY-MM - no path tricks.
    if not _MONTH_RE.match(month):
        raise ValueError(f"month must be YYYY-MM, got {month!r}")
    return directory / f"{month}.json"


def save_transcript(transcript: dict[str, Any], directory: Path | None = None) -> Path:
    """Write a transcript as <month>.json, replacing any earlier debate for that month (like
    NarrativeStore.add_narrative() does for Phase 1 narratives)."""
    directory = directory or DEBATES_PATH
    path = _path(transcript["month"], directory)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_transcript(month: str, directory: Path | None = None) -> dict[str, Any] | None:
    path = _path(month, directory or DEBATES_PATH)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_latest_transcript(directory: Path | None = None) -> dict[str, Any] | None:
    """The transcript for the most recent month (YYYY-MM file names sort chronologically)."""
    directory = directory or DEBATES_PATH
    months = sorted(p.stem for p in directory.glob("*.json") if _MONTH_RE.match(p.stem))
    return load_transcript(months[-1], directory) if months else None
