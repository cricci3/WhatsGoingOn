from whatsgoingon.agent.obsidian_export import (
    MACRO_NARRATIVE_TAG,
    _derive_tags,
    export_narrative_to_vault,
)

SAMPLE_DELTA_SUMMARY = (
    "- cpi: 300.00 (2026-07-01) -> 301.00 (2026-08-01), change +1.00 (+0.33%)\n"
    "- wti_oil: 60.00 (2026-07-01) -> 70.00 (2026-08-01), change +10.00 (+16.67%)\n"
    "- fed_funds_rate: 3.50 (2026-07-01) -> 3.50 (2026-08-01), change +0.00 (n/a)\n"
    "- unemployment_rate: 4.00 (2026-07-01) -> 3.70 (2026-08-01), change -0.30 (-7.50%)"
)


def _make_record(**overrides) -> dict:
    record = {
        "month": "2026-08",
        "narrative": "Oil surged this month, pushing yields higher.",
        "delta_summary": SAMPLE_DELTA_SUMMARY,
        "generated_at": "2026-08-01T09:00:00+00:00",
    }
    record.update(overrides)
    return record


def test_derive_tags_only_includes_significant_tracked_series() -> None:
    tags = _derive_tags(SAMPLE_DELTA_SUMMARY)

    assert tags[0] == MACRO_NARRATIVE_TAG
    assert "wti_oil" in tags
    assert "unemployment_rate" in tags
    assert "cpi" not in tags
    assert "fed_funds_rate" not in tags


def test_derive_tags_ignores_untracked_or_malformed_lines() -> None:
    delta_summary = (
        "- not_a_tracked_series: 1.00 (2026-07-01) -> 100.00 (2026-08-01), change +99.00 (+9900.00%)\n"
        "this line does not match the expected format at all"
    )

    tags = _derive_tags(delta_summary)

    assert tags == [MACRO_NARRATIVE_TAG]


def test_export_writes_frontmatter_and_body(tmp_path) -> None:
    record = _make_record()

    note_path = export_narrative_to_vault(record, tmp_path)

    assert note_path == tmp_path / "2026-08.md"
    content = note_path.read_text(encoding="utf-8")

    assert content.startswith("---\n")
    assert 'month: "2026-08"' in content
    assert 'generated_at: "2026-08-01T09:00:00+00:00"' in content
    assert "delta_summary:" in content
    assert "wti_oil" in content
    assert content.strip().endswith("Oil surged this month, pushing yields higher.")


def test_export_includes_wikilink_when_previous_month_given(tmp_path) -> None:
    record = _make_record()

    note_path = export_narrative_to_vault(record, tmp_path, previous_month="2026-07")

    content = note_path.read_text(encoding="utf-8")
    assert 'previous: "[[2026-07]]"' in content


def test_export_omits_previous_field_when_no_previous_month(tmp_path) -> None:
    record = _make_record()

    note_path = export_narrative_to_vault(record, tmp_path)

    content = note_path.read_text(encoding="utf-8")
    assert "previous:" not in content


def test_export_upserts_same_month_without_duplicating_files(tmp_path) -> None:
    record = _make_record()
    export_narrative_to_vault(record, tmp_path)

    updated = _make_record(narrative="Revised narrative for the month.")
    note_path = export_narrative_to_vault(updated, tmp_path)

    assert list(tmp_path.glob("*.md")) == [note_path]
    assert "Revised narrative for the month." in note_path.read_text(encoding="utf-8")


def test_export_creates_vault_directory_if_missing(tmp_path) -> None:
    vault_path = tmp_path / "nested" / "vault"
    record = _make_record()

    note_path = export_narrative_to_vault(record, vault_path)

    assert note_path.exists()
