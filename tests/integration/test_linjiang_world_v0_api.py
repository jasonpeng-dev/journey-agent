from typing import Any

from fastapi.testclient import TestClient


def test_linjiang_world_v0_draft_round_trip_preserves_topology_and_localization(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/scenarios",
        json={
            "mode": "EXAMPLE",
            "key": "linjiang_world_v0_test",
            "name": "临江市灾后基础设施恢复测试",
            "example_key": "linjiang_infrastructure_recovery",
        },
    )
    assert created.status_code == 201
    scenario_id = created.json()["id"]

    draft_response = client.get(f"/api/v1/scenarios/{scenario_id}/draft")
    assert draft_response.status_code == 200
    document: dict[str, Any] = draft_response.json()["definition_document"]
    nodes = document["world"]["nodes"]
    relations = document["world"]["relations"]
    nodes_by_key = {node["key"]: node for node in nodes}

    assert document["metadata"]["name"] == "临江市灾后基础设施恢复测试"
    assert len([node for node in nodes if node["node_type_key"] == "region"]) == 6
    assert len([node for node in nodes if node["node_type_key"] == "facility"]) == 24
    assert len([node for node in nodes if node["node_type_key"] == "transport"]) == 6
    assert len({node["key"] for node in nodes}) == 36
    assert nodes_by_key["central_hospital"]["name"] == "中央医院"
    assert nodes_by_key["central_hospital"]["description"]

    located_in = {
        relation["source_node_key"]: relation["target_node_key"]
        for relation in relations
        if relation["relation_type_key"] == "located_in"
    }
    assert len(located_in) == 24
    assert located_in["central_hospital"] == "central_district"
    assert located_in["river_port"] == "south_waterfront_district"

    endpoints: dict[str, set[str]] = {}
    for relation in relations:
        if relation["relation_type_key"] == "endpoint":
            endpoints.setdefault(relation["source_node_key"], set()).add(
                relation["target_node_key"]
            )
    assert endpoints["central_river_tunnel"] == {
        "central_district",
        "east_residential_district",
    }
    assert all(len(targets) == 2 for targets in endpoints.values())

    validation = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/validate",
        json={"expected_revision": 1},
    )
    assert validation.status_code == 200
    assert validation.json()["publish_ready"] is True

    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": document},
    )
    assert saved.status_code == 200
    reloaded = client.get(f"/api/v1/scenarios/{scenario_id}/draft")
    assert reloaded.status_code == 200
    assert reloaded.json()["definition_document"] == document

    published = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/publish",
        json={
            "expected_revision": 2,
            "expected_content_hash": validation.json()["content_hash"],
        },
    )
    assert published.status_code == 200
