"""Regression guard for the Phase H V3 authoring ownership audit.

The generated matrix is intentionally kept under ``tmp/`` as an audit artifact.
This guard makes the audit fail closed if a schema field becomes unclassified or
an authorable field loses its typed editor owner.
"""

from __future__ import annotations

import csv
from pathlib import Path

MATRIX = (
    Path(__file__).parents[2]
    / "tmp"
    / "scenario_v3_authoring_field_coverage_matrix_2026-09-26.csv"
)
DECOUPLING = (
    Path(__file__).parents[2]
    / "tmp"
    / "scenario_content_decoupling_matrix_2026-09-26.csv"
)
SECTIONS = (
    Path(__file__).parents[2]
    / "tmp"
    / "scenario_editor_section_responsibility_matrix_2026-09-26.csv"
)
PORTABILITY = (
    Path(__file__).parents[2]
    / "tmp"
    / "scenario_file_portability_readiness_2026-09-26.md"
)
CURRENT_CLEANUP = (
    Path(__file__).parents[2]
    / "tmp"
    / "scenario_v3_authoring_coverage_after_goal_cleanup_2026-09-26.csv"
)
ALLOWED = {
    "AUTHORABLE",
    "SYSTEM_MANAGED",
    "INITIALIZATION_AUTHORABLE",
    "DERIVED_READ_ONLY",
    "PLATFORM_POLICY",
    "LEGACY_COMPATIBILITY",
}


def test_phase_h_v3_authoring_matrix_is_complete_and_typed() -> None:
    assert MATRIX.exists(), f"run tmp/generate_phase_h_audits.py before checking {MATRIX}"
    with MATRIX.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 384
    assert {row["classification"] for row in rows} <= ALLOWED
    assert not [row for row in rows if row["classification"] not in ALLOWED]
    world_name = next(row for row in rows if row["schema_path"] == "world.name")
    assert world_name["classification"] == "LEGACY_COMPATIBILITY"
    assert world_name["coverage_status"] == "EXPLICIT_EXEMPTION"

    current = [
        row
        for row in rows
        if row["classification"] in {"AUTHORABLE", "INITIALIZATION_AUTHORABLE"}
    ]
    assert len(current) == 331
    assert not [row for row in current if row["coverage_status"] != "COVERED_TYPED"]
    assert not [row for row in current if not row["editor_section"] or not row["editor_control"]]
    assert len({row["schema_path"] for row in current}) == len(current)


def test_phase_h_decoupling_matrix_is_fail_closed() -> None:
    assert DECOUPLING.exists(), f"missing {DECOUPLING}"
    with DECOUPLING.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert not [row for row in rows if row["production_coupling"] != "0"]
    assert {
        "SCHEMA_CONTRACT",
        "API_CONTRACT",
        "ENGINE_CONTRACT",
        "DATABASE_ENVELOPE",
        "BUILTIN_DATA",
        "SCENARIO_SPECIFIC",
        "LEGACY_COMPATIBILITY",
        "TEST_ONLY",
    } <= {row["category"] for row in rows}


def test_phase_h_section_and_portability_audits_exist() -> None:
    assert SECTIONS.exists(), f"missing {SECTIONS}"
    with SECTIONS.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 24
    assert {row["route_id"] for row in rows} >= {
        "overview",
        "initialization",
        "configuration-check",
        "validation",
        "planning-recovery",
    }
    text = PORTABILITY.read_text(encoding="utf-8")
    assert "JSON export readiness" in text
    assert "YAML import readiness" in text
    assert "DB envelope separation" in text


def test_phase_j_goal_cleanup_coverage_has_one_writer_for_current_fields() -> None:
    assert CURRENT_CLEANUP.exists(), f"missing {CURRENT_CLEANUP}"
    with CURRENT_CLEANUP.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert len(rows) == 381
    assert not {
        "goal_resolution.allow_llm_fallback",
        "goal_resolution.clarification_prompt",
        "goal_resolution.world_goal_state_catalog",
    } & {row["schema_path"] for row in rows}
    current = [
        row
        for row in rows
        if row["classification"] in {"AUTHORABLE", "INITIALIZATION_AUTHORABLE"}
    ]
    assert current
    assert len(current) == 328
    assert not [row for row in current if not row["editor_section"] or not row["editor_control"]]
    assert not [
        row
        for row in current
        if row["writer_count"] != "1" or row["writer_status"] != "OK"
    ]
    assert len({row["schema_path"] for row in current}) == len(current)
