import { describe, expect, it } from "vitest";

import { buildEntityNeighborhood, buildScopeOverview, buildScopeTopology, findScopeForNode } from "./topology-projection";

function topologyDocument() {
  return {
    metadata: { locality: { enabled: true, region_node_type_key: "scope", facility_node_type_key: "entity", transport_node_type_key: "link", located_in_relation_type_key: "belongs_to", transport_endpoint_relation_type_key: "connects" } },
    world: {
      node_types: [{ key: "scope", name: "Scope" }, { key: "entity", name: "Entity" }, { key: "link", name: "Link" }],
      nodes: [
        { key: "north", name: "North Scope", node_type_key: "scope", facts: [] },
        { key: "south", name: "South Scope", node_type_key: "scope", facts: [] },
        { key: "north_a", name: "North A", node_type_key: "entity", facts: [{ key: "status" }] },
        { key: "north_b", name: "North B", node_type_key: "entity", facts: [] },
        { key: "south_a", name: "South A", node_type_key: "entity", facts: [] },
        { key: "bridge", name: "North South Link", node_type_key: "link", facts: [] },
        { key: "unrelated", name: "Unrelated", node_type_key: "entity", facts: [] },
      ],
      relations: [
        { key: "north_a__belongs_to__north", source_node_key: "north_a", relation_type_key: "belongs_to", target_node_key: "north" },
        { key: "north_b__belongs_to__north", source_node_key: "north_b", relation_type_key: "belongs_to", target_node_key: "north" },
        { key: "south_a__belongs_to__south", source_node_key: "south_a", relation_type_key: "belongs_to", target_node_key: "south" },
        { key: "bridge__connects__north", source_node_key: "bridge", relation_type_key: "connects", target_node_key: "north" },
        { key: "bridge__connects__south", source_node_key: "bridge", relation_type_key: "connects", target_node_key: "south" },
        { key: "north_a__supplies__north_b", source_node_key: "north_a", relation_type_key: "supplies", target_node_key: "north_b" },
        { key: "north_b__crosses__south_a", source_node_key: "north_b", relation_type_key: "crosses", target_node_key: "south_a" },
      ],
    },
  };
}

describe("generic topology projections", () => {
  it("builds a scope overview from authored scope types and keeps only navigable boundary connections", () => {
    const document = topologyDocument();
    const projection = buildScopeOverview(document);

    expect(projection.scopes.map((scope) => scope.key)).toEqual(["north", "south"]);
    expect(projection.scopes.find((scope) => scope.key === "north")).toMatchObject({ internalNodeKeys: ["north_a", "north_b"], externalConnectionCount: 1 });
    expect(projection.boundaryConnections).toHaveLength(1);
    expect(projection.boundaryConnections[0]).toMatchObject({ kind: "NAVIGABLE_BOUNDARY", sourceScopeKey: "north", targetScopeKey: "south", transportNodeKeys: ["bridge"], relationTypeKeys: ["connects"] });
  });

  it("keeps scope contents local and exposes external scope connections as portals", () => {
    const projection = buildScopeTopology(topologyDocument(), "north");

    expect(projection.nodes.map((node) => node.key)).toEqual(["north_a", "north_b"]);
    expect(projection.relations.map((relation) => relation.relationTypeKey)).toEqual(["supplies"]);
    expect(projection.portals).toHaveLength(1);
    expect(projection.portals[0]).toMatchObject({ neighborScopeKey: "south", transportNodeNames: ["North South Link"] });
  });

  it("builds an entity neighborhood with only one-hop relations", () => {
    const document = topologyDocument();
    const before = JSON.stringify(document);
    const projection = buildEntityNeighborhood(document, "north_b");

    expect(projection.nodes.map((node) => node.key).sort()).toEqual(["north_a", "north_b", "south_a"].sort());
    expect(projection.relations.map((relation) => relation.relationTypeKey).sort()).toEqual(["crosses", "supplies"].sort());
    expect(projection.relations.find((relation) => relation.relationTypeKey === "crosses")?.relationScope).toBe("CROSS_SCOPE_BUSINESS");
    expect(JSON.stringify(document)).toBe(before);
    expect(findScopeForNode(document, "north_b")).toBe("north");
  });

  it("does not invent a hierarchy when locality is not configured", () => {
    const document = { world: { nodes: [{ key: "node", name: "Node", node_type_key: "custom" }], relations: [] } };
    expect(buildScopeOverview(document)).toEqual({ configured: false, scopes: [], boundaryConnections: [] });
  });
});
