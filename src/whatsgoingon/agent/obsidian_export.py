from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from whatsgoingon.config import FRED_SERIES, YAHOO_TICKERS

# Always applied, regardless of which series moved.
MACRO_NARRATIVE_TAG = "macro-narrative"

# Minimum absolute percent change (e.g. 5.0 == 5%) for a tracked series to earn its own tag.
SIGNIFICANT_PCT_CHANGE_THRESHOLD = 5.0

# Matches one line of agent.state.format_deltas()'s output, e.g.:
# "- cpi: 100.00 (2024-01-01) -> 101.00 (2024-02-01), change +1.00 (+1.00%)"
_DELTA_LINE_RE = re.compile(r"^-\s*(?P<name>\S+):.*\(\s*(?P<pct>[+-]?\d+(?:\.\d+)?)%\s*\)\s*$")


def _tracked_series_names() -> set[str]:
    return set(FRED_SERIES.values()) | set(YAHOO_TICKERS.values())


def _derive_tags(delta_summary: str) -> list[str]:
    """Extract one tag per tracked series whose delta exceeds the significance threshold.

    Parses the plain-text format produced by agent.state.format_deltas(); any line that
    doesn't match that format is silently skipped rather than raising, so a future change
    to that format degrades export quality instead of breaking the export.
    """
    tracked = _tracked_series_names()
    tags: list[str] = []
    for raw_line in delta_summary.splitlines():
        match = _DELTA_LINE_RE.match(raw_line.strip())
        if match is None:
            continue
        name = match.group("name")
        try:
            pct = float(match.group("pct"))
        except ValueError:
            continue
        if name in tracked and abs(pct) > SIGNIFICANT_PCT_CHANGE_THRESHOLD and name not in tags:
            tags.append(name)
    return [MACRO_NARRATIVE_TAG, *tags]


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_frontmatter(record: dict[str, Any], tags: list[str], previous_month: str | None) -> str:
    delta_summary = record.get("delta_summary") or ""
    delta_lines = [line for line in delta_summary.splitlines() if line.strip()]

    lines = [
        "---",
        f"month: {_yaml_quote(record['month'])}",
        f"generated_at: {_yaml_quote(record.get('generated_at') or '')}",
        "delta_summary:",
    ]
    lines += [f"  - {_yaml_quote(line)}" for line in delta_lines]
    lines.append("tags:")
    lines += [f"  - {tag}" for tag in tags]
    if previous_month:
        lines.append(f"previous: {_yaml_quote(f'[[{previous_month}]]')}")
    lines.append("---")
    return "\n".join(lines)


def export_narrative_to_vault(
    record: dict[str, Any],
    vault_path: Path,
    *,
    previous_month: str | None = None,
) -> Path:
    """Write a narrative record as a Markdown note in a local Obsidian vault folder.

    `record` has the same shape as NarrativeStore.get_narrative(): month, narrative,
    delta_summary, generated_at. The note is named `<month>.md` with YAML frontmatter
    (month, generated_at, delta_summary, tags, and an optional wikilink to the previous
    month's note) so monthly notes connect to each other in Obsidian's graph view.
    Writing again for the same month overwrites the existing note (upsert), matching
    NarrativeStore.add_narrative()'s behavior.
    """
    vault_path = Path(vault_path)
    vault_path.mkdir(parents=True, exist_ok=True)

    tags = _derive_tags(record.get("delta_summary") or "")
    frontmatter = _build_frontmatter(record, tags, previous_month)
    narrative = record.get("narrative") or ""

    note_path = vault_path / f"{record['month']}.md"
    note_path.write_text(f"{frontmatter}\n\n{narrative}\n", encoding="utf-8")
    return note_path
