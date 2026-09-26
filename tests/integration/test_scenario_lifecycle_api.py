from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.provider import PlannerInput, PlanRequest
from app.infrastructure.db.models import Scenario, ScenarioDraft, ScenarioVersion
from app.scenarios.documents import parse_scenario_document_versioned
from app.scenarios.serialization import legacy_resource_source_hint_payload


def _create_example(client: TestClient, *, key: str = "clinic_one") -> dict[str, object]:
    response = client.post(
        "/api/v1/scenarios",
        json={
            "mode": "EXAMPLE",
            "key": key,
            "name": "Clinic One",
            "example_key": "linjiang_infrastructure_recovery_v2_0",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_blank_draft_is_editable_but_cannot_publish(client: TestClient) -> None:
    created = client.post(
        "/api/v1/scenarios",
        json={"mode": "BLANK", "key": "blank_case", "name": "Blank Case"},
    )
    assert created.status_code == 201
    scenario_id = created.json()["id"]

    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft")
    assert draft.status_code == 200
    assert draft.json()["revision"] == 1
    document = draft.json()["definition_document"]
    assert document["schema_version"] == 3
    assert "recovery_hints" not in document["planning"]
    assert document["world"]["nodes"] == []
    assert "public_knowledge" not in draft.json()["definition_document"]

    incomplete = {"metadata": {"key": "blank_case", "name": "Still Draft"}}
    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": incomplete},
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2

    stale = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": incomplete},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "SCENARIO_DRAFT_CONFLICT"

    validation = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/validate",
        json={"expected_revision": 2},
    )
    assert validation.status_code == 200
    assert validation.json()["publish_ready"] is False
    assert validation.json()["issues"][0]["severity"] == "ERROR"

    publish = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/publish",
        json={"expected_revision": 2},
    )
    assert publish.status_code == 409
    assert publish.json()["error"]["code"] == "SCENARIO_DRAFT_INVALID"


def test_planning_instruction_save_reload_reaches_provider_payload(
    client: TestClient,
) -> None:
    """The current authoring instruction survives the complete draft boundary."""

    created = _create_example(client, key="planning_instruction_provider_boundary")
    scenario_id = created["id"]
    initial = client.get(f"/api/v1/scenarios/{scenario_id}/draft")
    assert initial.status_code == 200, initial.text
    initial_payload = initial.json()
    working = deepcopy(initial_payload["definition_document"])
    instruction = "优先确认当前区域的公开前置动作"
    working["planning"]["instructions"].append(instruction)

    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={
            "expected_revision": initial_payload["revision"],
            "definition_document": working,
        },
    )
    assert saved.status_code == 200, saved.text

    reloaded = client.get(f"/api/v1/scenarios/{scenario_id}/draft")
    assert reloaded.status_code == 200, reloaded.text
    document = reloaded.json()["definition_document"]
    authored = parse_scenario_document_versioned(document)
    instructions = tuple(authored.planning.instructions)
    assert instruction in instructions

    payload = PlanRequest(
        call_type="INITIAL_PLAN",
        planner_input=PlannerInput(),
        author_planning_instructions=instructions,
    ).provider_payload()
    assert instruction in payload["author_planning_instructions"]
    assert "recovery_hints" not in payload


def test_legacy_draft_get_is_read_only_and_manual_save_persists_current_shape(
    client: TestClient,
    session: Session,
) -> None:
    created = _create_example(client, key="legacy_source_hint_draft")
    scenario_id = UUID(str(created["id"]))
    draft = session.scalar(select(ScenarioDraft).where(ScenarioDraft.scenario_id == scenario_id))
    assert draft is not None
    legacy_document = legacy_resource_source_hint_payload(draft.definition_document)
    assert legacy_document is not None
    draft.definition_document = deepcopy(legacy_document)
    session.flush()
    stored_legacy_document = deepcopy(draft.definition_document)

    opened = client.get(f"/api/v1/scenarios/{scenario_id}/draft")
    assert opened.status_code == 200, opened.text
    opened_document = opened.json()["definition_document"]
    assert "public_knowledge" not in opened_document
    hinted_resource = next(
        item for item in opened_document["world"]["resources"] if "source_hint" in item
    )
    assert "resource_key" not in hinted_resource["source_hint"]
    assert hinted_resource["source_hint"]

    session.refresh(draft)
    assert draft.definition_document == stored_legacy_document

    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": opened_document},
    )
    assert saved.status_code == 200, saved.text
    session.refresh(draft)
    assert saved.json()["revision"] == 2
    assert "public_knowledge" not in draft.definition_document
    assert draft.definition_document == opened_document


def test_initialization_preview_is_readonly_and_uses_the_working_document(
    client: TestClient,
) -> None:
    created = _create_example(client, key="initialization_preview")
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    working = deepcopy(draft["definition_document"])
    working["world"]["nodes"][0]["initial_access"] = "LOCKED"

    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/initialization-preview",
        json={
            "expected_revision": draft["revision"],
            "definition_document": working,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["id"] for item in payload["projection"]["domains"]] == [
        "basic",
        "nodes",
        "actors",
        "resources",
        "relations",
        "derived",
    ]
    assert payload["projection"]["summary"]["nodes"] == 41
    persisted = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    assert persisted["revision"] == draft["revision"]
    assert persisted["definition_document"] == draft["definition_document"]


def test_publish_versions_restore_clone_and_archive_are_isolated(
    client: TestClient,
    session: Session,
) -> None:
    created = _create_example(client)
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()

    validation = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/validate",
        json={"expected_revision": draft["revision"]},
    )
    assert validation.status_code == 200
    assert validation.json()["publish_ready"] is True
    content_hash = validation.json()["content_hash"]

    published_v1 = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/publish",
        json={"expected_revision": 1, "expected_content_hash": content_hash},
    )
    assert published_v1.status_code == 200, published_v1.text
    version_one = published_v1.json()["version"]
    assert version_one["version_number"] == 1

    no_change = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/publish",
        json={"expected_revision": 1},
    )
    assert no_change.status_code == 409
    assert no_change.json()["error"]["code"] == "SCENARIO_PUBLISH_NO_CHANGES"

    changed_document = deepcopy(draft["definition_document"])
    changed_document["metadata"]["name"] = "Clinic Two"
    changed_document["world"]["name"] = "Clinic Two"
    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": changed_document},
    )
    assert saved.status_code == 200

    published_v2 = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/publish",
        json={"expected_revision": 2},
    )
    assert published_v2.status_code == 200
    version_two = published_v2.json()["version"]
    assert version_two["version_number"] == 2

    old_version = client.get(f"/api/v1/scenarios/{scenario_id}/versions/{version_one['id']}").json()
    assert old_version["definition_document"]["metadata"]["name"] == "Clinic One"

    versions = client.get(f"/api/v1/scenarios/{scenario_id}/versions")
    assert [item["version_number"] for item in versions.json()] == [2, 1]

    restored = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/restore",
        json={"expected_revision": 2, "version_id": version_one["id"]},
    )
    assert restored.status_code == 200
    assert restored.json()["revision"] == 3
    assert restored.json()["base_scenario_version_id"] == version_one["id"]
    assert restored.json()["definition_document"]["metadata"]["name"] == "Clinic One"

    clone = client.post(
        "/api/v1/scenarios",
        json={
            "mode": "CLONE_VERSION",
            "key": "clinic_clone",
            "name": "Clinic Clone",
            "source_version_id": version_one["id"],
        },
    )
    assert clone.status_code == 201
    clone_draft = client.get(f"/api/v1/scenarios/{clone.json()['id']}/draft").json()
    assert clone_draft["definition_document"]["metadata"]["key"] == "clinic_clone"
    assert clone_draft["base_scenario_version_id"] == version_one["id"]

    archived = client.post(f"/api/v1/scenarios/{scenario_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    blocked = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={
            "expected_revision": 3,
            "definition_document": restored.json()["definition_document"],
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SCENARIO_ARCHIVED"

    assert (
        session.scalar(select(ScenarioVersion).where(ScenarioVersion.id == UUID(version_one["id"])))
        is not None
    )


def test_scenario_library_detail_identity_and_not_found_contract(
    client: TestClient,
    session: Session,
) -> None:
    created = _create_example(client, key="library_case")
    scenario_id = created["id"]

    listing = client.get("/api/v1/scenarios")
    assert listing.status_code == 200
    assert any(item["id"] == scenario_id for item in listing.json())

    detail = client.get(f"/api/v1/scenarios/{scenario_id}")
    assert detail.status_code == 200
    assert detail.json()["key"] == "library_case"

    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    document = draft["definition_document"]
    document["metadata"]["key"] = "renamed_identity"
    immutable = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": document},
    )
    assert immutable.status_code == 409
    assert immutable.json()["error"]["code"] == "SCENARIO_KEY_IMMUTABLE"

    missing = client.get(f"/api/v1/scenarios/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SCENARIO_NOT_FOUND"

    assert session.scalar(select(Scenario).where(Scenario.key == "library_case")) is not None


def test_reference_navigation_atomic_rename_and_guarded_delete(client: TestClient) -> None:
    created = _create_example(client, key="reference_case")
    scenario_id = created["id"]

    document = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()["definition_document"]
    interaction_key = document["actions"][0]["required_interaction_key"]
    references = client.get(f"/api/v1/scenarios/{scenario_id}/draft/references")
    assert references.status_code == 200
    assert any(
        edge["target"]["object_kind"] == "interaction"
        and edge["target"]["object_key"] == interaction_key
        for edge in references.json()["references"]
    )

    blocked = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/delete-object",
        json={
            "expected_revision": 1,
            "object_kind": "interaction",
            "object_key": interaction_key,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SCENARIO_OBJECT_REFERENCED"

    renamed = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/rename-key",
        json={
            "expected_revision": 1,
            "object_kind": "interaction",
            "old_key": interaction_key,
            "new_key": "diagnosis_capability",
        },
    )
    assert renamed.status_code == 200
    assert renamed.json()["revision"] == 2
    assert renamed.json()["definition_document"]["actions"][0]["required_interaction_key"] == (
        "diagnosis_capability"
    )


def test_working_copy_transform_and_reference_analysis_do_not_persist(client: TestClient) -> None:
    created = _create_example(client, key="working_copy_case")
    scenario_id = created["id"]
    draft_response = client.get(f"/api/v1/scenarios/{scenario_id}/draft")
    original = draft_response.json()["definition_document"]
    interaction_key = original["actions"][0]["required_interaction_key"]

    analysis_document = deepcopy(original)
    analysis_document["actions"][0]["required_interaction_key"] = "working_copy_interaction"
    analyzed = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/reference-analysis",
        json={"expected_revision": 1, "definition_document": analysis_document},
    )
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["source"] == "WORKING_COPY"
    assert analyzed.json()["base_revision"] == 1
    assert any(
        edge["target"]["object_kind"] == "interaction"
        and edge["target"]["object_key"] == "working_copy_interaction"
        for edge in analyzed.json()["references"]
    )

    renamed = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/transform",
        json={
            "expected_revision": 1,
            "definition_document": original,
            "operation": {
                "kind": "RENAME_KEY",
                "object_kind": "interaction",
                "old_key": interaction_key,
                "new_key": "working_copy_renamed",
            },
        },
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["base_revision"] == 1
    assert renamed.json()["definition_document"]["actions"][0]["required_interaction_key"] == (
        "working_copy_renamed"
    )

    with_unused = deepcopy(original)
    with_unused["world"]["resources"].append(
        {"key": "working_copy_unused", "name": "Working copy only", "initial_value": 0}
    )
    deleted = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/transform",
        json={
            "expected_revision": 1,
            "definition_document": with_unused,
            "operation": {
                "kind": "DELETE_OBJECT",
                "object_kind": "resource",
                "object_key": "working_copy_unused",
            },
        },
    )
    assert deleted.status_code == 200, deleted.text
    assert all(
        item["key"] != "working_copy_unused"
        for item in deleted.json()["definition_document"]["world"]["resources"]
    )

    fact_document = deepcopy(original)
    node = fact_document["world"]["nodes"][0]
    node_key = node["key"]
    node["facts"].append(
        {
            "key": "working_only",
            "name": "Working only",
            "value_type": "BOOLEAN",
            "initial_value": False,
        }
    )
    fact_document["actions"][0]["planning"]["knowledge_gate"] = {
        "node_key": node_key,
        "fact_key": "working_only",
        "accepted_values": [True],
    }
    blocked_fact = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/transform",
        json={
            "expected_revision": 1,
            "definition_document": fact_document,
            "operation": {
                "kind": "DELETE_FACT",
                "object_kind": "node",
                "node_key": node_key,
                "fact_key": "working_only",
            },
        },
    )
    assert blocked_fact.status_code == 409, blocked_fact.text
    assert blocked_fact.json()["error"]["code"] == "SCENARIO_FACT_REFERENCED"
    assert blocked_fact.json()["error"]["details"]["references"]

    without_reference = deepcopy(fact_document)
    without_reference["actions"][0]["planning"]["knowledge_gate"] = None
    deleted_fact = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/transform",
        json={
            "expected_revision": 1,
            "definition_document": without_reference,
            "operation": {
                "kind": "DELETE_FACT",
                "object_kind": "node",
                "node_key": node_key,
                "fact_key": "working_only",
            },
        },
    )
    assert deleted_fact.status_code == 200, deleted_fact.text
    deleted_node = next(
        item
        for item in deleted_fact.json()["definition_document"]["world"]["nodes"]
        if item["key"] == node_key
    )
    assert all(fact["key"] != "working_only" for fact in deleted_node["facts"])

    persisted = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    assert persisted["revision"] == 1
    assert persisted["definition_document"] == original

    stale = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/reference-analysis",
        json={"expected_revision": 2, "definition_document": original},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "SCENARIO_DRAFT_CONFLICT"

    # Authoring preflight intentionally analyzes the raw working shape instead
    # of parsing the whole ScenarioDefinition first: deletion can itself be a
    # repair for an unrelated invalid field.
    invalid_working = deepcopy(original)
    invalid_working["actions"][0]["unrelated_invalid_field"] = {"value_type": "NOT_A_SCHEMA_VALUE"}
    invalid_working["world"]["nodes"][0]["facts"].append(
        {
            "key": "repair_candidate",
            "name": "Repair candidate",
            "value_type": "BOOLEAN",
            "initial_value": False,
        }
    )
    invalid_delete = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/transform",
        json={
            "expected_revision": 1,
            "definition_document": invalid_working,
            "operation": {
                "kind": "DELETE_FACT",
                "object_kind": "node",
                "node_key": node_key,
                "fact_key": "repair_candidate",
            },
        },
    )
    assert invalid_delete.status_code == 200, invalid_delete.text
    assert invalid_delete.json()["definition_document"]["actions"][0]["unrelated_invalid_field"]
    assert all(
        fact["key"] != "repair_candidate"
        for fact in invalid_delete.json()["definition_document"]["world"]["nodes"][0]["facts"]
    )

    root_working = deepcopy(original)
    root_working["initialization"]["resource_pools"].append(
        {
            "pool_key": "working_pool",
            "resource_key": "medicine",
            "region_key": None,
            "facility_key": None,
            "quantity": 1,
            "reserved_value": 0,
            "visibility": "VISIBLE",
            "availability": "AVAILABLE",
            "survey_discoverable": False,
        }
    )
    root_deleted = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/transform",
        json={
            "expected_revision": 1,
            "definition_document": root_working,
            "operation": {
                "kind": "DELETE_ROOT_COLLECTION_ITEM",
                "object_kind": "initialization",
                "collection": "resource_pools",
                "identity": "working_pool",
            },
        },
    )
    assert root_deleted.status_code == 200, root_deleted.text
    assert all(
        item["pool_key"] != "working_pool"
        for item in root_deleted.json()["definition_document"]["initialization"]["resource_pools"]
    )

    nested_working = deepcopy(original)
    nested_action = next(
        item for item in nested_working["actions"] if item["key"] == "repair_facility"
    )
    nested_action["parameters"].append(
        {
            "key": "working_parameter",
            "name": "Working parameter",
            "value_type": "INTEGER",
            "required": False,
            "minimum": 0,
            "maximum": 10,
            "allowed_values": [],
            "default": 1,
        }
    )
    nested_working["rules"].append(
        {
            "key": "working_parameter_rule",
            "phase": "RESOLVE",
            "action_key": "repair_facility",
            "priority": 0,
            "condition": {
                "kind": "PARAMETER_COMPARE",
                "parameter_key": "working_parameter",
                "operator": "GTE",
                "value": 1,
            },
            "effects": [],
        }
    )
    blocked_nested = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/transform",
        json={
            "expected_revision": 1,
            "definition_document": nested_working,
            "operation": {
                "kind": "DELETE_NESTED",
                "object_kind": "action",
                "parent_kind": "action",
                "parent_key": "repair_facility",
                "collection": "parameters",
                "nested_key": "working_parameter",
            },
        },
    )
    assert blocked_nested.status_code == 409, blocked_nested.text
    assert blocked_nested.json()["error"]["code"] == "SCENARIO_NESTED_OBJECT_REFERENCED"
    assert blocked_nested.json()["error"]["details"]["references"]

    nested_without_reference = deepcopy(nested_working)
    nested_without_reference["rules"][-1]["condition"] = None
    deleted_nested = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/transform",
        json={
            "expected_revision": 1,
            "definition_document": nested_without_reference,
            "operation": {
                "kind": "DELETE_NESTED",
                "object_kind": "action",
                "parent_kind": "action",
                "parent_key": "repair_facility",
                "collection": "parameters",
                "nested_key": "working_parameter",
            },
        },
    )
    assert deleted_nested.status_code == 200, deleted_nested.text
    assert next(
        item for item in deleted_nested.json()["definition_document"]["actions"]
        if item["key"] == "repair_facility"
    )["parameters"] == []


def test_working_copy_completeness_is_readonly_and_tolerates_temporary_invalid_shape(
    client: TestClient,
) -> None:
    created = _create_example(client, key="completeness_case")
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    working = deepcopy(draft["definition_document"])
    working["temporary_editor_note"] = {"not_in_schema": True}
    working["initialization"]["start_node_key"] = "temporary_missing_node"

    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/completeness",
        json={"expected_revision": draft["revision"], "definition_document": working},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["base_revision"] == draft["revision"]
    assert payload["required_missing"] > 0
    assert payload["validation_issue_count"] > 0
    assert payload["validation_issues"]
    assert all(
        "code" in issue and "path" in issue and "type" in issue
        for issue in payload["validation_issues"]
    )
    assert any(item["key"] == "initialization.start-node" for item in payload["items"])
    persisted = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    assert persisted["revision"] == draft["revision"]
    assert persisted["definition_document"] == draft["definition_document"]


def test_working_copy_completeness_returns_nested_action_outcome_locator_without_writing(
    client: TestClient,
) -> None:
    created = _create_example(client, key="nested_outcome_completeness")
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    working = deepcopy(draft["definition_document"])
    action = working["actions"][0]
    outcome = action["expected_outcomes"][0]
    outcome.pop("name", None)

    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/completeness",
        json={"expected_revision": draft["revision"], "definition_document": working},
    )

    assert response.status_code == 200, response.text
    issue = next(
        issue for issue in response.json()["validation_issues"]
        if issue["path"].endswith("expected_outcomes.0.name")
    )
    assert issue["type"] == "missing"
    assert issue["locator"] == {
        "object_kind": "action",
        "object_key": action["key"],
        "field_path": "expected_outcomes.0.name",
    }
    persisted = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    assert persisted["revision"] == draft["revision"]
    assert persisted["definition_document"] == draft["definition_document"]


def test_invalid_initialization_preview_returns_json_safe_validation_details(
    client: TestClient,
) -> None:
    created = _create_example(client, key="invalid_initialization_preview")
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    working = deepcopy(draft["definition_document"])
    resource = next(
        item for item in working["world"]["resources"] if item.get("source_hint")
    )
    resource["source_hint"] = {
        "primary_region_key": None,
        "candidate_region_keys": [],
    }

    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/initialization-preview",
        json={
            "expected_revision": draft["revision"],
            "definition_document": working,
        },
    )

    assert response.status_code == 422, response.text
    payload = response.json()["error"]
    assert payload["code"] == "SCENARIO_INITIALIZATION_PREVIEW_INVALID"
    assert payload["details"]["issues"]
    assert all("ctx" not in issue for issue in payload["details"]["issues"])


def test_incomplete_focused_node_preview_returns_structured_field_locator(
    client: TestClient,
) -> None:
    created = _create_example(client, key="incomplete_focused_node_preview")
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    working = deepcopy(draft["definition_document"])
    existing = working["world"]["nodes"][0]
    incomplete = deepcopy(existing)
    incomplete["key"] = "new_incomplete_node"
    incomplete["name"] = ""
    working["world"]["nodes"].append(incomplete)

    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/initialization-preview",
        json={
            "expected_revision": draft["revision"],
            "definition_document": working,
            "focus": {"object_kind": "node", "object_key": "new_incomplete_node"},
        },
    )

    assert response.status_code == 422, response.text
    issue = response.json()["error"]["details"]["issues"][0]
    assert issue["loc"][-1] == "name"
    assert "ctx" not in issue
    persisted = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    assert persisted["revision"] == draft["revision"]
    assert persisted["definition_document"] == draft["definition_document"]


def test_unfocused_incomplete_node_returns_partial_workspace_projection_with_recovery_locator(
    client: TestClient,
) -> None:
    created = _create_example(client, key="unfocused_incomplete_node_preview")
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    working = deepcopy(draft["definition_document"])
    existing_key = working["world"]["nodes"][0]["key"]
    working["world"]["nodes"].append({"key": "new_incomplete_node", "name": ""})

    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/initialization-preview",
        json={
            "expected_revision": draft["revision"],
            "definition_document": working,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["partial"] is True
    assert payload["omitted_issue_count"] == len(payload["issues"]) >= 1
    assert all(issue["identity"] == "node:new_incomplete_node" for issue in payload["issues"])
    assert all(issue["canonical_owner"] == "world-entities" for issue in payload["issues"])
    name_issue = next(issue for issue in payload["issues"] if issue["field_path"] == "name")
    assert name_issue["locator"] == {
        "section": "world-entities",
        "object_kind": "node",
        "object_key": "new_incomplete_node",
        "field_path": "name",
    }
    projected_ids = [
        item["id"]
        for domain in payload["projection"]["domains"]
        for group in domain["groups"]
        for item in group["items"]
    ]
    assert f"node:{existing_key}" in projected_ids
    assert "node:new_incomplete_node" not in projected_ids
    persisted = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    assert persisted["revision"] == draft["revision"]
    assert persisted["definition_document"] == draft["definition_document"]


def test_unfocused_missing_node_type_is_attributed_and_pruned_for_partial_preview(
    client: TestClient,
) -> None:
    created = _create_example(client, key="unfocused_missing_node_type_preview")
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    working = deepcopy(draft["definition_document"])
    incomplete = deepcopy(working["world"]["nodes"][0])
    incomplete["key"] = "unrelated_missing_type_node"
    incomplete["name"] = "Unrelated incomplete node"
    incomplete["node_type_key"] = "missing_node_type"
    working["world"]["nodes"].append(incomplete)

    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/initialization-preview",
        json={
            "expected_revision": draft["revision"],
            "definition_document": working,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["partial"] is True
    issue = next(
        issue
        for issue in payload["issues"]
        if issue.get("identity") == "node:unrelated_missing_type_node"
    )
    assert issue["canonical_owner"] == "world-entities"
    assert issue["field_path"] == "node_type_key"
    assert issue["reference_owner"] == "node-types"
    assert issue["locator"] == {
        "section": "world-entities",
        "object_kind": "node",
        "object_key": "unrelated_missing_type_node",
        "field_path": "node_type_key",
    }
    projected_ids = [
        item["id"]
        for domain in payload["projection"]["domains"]
        for group in domain["groups"]
        for item in group["items"]
    ]
    assert "node:unrelated_missing_type_node" not in projected_ids
    persisted = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    assert persisted["revision"] == draft["revision"]
    assert persisted["definition_document"] == draft["definition_document"]


def test_focused_initialization_preview_survives_an_unrelated_incomplete_node(
    client: TestClient,
) -> None:
    created = _create_example(client, key="partial_focused_node_preview")
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    working = deepcopy(draft["definition_document"])
    focus_key = working["world"]["nodes"][0]["key"]
    working["world"]["nodes"].append({"key": "unrelated_incomplete_node", "name": ""})

    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/initialization-preview",
        json={
            "expected_revision": draft["revision"],
            "definition_document": working,
            "focus": {"object_kind": "node", "object_key": focus_key},
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["partial"] is True
    assert payload["omitted_issue_count"] >= 1
    projected_ids = [
        item["id"]
        for domain in payload["projection"]["domains"]
        for group in domain["groups"]
        for item in group["items"]
    ]
    assert f"node:{focus_key}" in projected_ids
    assert "node:unrelated_incomplete_node" not in projected_ids
    persisted = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    assert persisted["revision"] == draft["revision"]
    assert persisted["definition_document"] == draft["definition_document"]

@pytest.mark.parametrize("example_key", ["linjiang_infrastructure_recovery_v2_0"])
def test_generic_editor_round_trip_remains_engine_parseable(
    client: TestClient,
    example_key: str,
) -> None:
    created = client.post(
        "/api/v1/scenarios",
        json={
            "mode": "EXAMPLE",
            "key": f"edited_{example_key}",
            "name": f"Edited {example_key}",
            "example_key": example_key,
        },
    )
    assert created.status_code == 201
    scenario_id = created.json()["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    document = draft["definition_document"]
    document["actions"][0]["description"] = "Edited through the generic Action builder."
    document["planning"]["instructions"].append("Prefer visible, accessible targets.")
    document["derived_states"][0]["description"] += " Edited through the Derived State builder."

    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": document},
    )
    assert saved.status_code == 200
    validation = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/validate",
        json={"expected_revision": 2},
    )
    assert validation.status_code == 200
    assert validation.json()["publish_ready"] is True


def test_definition_schema_exposes_closed_condition_and_effect_vocabulary(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/scenario-definition-schema")
    assert response.status_code == 200
    encoded = response.text
    assert all(kind in encoded for kind in ["ALL", "ANY", "NOT", "RELATION_EXISTS"])
    assert all(kind in encoded for kind in ["SET_FACT", "ADJUST_RESOURCE", "EMIT_OUTCOME"])
    assert "execute_action" not in encoded


def test_examples_expose_the_current_complete_template(client: TestClient) -> None:
    examples = client.get("/api/v1/scenario-examples")
    assert examples.status_code == 200
    maturity = {item["key"]: item["maturity"] for item in examples.json()}
    assert maturity == {
        "linjiang_infrastructure_recovery_v2_0": "PUBLISH_READY",
    }


def test_warning_does_not_block_publish_but_missing_playability_does(
    client: TestClient,
) -> None:
    created = _create_example(client, key="warning_case")
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    document = draft["definition_document"]
    extra = deepcopy(document["actions"][0])
    extra["key"] = "unused_action"
    extra["name"] = "Unused Action"
    document["actions"].append(extra)
    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": document},
    )
    assert saved.status_code == 200
    validation = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/validate",
        json={"expected_revision": 2},
    )
    assert validation.status_code == 200
    assert validation.json()["publish_ready"] is True
    assert any(issue["severity"] == "WARNING" for issue in validation.json()["issues"])
    warning = next(issue for issue in validation.json()["issues"] if issue["severity"] == "WARNING")
    assert warning["locator"] == {
        "object_kind": "action",
        "object_key": "unused_action",
        "field_path": None,
    }
    published = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/publish",
        json={
            "expected_revision": 2,
            "expected_content_hash": validation.json()["content_hash"],
        },
    )
    assert published.status_code == 200

    blocked = _create_example(client, key="not_playable")
    blocked_id = blocked["id"]
    blocked_draft = client.get(f"/api/v1/scenarios/{blocked_id}/draft").json()
    blocked_document = blocked_draft["definition_document"]
    for action in blocked_document["actions"]:
        action["planning"]["terminal_effects"] = []
        action["planning"]["target_terminal_effects"] = []
        action["planning"]["supporting_effects"] = []
    saved_blocked = client.put(
        f"/api/v1/scenarios/{blocked_id}/draft",
        json={"expected_revision": 1, "definition_document": blocked_document},
    )
    assert saved_blocked.status_code == 200
    invalid = client.post(
        f"/api/v1/scenarios/{blocked_id}/draft/validate",
        json={"expected_revision": 2},
    )
    assert invalid.json()["publish_ready"] is False
    publish = client.post(
        f"/api/v1/scenarios/{blocked_id}/draft/publish",
        json={"expected_revision": 2},
    )
    assert publish.status_code == 409
