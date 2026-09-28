export type TopologyLayoutMode = "overview" | "scope";

export type TopologyLayoutNode = {
  id: string;
  width: number;
  height: number;
};

export type TopologyLayoutEdge = {
  id: string;
  source: string;
  target: string;
};

export type TopologyLayoutPoint = {
  x: number;
  y: number;
};

export type TopologyLayoutNodePosition = TopologyLayoutNode & TopologyLayoutPoint;

export type TopologyLayoutEdgeResult = {
  id: string;
  points: TopologyLayoutPoint[];
  labelPoint: TopologyLayoutPoint;
};

export type TopologyLayoutResult = {
  width: number;
  height: number;
  nodes: Map<string, TopologyLayoutNodePosition>;
  edges: Map<string, TopologyLayoutEdgeResult>;
};

export type TopologyLayoutViewport = {
  width: number;
  height: number;
};

export type TopologyFit = {
  scale: number;
  offsetX: number;
  offsetY: number;
  viewportWidth: number;
  viewportHeight: number;
};

const DEFAULT_LOGICAL_WIDTH = 960;
const DEFAULT_LOGICAL_HEIGHT = 580;
const GRAPH_PADDING = 32;
const EDGE_LANE_GAP = 8;
const ROUTE_CLEARANCE = 18;

function pointEquals(left: TopologyLayoutPoint, right: TopologyLayoutPoint): boolean {
  return Math.abs(left.x - right.x) < 0.1 && Math.abs(left.y - right.y) < 0.1;
}

function withoutDuplicatePoints(points: TopologyLayoutPoint[]): TopologyLayoutPoint[] {
  return points.filter((point, index) => index === 0 || !pointEquals(point, points[index - 1]));
}

function pointDistance(left: TopologyLayoutPoint, right: TopologyLayoutPoint): number {
  return Math.hypot(right.x - left.x, right.y - left.y);
}

/** Pick the middle of the longest actual path segment so labels remain readable on elbow routes. */
export function topologyLabelPoint(points: TopologyLayoutPoint[]): TopologyLayoutPoint {
  const cleanPoints = withoutDuplicatePoints(points);
  if (cleanPoints.length === 0) return { x: 0, y: 0 };
  if (cleanPoints.length === 1) return cleanPoints[0];
  let bestIndex = 0;
  let bestLength = -1;
  for (let index = 0; index < cleanPoints.length - 1; index += 1) {
    const length = pointDistance(cleanPoints[index], cleanPoints[index + 1]);
    if (length > bestLength) {
      bestLength = length;
      bestIndex = index;
    }
  }
  const start = cleanPoints[bestIndex];
  const end = cleanPoints[bestIndex + 1];
  return { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
}

function boundaryPoint(node: TopologyLayoutNodePosition, toward: TopologyLayoutPoint): TopologyLayoutPoint {
  const dx = toward.x - node.x;
  const dy = toward.y - node.y;
  if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) return { x: node.x + node.width / 2, y: node.y };
  const scaleX = Math.abs(dx) < 0.001 ? Number.POSITIVE_INFINITY : node.width / 2 / Math.abs(dx);
  const scaleY = Math.abs(dy) < 0.001 ? Number.POSITIVE_INFINITY : node.height / 2 / Math.abs(dy);
  const scale = Math.min(scaleX, scaleY);
  return { x: node.x + dx * scale, y: node.y + dy * scale };
}

/** Return source/target points on the two node rectangles, with an optional deterministic lane offset. */
export function topologyNodeBoundaryPoints(source: TopologyLayoutNodePosition, target: TopologyLayoutNodePosition, laneOffset = 0): [TopologyLayoutPoint, TopologyLayoutPoint] {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const length = Math.hypot(dx, dy);
  if (length < 0.001) return [{ x: source.x + source.width / 2, y: source.y }, { x: target.x - target.width / 2, y: target.y }];
  const normal = { x: -dy / length, y: dx / length };
  const offset = { x: normal.x * laneOffset, y: normal.y * laneOffset };
  return [
    boundaryPoint(source, { x: target.x + offset.x, y: target.y + offset.y }),
    boundaryPoint(target, { x: source.x + offset.x, y: source.y + offset.y }),
  ];
}

export function topologyEdgeLaneOffset(index: number, count: number, gap = EDGE_LANE_GAP): number {
  return (index - (count - 1) / 2) * gap;
}

function segmentIntersectsNodeInterior(start: TopologyLayoutPoint, end: TopologyLayoutPoint, node: TopologyLayoutNodePosition): boolean {
  const left = node.x - node.width / 2 + 0.5;
  const right = node.x + node.width / 2 - 0.5;
  const top = node.y - node.height / 2 + 0.5;
  const bottom = node.y + node.height / 2 - 0.5;
  let tMin = 0;
  let tMax = 1;
  const clip = (origin: number, direction: number, min: number, max: number): boolean => {
    if (Math.abs(direction) < 0.001) return origin > min && origin < max;
    const reciprocal = 1 / direction;
    let near = (min - origin) * reciprocal;
    let far = (max - origin) * reciprocal;
    if (near > far) [near, far] = [far, near];
    tMin = Math.max(tMin, near);
    tMax = Math.min(tMax, far);
    return tMin < tMax;
  };
  return clip(start.x, end.x - start.x, left, right) && clip(start.y, end.y - start.y, top, bottom) && tMax > 0.001 && tMin < 0.999;
}

function pathIntersectionCount(points: TopologyLayoutPoint[], sourceId: string, targetId: string, nodes: Map<string, TopologyLayoutNodePosition>): number {
  let intersections = 0;
  for (const [id, node] of nodes) {
    if (id === sourceId || id === targetId) continue;
    if (points.some((point, index) => index > 0 && segmentIntersectsNodeInterior(points[index - 1], point, node))) intersections += 1;
  }
  return intersections;
}

function pathLength(points: TopologyLayoutPoint[]): number {
  return points.slice(1).reduce((total, point, index) => total + pointDistance(points[index], point), 0);
}

function orthogonalCandidates(source: TopologyLayoutNodePosition, target: TopologyLayoutNodePosition, laneOffset: number): TopologyLayoutPoint[][] {
  const xValues = [
    (source.x + target.x) / 2 + laneOffset,
    source.x - source.width / 2 - ROUTE_CLEARANCE + laneOffset,
    source.x + source.width / 2 + ROUTE_CLEARANCE + laneOffset,
    target.x - target.width / 2 - ROUTE_CLEARANCE + laneOffset,
    target.x + target.width / 2 + ROUTE_CLEARANCE + laneOffset,
  ];
  const yValues = [
    (source.y + target.y) / 2 + laneOffset,
    source.y - source.height / 2 - ROUTE_CLEARANCE + laneOffset,
    source.y + source.height / 2 + ROUTE_CLEARANCE + laneOffset,
    target.y - target.height / 2 - ROUTE_CLEARANCE + laneOffset,
    target.y + target.height / 2 + ROUTE_CLEARANCE + laneOffset,
  ];
  const candidates: TopologyLayoutPoint[][] = [];
  for (const x of xValues) {
    const sourceControl = { x, y: source.y };
    const targetControl = { x, y: target.y };
    candidates.push(withoutDuplicatePoints([boundaryPoint(source, sourceControl), sourceControl, targetControl, boundaryPoint(target, targetControl)]));
  }
  for (const y of yValues) {
    const sourceControl = { x: source.x, y };
    const targetControl = { x: target.x, y };
    candidates.push(withoutDuplicatePoints([boundaryPoint(source, sourceControl), sourceControl, targetControl, boundaryPoint(target, targetControl)]));
  }
  return candidates;
}

function routedPoints(source: TopologyLayoutNodePosition, target: TopologyLayoutNodePosition, sourceId: string, targetId: string, laneOffset: number, nodes: Map<string, TopologyLayoutNodePosition>): TopologyLayoutPoint[] {
  const [sourcePoint, targetPoint] = topologyNodeBoundaryPoints(source, target, laneOffset);
  const direct = withoutDuplicatePoints([sourcePoint, targetPoint]);
  const candidates = [direct, ...orthogonalCandidates(source, target, laneOffset)];
  return candidates.map((points, order) => ({
    points,
    order,
    intersections: pathIntersectionCount(points, sourceId, targetId, nodes),
    bends: Math.max(0, points.length - 2),
    length: pathLength(points),
  })).sort((left, right) => left.intersections - right.intersections || left.bends - right.bends || left.length - right.length || left.order - right.order)[0].points;
}

function edgePairKey(edge: TopologyLayoutEdge): string {
  return [edge.source, edge.target].sort((left, right) => left.localeCompare(right)).join("\u0000");
}

function edgeResults(edges: TopologyLayoutEdge[], nodes: Map<string, TopologyLayoutNodePosition>): Map<string, TopologyLayoutEdgeResult> {
  const groups = new Map<string, TopologyLayoutEdge[]>();
  edges.forEach((edge) => {
    if (!nodes.has(edge.source) || !nodes.has(edge.target)) return;
    const key = edgePairKey(edge);
    groups.set(key, [...(groups.get(key) ?? []), edge]);
  });
  return new Map(Array.from(groups.values()).flatMap((group) => {
    const orderedGroup = [...group].sort((left, right) => left.id.localeCompare(right.id));
    return orderedGroup.flatMap((edge, index) => {
      const source = nodes.get(edge.source);
      const target = nodes.get(edge.target);
      if (!source || !target) return [];
      const points = routedPoints(source, target, edge.source, edge.target, topologyEdgeLaneOffset(index, orderedGroup.length), nodes);
      return [[edge.id, { id: edge.id, points, labelPoint: topologyLabelPoint(points) }] as const];
    });
  }));
}

function fromCenterPositions(nodes: TopologyLayoutNode[], edges: TopologyLayoutEdge[], centers: Map<string, TopologyLayoutPoint>, minWidth: number, minHeight: number, padding = 48): TopologyLayoutResult {
  if (nodes.length === 0) return { width: minWidth, height: minHeight, nodes: new Map(), edges: new Map() };
  const positioned = nodes.map((node) => ({ node, center: centers.get(node.id) ?? { x: 0, y: 0 } }));
  const left = Math.min(...positioned.map(({ node, center }) => center.x - node.width / 2));
  const right = Math.max(...positioned.map(({ node, center }) => center.x + node.width / 2));
  const top = Math.min(...positioned.map(({ node, center }) => center.y - node.height / 2));
  const bottom = Math.max(...positioned.map(({ node, center }) => center.y + node.height / 2));
  const contentWidth = right - left;
  const contentHeight = bottom - top;
  const width = Math.max(minWidth, contentWidth + padding * 2);
  const height = Math.max(minHeight, contentHeight + padding * 2);
  const offsetX = (width - contentWidth) / 2 - left;
  const offsetY = (height - contentHeight) / 2 - top;
  const layoutNodes = new Map(positioned.map(({ node, center }) => [node.id, {
    ...node,
    x: center.x + offsetX,
    y: center.y + offsetY,
  }]));
  return { width, height, nodes: layoutNodes, edges: edgeResults(edges, layoutNodes) };
}

/** A stable, undirected-looking map layout for scope-level network overviews. */
export function createRegionNetworkLayout(nodes: TopologyLayoutNode[], edges: TopologyLayoutEdge[]): TopologyLayoutResult {
  const orderedNodes = [...nodes].sort((left, right) => left.id.localeCompare(right.id));
  if (orderedNodes.length === 0) return fromCenterPositions(nodes, edges, new Map(), 820, 500);
  const degree = new Map(orderedNodes.map((node) => [node.id, 0]));
  edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  });
  const hub = [...orderedNodes].sort((left, right) => (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0) || left.id.localeCompare(right.id))[0];
  const outerNodes = orderedNodes.filter((node) => node.id !== hub.id);
  const centers = new Map<string, TopologyLayoutPoint>([[hub.id, { x: 0, y: 0 }]]);
  const radiusX = Math.max(230, Math.min(320, 190 + orderedNodes.length * 18));
  const radiusY = Math.max(150, Math.min(195, 105 + orderedNodes.length * 14));
  outerNodes.forEach((node, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / Math.max(outerNodes.length, 1);
    centers.set(node.id, { x: Math.cos(angle) * radiusX, y: Math.sin(angle) * radiusY });
  });
  const layout = fromCenterPositions(orderedNodes, edges, centers, 820, 500, 52);
  const laidOutHub = layout.nodes.get(hub.id);
  if (!laidOutHub) return layout;
  const shiftX = layout.width / 2 - laidOutHub.x;
  const shiftY = layout.height / 2 - laidOutHub.y;
  const centredNodes = new Map(Array.from(layout.nodes.entries()).map(([id, node]) => [id, { ...node, x: node.x + shiftX, y: node.y + shiftY }]));
  return { ...layout, nodes: centredNodes, edges: edgeResults(edges, centredNodes) };
}

/** A deliberate compact template for facilities inside one scope. */
export function createScopeTemplateLayout(nodes: TopologyLayoutNode[], edges: TopologyLayoutEdge[], viewport?: TopologyLayoutViewport): TopologyLayoutResult {
  const orderedNodes = [...nodes].sort((left, right) => left.id.localeCompare(right.id));
  if (viewport) {
    const maxNodeWidth = Math.max(...orderedNodes.map((node) => node.width), 0);
    const maxNodeHeight = Math.max(...orderedNodes.map((node) => node.height), 0);
    const safeWidth = Math.max(viewport.width, maxNodeWidth + 56);
    const safeHeight = Math.max(viewport.height, maxNodeHeight + 52);
    const horizontalPadding = 28;
    const verticalPadding = 26;
    const left = horizontalPadding + maxNodeWidth / 2;
    const right = Math.max(left, safeWidth - horizontalPadding - maxNodeWidth / 2);
    const top = verticalPadding + maxNodeHeight / 2;
    const bottom = Math.max(top, safeHeight - verticalPadding - maxNodeHeight / 2);
    const fractions = scopeTemplateFractions(orderedNodes.length);
    const centers = new Map(orderedNodes.map((node, index) => {
      const fraction = fractions[index] ?? { x: 0.5, y: 0.5 };
      return [node.id, { x: left + (right - left) * fraction.x, y: top + (bottom - top) * fraction.y }];
    }));
    return fromCenterPositions(orderedNodes, edges, centers, safeWidth, safeHeight, 0);
  }
  const centers = new Map<string, TopologyLayoutPoint>();
  const templates: Record<number, TopologyLayoutPoint[]> = {
    1: [{ x: 0, y: 0 }],
    2: [{ x: -220, y: 0 }, { x: 220, y: 0 }],
    3: [{ x: 0, y: -165 }, { x: -230, y: 170 }, { x: 230, y: 170 }],
    4: [{ x: -220, y: -140 }, { x: 220, y: -140 }, { x: -220, y: 165 }, { x: 220, y: 165 }],
    5: [{ x: 0, y: -200 }, { x: -245, y: 0 }, { x: 245, y: 0 }, { x: -145, y: 205 }, { x: 145, y: 205 }],
    6: [{ x: -320, y: -155 }, { x: 0, y: -155 }, { x: 320, y: -155 }, { x: -320, y: 185 }, { x: 0, y: 185 }, { x: 320, y: 185 }],
  };
  const template = templates[orderedNodes.length];
  if (template) {
    orderedNodes.forEach((node, index) => centers.set(node.id, template[index]));
  } else {
    const columns = Math.max(2, Math.ceil(Math.sqrt(orderedNodes.length)));
    orderedNodes.forEach((node, index) => centers.set(node.id, {
      x: (index % columns) * 260,
      y: Math.floor(index / columns) * 180,
    }));
  }
  return fromCenterPositions(orderedNodes, edges, centers, 720, orderedNodes.length <= 1 ? 320 : 480, 58);
}

function scopeTemplateFractions(nodeCount: number): TopologyLayoutPoint[] {
  const templates: Record<number, TopologyLayoutPoint[]> = {
    1: [{ x: 0.5, y: 0.5 }],
    2: [{ x: 0, y: 0.5 }, { x: 1, y: 0.5 }],
    3: [{ x: 0.5, y: 0.24 }, { x: 0, y: 0.76 }, { x: 1, y: 0.76 }],
    4: [{ x: 0, y: 0.24 }, { x: 1, y: 0.24 }, { x: 0, y: 0.76 }, { x: 1, y: 0.76 }],
    5: [{ x: 0.5, y: 0.16 }, { x: 0, y: 0.5 }, { x: 1, y: 0.5 }, { x: 0.25, y: 0.84 }, { x: 0.75, y: 0.84 }],
    6: [{ x: 0, y: 0.24 }, { x: 0.5, y: 0.24 }, { x: 1, y: 0.24 }, { x: 0, y: 0.76 }, { x: 0.5, y: 0.76 }, { x: 1, y: 0.76 }],
  };
  if (templates[nodeCount]) return templates[nodeCount];
  const columns = Math.max(2, Math.ceil(Math.sqrt(Math.max(nodeCount, 1))));
  const rows = Math.max(1, Math.ceil(nodeCount / columns));
  return Array.from({ length: nodeCount }, (_, index) => ({
    x: columns === 1 ? 0.5 : (index % columns) / (columns - 1),
    y: rows === 1 ? 0.5 : Math.floor(index / columns) / (rows - 1),
  }));
}

function gridColumns(nodeCount: number): number {
  return Math.max(1, Math.ceil(Math.sqrt(Math.max(nodeCount, 1))));
}

export function createGridTopologyLayout(nodes: TopologyLayoutNode[], edges: TopologyLayoutEdge[]): TopologyLayoutResult {
  const orderedNodes = [...nodes].sort((left, right) => left.id.localeCompare(right.id));
  const columns = gridColumns(orderedNodes.length);
  const cellWidth = Math.max(...orderedNodes.map((node) => node.width), 128) + 72;
  const cellHeight = Math.max(...orderedNodes.map((node) => node.height), 52) + 72;
  const rows = Math.max(1, Math.ceil(orderedNodes.length / columns));
  const width = Math.max(DEFAULT_LOGICAL_WIDTH, GRAPH_PADDING * 2 + columns * cellWidth);
  const height = Math.max(DEFAULT_LOGICAL_HEIGHT, GRAPH_PADDING * 2 + rows * cellHeight);
  const layoutNodes = new Map(orderedNodes.map((node, index) => [node.id, orderedNodes.length === 1 ? {
    ...node,
    x: width / 2,
    y: height / 2,
  } : {
    ...node,
    x: GRAPH_PADDING + node.width / 2 + (index % columns) * cellWidth,
    y: GRAPH_PADDING + node.height / 2 + Math.floor(index / columns) * cellHeight,
  }]));
  return { width, height, nodes: layoutNodes, edges: edgeResults(edges, layoutNodes) };
}

export async function layoutTopologyGraph(nodes: TopologyLayoutNode[], edges: TopologyLayoutEdge[], mode: TopologyLayoutMode): Promise<TopologyLayoutResult> {
  // Runtime canvases use the stable semantic templates below. Keep this helper as a
  // deterministic compatibility entry point for callers that still request a generic layout.
  void mode;
  return createGridTopologyLayout(nodes, edges);
}

export function createNeighborhoodTopologyLayout(nodes: TopologyLayoutNode[], edges: TopologyLayoutEdge[], focusId: string): TopologyLayoutResult {
  const focus = nodes.find((node) => node.id === focusId);
  if (!focus) return createGridTopologyLayout(nodes, edges);
  const centers = new Map<string, TopologyLayoutPoint>([[focus.id, { x: 0, y: 0 }]]);
  const neighbors = nodes.filter((node) => node.id !== focusId).sort((left, right) => left.id.localeCompare(right.id));
  const customPositions: Record<number, TopologyLayoutPoint[]> = {
    2: [{ x: 0, y: -142 }, { x: 0, y: 142 }],
    3: [{ x: -150, y: -112 }, { x: 150, y: -112 }, { x: 0, y: 142 }],
    4: [{ x: 0, y: -142 }, { x: 170, y: 0 }, { x: 0, y: 142 }, { x: -170, y: 0 }],
  };
  const positions = customPositions[neighbors.length];
  neighbors.forEach((node, index) => {
    const position = positions?.[index] ?? {
      x: Math.cos(-Math.PI / 2 + (Math.PI * 2 * index) / Math.max(neighbors.length, 1)) * Math.min(190, Math.max(150, 128 + neighbors.length * 8)),
      y: Math.sin(-Math.PI / 2 + (Math.PI * 2 * index) / Math.max(neighbors.length, 1)) * Math.min(190, Math.max(150, 128 + neighbors.length * 8)),
    };
    centers.set(node.id, position);
  });
  return fromCenterPositions(nodes, edges, centers, 760, 480, 52);
}

export function calculateTopologyFit(graphWidth: number, graphHeight: number, viewportWidth: number, viewportHeight: number, padding = 24): TopologyFit {
  const safeGraphWidth = Math.max(graphWidth, 1);
  const safeGraphHeight = Math.max(graphHeight, 1);
  const safeViewportWidth = Math.max(viewportWidth, padding * 2 + 1);
  const safeViewportHeight = Math.max(viewportHeight, padding * 2 + 1);
  const scale = Math.min(1, (safeViewportWidth - padding * 2) / safeGraphWidth, (safeViewportHeight - padding * 2) / safeGraphHeight);
  return {
    scale,
    offsetX: (safeViewportWidth - safeGraphWidth * scale) / 2,
    offsetY: (safeViewportHeight - safeGraphHeight * scale) / 2,
    viewportWidth: safeViewportWidth,
    viewportHeight: safeViewportHeight,
  };
}

export function topologyPointsToPath(points: TopologyLayoutPoint[]): string {
  if (points.length === 0) return "";
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
}
