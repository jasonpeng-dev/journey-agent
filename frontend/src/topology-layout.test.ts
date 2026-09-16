import { describe, expect, it } from "vitest";

import { calculateTopologyFit, createGridTopologyLayout, createRegionNetworkLayout, createScopeTemplateLayout, layoutTopologyGraph, topologyEdgeLaneOffset, topologyLabelPoint, topologyNodeBoundaryPoints, type TopologyLayoutEdge, type TopologyLayoutNode, type TopologyLayoutResult } from "./topology-layout";

const node = (id: string): TopologyLayoutNode => ({ id, width: 124, height: 50 });
const edge = (id: string, source: string, target: string): TopologyLayoutEdge => ({ id, source, target });
const tenNodes = Array.from({ length: 10 }, (_, index) => node(`n${index}`));
const tenEdges = tenNodes.slice(1).map((current, index) => edge(`n${index}-n${index + 1}`, `n${index}`, current.id));
const twentyNodes = Array.from({ length: 20 }, (_, index) => node(`v${index}`));
const twentyEdges = twentyNodes.slice(1).map((current, index) => edge(`v${index}-v${index + 1}`, `v${index}`, current.id));

function assertNoNodeOverlap(layout: TopologyLayoutResult) {
  const nodes = Array.from(layout.nodes.values());
  for (let leftIndex = 0; leftIndex < nodes.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < nodes.length; rightIndex += 1) {
      const left = nodes[leftIndex];
      const right = nodes[rightIndex];
      const horizontalGap = Math.abs(left.x - right.x) - (left.width + right.width) / 2;
      const verticalGap = Math.abs(left.y - right.y) - (left.height + right.height) / 2;
      expect(horizontalGap > 0 || verticalGap > 0).toBe(true);
    }
  }
}

function assertNodesInsideBounds(layout: TopologyLayoutResult) {
  for (const current of layout.nodes.values()) {
    expect(current.x - current.width / 2).toBeGreaterThanOrEqual(-0.01);
    expect(current.y - current.height / 2).toBeGreaterThanOrEqual(-0.01);
    expect(current.x + current.width / 2).toBeLessThanOrEqual(layout.width + 0.01);
    expect(current.y + current.height / 2).toBeLessThanOrEqual(layout.height + 0.01);
  }
}

describe("generic topology layout", () => {
  it.each([
    ["one node", [node("a")], []],
    ["disconnected nodes", [node("a"), node("b"), node("c")], []],
    ["chain", [node("a"), node("b"), node("c"), node("d")], [edge("ab", "a", "b"), edge("bc", "b", "c"), edge("cd", "c", "d")]],
    ["star", [node("a"), node("b"), node("c"), node("d"), node("e")], [edge("ab", "a", "b"), edge("ac", "a", "c"), edge("ad", "a", "d"), edge("ae", "a", "e")]],
    ["cycle", [node("a"), node("b"), node("c"), node("d")], [edge("ab", "a", "b"), edge("bc", "b", "c"), edge("cd", "c", "d"), edge("da", "d", "a")]],
    ["six scope overview", [node("s1"), node("s2"), node("s3"), node("s4"), node("s5"), node("s6")], [edge("s1-s2", "s1", "s2"), edge("s2-s3", "s2", "s3"), edge("s2-s4", "s2", "s4"), edge("s3-s5", "s3", "s5"), edge("s4-s6", "s4", "s6")]],
    ["ten node graph", tenNodes, tenEdges],
    ["twenty node graph", twentyNodes, twentyEdges],
  ])("lays out %s without node overlap", async (_name, nodes, edges) => {
    const layout = await layoutTopologyGraph(nodes, edges, "overview");
    expect(layout.nodes.size).toBe(nodes.length);
    assertNoNodeOverlap(layout);
    assertNodesInsideBounds(layout);
    for (const routed of layout.edges.values()) expect(routed.points.length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the same graph input deterministic", async () => {
    const nodes = [node("a"), node("b"), node("c"), node("d")];
    const edges = [edge("ab", "a", "b"), edge("ac", "a", "c"), edge("bd", "b", "d"), edge("cd", "c", "d")];
    const first = await layoutTopologyGraph(nodes, edges, "scope");
    const second = await layoutTopologyGraph(nodes, edges, "scope");
    expect(Array.from(second.nodes.entries())).toEqual(Array.from(first.nodes.entries()));
    expect(Array.from(second.edges.entries())).toEqual(Array.from(first.edges.entries()));
  });

  it("keeps overview as a centered region network and scope contents as a spaced template", () => {
    const overviewNodes = [node("center"), node("west"), node("east"), node("north"), node("south"), node("southeast")];
    const overviewEdges = [edge("center-west", "center", "west"), edge("center-east", "center", "east"), edge("center-north", "center", "north"), edge("center-south", "center", "south"), edge("center-southeast", "center", "southeast")];
    const overview = createRegionNetworkLayout(overviewNodes, overviewEdges);
    const hub = overview.nodes.get("center")!;
    expect(hub.x).toBeCloseTo(overview.width / 2);
    expect(hub.y).toBeCloseTo(overview.height / 2);
    assertNoNodeOverlap(overview);
    assertNodesInsideBounds(overview);

    const scope = createScopeTemplateLayout(overviewNodes, overviewEdges);
    expect(scope.nodes.size).toBe(overviewNodes.length);
    assertNoNodeOverlap(scope);
    assertNodesInsideBounds(scope);
  });

  it("keeps scope node bounds at CSS-pixel size when laid out for a real workspace", () => {
    const nodes = Array.from({ length: 6 }, (_, index) => ({ id: `facility-${index}`, width: 156, height: 58 }));
    const layout = createScopeTemplateLayout(nodes, [], { width: 720, height: 420 });
    expect(layout.width).toBe(720);
    expect(layout.height).toBe(420);
    expect(Array.from(layout.nodes.values()).every((current) => current.width === 156 && current.height === 58)).toBe(true);
    assertNoNodeOverlap(layout);
    assertNodesInsideBounds(layout);
  });

  it("fits logical graph bounds into the real viewport without overflow", () => {
    const fit = calculateTopologyFit(800, 500, 400, 300, 20);
    expect(fit.scale).toBeCloseTo(0.45);
    expect(fit.offsetX).toBeGreaterThanOrEqual(20);
    expect(fit.offsetY).toBeGreaterThanOrEqual(20);
    expect(fit.offsetX + 800 * fit.scale).toBeLessThanOrEqual(380.01);
    expect(fit.offsetY + 500 * fit.scale).toBeLessThanOrEqual(280.01);
  });

  it("terminates relation paths at node borders and labels the longest path segment", () => {
    const source = { id: "source", width: 100, height: 50, x: 100, y: 100 };
    const target = { id: "target", width: 120, height: 60, x: 360, y: 100 };
    const [sourcePoint, targetPoint] = topologyNodeBoundaryPoints(source, target);
    expect(sourcePoint).toEqual({ x: 150, y: 100 });
    expect(targetPoint).toEqual({ x: 300, y: 100 });
    expect(topologyLabelPoint([{ x: 0, y: 0 }, { x: 0, y: 80 }, { x: 260, y: 80 }, { x: 260, y: 100 }])).toEqual({ x: 130, y: 80 });
  });

  it("keeps multiple relations in deterministic visual lanes", () => {
    const nodes = [
      { id: "source", width: 124, height: 50 },
      { id: "target", width: 124, height: 50 },
    ];
    const edges = [edge("relation-b", "source", "target"), edge("relation-a", "source", "target")];
    const first = createGridTopologyLayout(nodes, edges);
    const second = createGridTopologyLayout(nodes, edges);
    expect(first.edges.get("relation-a")).toEqual(second.edges.get("relation-a"));
    expect(first.edges.get("relation-b")).toEqual(second.edges.get("relation-b"));
    expect(first.edges.get("relation-a")?.labelPoint.y).not.toBe(first.edges.get("relation-b")?.labelPoint.y);
    expect(topologyEdgeLaneOffset(0, 2)).toBe(-4);
    expect(topologyEdgeLaneOffset(1, 2)).toBe(4);
  });

  it("does not include portal rail items in the internal graph input", async () => {
    const internalNodes = [node("facility-a"), node("facility-b")];
    const internalEdges = [edge("internal", "facility-a", "facility-b")];
    const layout = await layoutTopologyGraph(internalNodes, internalEdges, "scope");
    expect(Array.from(layout.nodes.keys())).not.toContain("portal-to-south");
    expect(Array.from(layout.edges.keys())).not.toContain("portal-to-south");
  });
});
