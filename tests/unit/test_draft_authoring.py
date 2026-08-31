import pytest

from app.scenarios.authoring import DraftAuthoringError, delete_object, reference_index, rename_key
from tests.scenario_fixtures import GENERIC_TEST


def _document():  # type: ignore[no-untyped-def]
    return GENERIC_TEST.model_dump(mode="json")


def test_reference_index_reports_used_by_edges() -> None:
    edges = reference_index(_document())

    assert any(
        edge.source.object_kind == "action"
        and edge.source.object_key == "diagnose_patient"
        and edge.target.object_kind == "interaction"
        and edge.target.object_key == "diagnosable"
        for edge in edges
    )
    assert any(
        edge.target.object_kind == "node" and edge.target.object_key == "patient_one"
        for edge in edges
    )


def test_stable_key_rename_atomically_updates_declared_references() -> None:
    renamed = rename_key(
        _document(),
        object_kind="interaction",
        old_key="diagnosable",
        new_key="diagnosis_capability",
    )

    interactions = {item["key"] for item in renamed["interactions"]}
    assert "diagnosis_capability" in interactions and "diagnosable" not in interactions
    assert renamed["actions"][0]["required_interaction_key"] == "diagnosis_capability"
    patient = next(item for item in renamed["world"]["nodes"] if item["key"] == "patient_one")
    assert "diagnosis_capability" in patient["interaction_keys"]


def test_referenced_delete_is_blocked_and_unreferenced_delete_succeeds() -> None:
    with pytest.raises(DraftAuthoringError) as referenced:
        delete_object(_document(), object_kind="interaction", object_key="diagnosable")
    assert referenced.value.code == "SCENARIO_OBJECT_REFERENCED"
    assert referenced.value.references

    document = _document()
    document["world"]["resources"].append(
        {"key": "unused", "name": "Unused", "initial_value": 0, "minimum": 0}
    )
    changed = delete_object(document, object_kind="resource", object_key="unused")
    assert all(item["key"] != "unused" for item in changed["world"]["resources"])
