from copy import deepcopy

from app.scenarios.semantic_diff import semantic_diff
from tests.scenario_fixtures import GENERIC_TEST


def _document() -> dict[str, object]:
    return deepcopy(GENERIC_TEST.model_dump(mode="json"))


def test_canonical_identity_collection_reorder_is_equal() -> None:
    published = _document()
    working = _document()
    working["world"]["nodes"] = list(reversed(working["world"]["nodes"]))  # type: ignore[index]
    working["actions"] = list(reversed(working["actions"]))  # type: ignore[index]

    result = semantic_diff(working, published, include_entries=True)

    assert result["is_equal"] is True
    assert result["entries"] == []


def test_ordered_planning_and_quick_input_reorder_is_real_change() -> None:
    published = _document()
    working = _document()
    published["planning"]["instructions"] = ["first", "second"]  # type: ignore[index]
    working["planning"]["instructions"] = ["second", "first"]  # type: ignore[index]
    published["goal_resolution"]["quick_inputs"] = ["one", "two"]  # type: ignore[index]
    working["goal_resolution"]["quick_inputs"] = ["two", "one"]  # type: ignore[index]

    result = semantic_diff(working, published, include_entries=True)
    entries = result["entries"]

    assert result["is_equal"] is False
    assert isinstance(entries, list)
    assert {item["change_kind"] for item in entries} == {"REORDERED"}
    assert {item["field_path"] for item in entries} == {
        "planning.instructions",
        "goal_resolution.quick_inputs",
    }


def test_initialization_and_design_changes_have_canonical_sections() -> None:
    published = _document()
    working = _document()
    working["world"]["nodes"][0]["initial_access"] = "LOCKED"  # type: ignore[index]
    working["interactions"][0]["name"] = "Changed"  # type: ignore[index]

    result = semantic_diff(working, published, include_entries=True)

    assert result["is_equal"] is False
    assert {item["scope"] for item in result["entries"]} == {"INITIALIZATION", "DESIGN"}  # type: ignore[index]
    assert {item["editor_section"] for item in result["entries"]} == {"初始化", "角色与行动"}  # type: ignore[index]


def test_default_omission_and_canonical_hash_shape_do_not_create_changes() -> None:
    published = _document()
    working = _document()
    # The V2 parser/canonical boundary treats these optional defaults as
    # equivalent legacy omissions.
    published["initialization"].pop("resource_initial_states", None)  # type: ignore[index]
    working["initialization"]["resource_initial_states"] = []  # type: ignore[index]

    result = semantic_diff(working, published)

    assert result["is_equal"] is True
    assert result["total_changed_items"] == 0
