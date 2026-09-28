import type { JsonObject } from "./editor";

export type TopologyScopeSummary = {
  key: string;
  name: string;
  nodeTypeKey: string;
  description: string;
  internalNodeKeys: string[];
  internalNodeCount: number;
  externalConnectionCount: number;
  neighborScopeKeys: string[];
  factCount: number;
};

export type TopologyEntity = {
  key: string;
  name: string;
  nodeTypeKey: string;
  nodeTypeName: string;
  description: string;
  scopeKeys: string[];
  relationCount: number;
  factCount: number;
};

export type TopologyRelation = {
  key: string;
  sourceNodeKey: string;
  sourceNodeName: string;
  relationTypeKey: string;
  targetNodeKey: string;
  targetNodeName: string;
  relationScope: "INTERNAL" | "CROSS_SCOPE_BUSINESS";
};

export type NavigableBoundaryConnection = {
  kind: "NAVIGABLE_BOUNDARY";
  key: string;
  sourceScopeKey: string;
  targetScopeKey: string;
  relationKeys: string[];
  relationTypeKeys: string[];
  transportNodeKeys: string[];
};

export type TopologyPortal = NavigableBoundaryConnection & {
  neighborScopeKey: string;
  neighborScopeName: string;
  relationSummaries: string[];
  transportNodeNames: string[];
};

export type ScopeOverviewProjection = {
  configured: boolean;
  scopes: TopologyScopeSummary[];
  boundaryConnections: NavigableBoundaryConnection[];
};

export type ScopeTopologyProjection = {
  configured: boolean;
  scope: TopologyScopeSummary | null;
  nodes: TopologyEntity[];
  relations: TopologyRelation[];
  portals: TopologyPortal[];
};

export type EntityNeighborhoodProjection = {
  configured: boolean;
  entity: TopologyEntity | null;
  nodes: TopologyEntity[];
  relations: TopologyRelation[];
};

type RawNode = JsonObject & { key: string };
type RawRelation = JsonObject & {
  key: string;
  sourceNodeKey: string;
  relationTypeKey: string;
  targetNodeKey: string;
};

type TopologyIndex = {
  configured: boolean;
  regionNodeTypeKey: string | null;
  facilityNodeTypeKey: string | null;
  transportNodeTypeKey: string | null;
  locatedInRelationTypeKey: string | null;
  endpointRelationTypeKey: string | null;
  nodes: RawNode[];
  nodeByKey: Map<string, RawNode>;
  relations: RawRelation[];
  relationByKey: Map<string, RawRelation>;
  nodeTypeNames: Map<string, string>;
  memberships: Map<string, Set<string>>;
  endpointScopes: Map<string, Set<string>>;
  scopeKeys: Set<string>;
  relationCounts: Map<string, number>;
};

function asObject(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function asObjectArray(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function displayName(value: JsonObject, fallback: string): string {
  return stringValue(value.name, fallback).trim() || fallback;
}

function sortedUnique(values: Iterable<string>): string[] {
  return Array.from(new Set(values)).sort((left, right) => left.localeCompare(right));
}

function relationIdentity(value: JsonObject, index: number): string {
  const explicit = stringValue(value.key).trim();
  if (explicit) return explicit;
  return `${stringValue(value.source_node_key)}__${stringValue(value.relation_type_key)}__${stringValue(value.target_node_key)}__${index}`;
}

function relationLabel(value: string): string {
  return value.replaceAll("_", " ");
}

function buildIndex(document: Record<string, unknown>): TopologyIndex {
  const metadata = asObject(document.metadata);
  const locality = asObject(metadata.locality);
  const world = asObject(document.world);
  const regionNodeTypeKey = stringValue(locality.region_node_type_key).trim() || null;
  const facilityNodeTypeKey = stringValue(locality.facility_node_type_key).trim() || null;
  const transportNodeTypeKey = stringValue(locality.transport_node_type_key).trim() || null;
  const locatedInRelationTypeKey = stringValue(locality.located_in_relation_type_key).trim() || null;
  const endpointRelationTypeKey = stringValue(locality.transport_endpoint_relation_type_key).trim() || null;
  const configured = locality.enabled === true && Boolean(regionNodeTypeKey && locatedInRelationTypeKey);
  const nodes: RawNode[] = asObjectArray(world.nodes).flatMap((value, index) => {
    const key = stringValue(value.key).trim();
    return key ? [{ ...value, key }] : [{ ...value, key: `node-${index}` }];
  });
  const nodeByKey = new Map(nodes.map((node) => [node.key, node]));
  const nodeTypeNames = new Map(asObjectArray(world.node_types).flatMap((value) => {
    const key = stringValue(value.key).trim();
    return key ? [[key, displayName(value, key)] as const] : [];
  }));
  const relations = asObjectArray(world.relations).flatMap((value, index) => {
    const sourceNodeKey = stringValue(value.source_node_key).trim();
    const relationTypeKey = stringValue(value.relation_type_key).trim();
    const targetNodeKey = stringValue(value.target_node_key).trim();
    if (!sourceNodeKey || !relationTypeKey || !targetNodeKey) return [];
    return [{ ...value, key: relationIdentity(value, index), sourceNodeKey, relationTypeKey, targetNodeKey }];
  });
  const relationByKey = new Map(relations.map((relation) => [relation.key, relation]));
  const scopeKeys = new Set(nodes.filter((node) => node.node_type_key === regionNodeTypeKey).map((node) => node.key));
  const memberships = new Map<string, Set<string>>();
  const endpointScopes = new Map<string, Set<string>>();
  const addMapping = (map: Map<string, Set<string>>, nodeKey: string, scopeKey: string) => {
    if (!map.has(nodeKey)) map.set(nodeKey, new Set());
    map.get(nodeKey)!.add(scopeKey);
  };
  relations.forEach((relation) => {
    if (relation.relationTypeKey === locatedInRelationTypeKey) {
      if (scopeKeys.has(relation.targetNodeKey)) addMapping(memberships, relation.sourceNodeKey, relation.targetNodeKey);
      if (scopeKeys.has(relation.sourceNodeKey)) addMapping(memberships, relation.targetNodeKey, relation.sourceNodeKey);
    }
    if (relation.relationTypeKey === endpointRelationTypeKey) {
      if (scopeKeys.has(relation.targetNodeKey)) addMapping(endpointScopes, relation.sourceNodeKey, relation.targetNodeKey);
      if (scopeKeys.has(relation.sourceNodeKey)) addMapping(endpointScopes, relation.targetNodeKey, relation.sourceNodeKey);
    }
  });
  const relationCounts = new Map<string, number>();
  relations.forEach((relation) => {
    relationCounts.set(relation.sourceNodeKey, (relationCounts.get(relation.sourceNodeKey) ?? 0) + 1);
    relationCounts.set(relation.targetNodeKey, (relationCounts.get(relation.targetNodeKey) ?? 0) + 1);
  });
  return {
    configured,
    regionNodeTypeKey,
    facilityNodeTypeKey,
    transportNodeTypeKey,
    locatedInRelationTypeKey,
    endpointRelationTypeKey,
    nodes,
    nodeByKey,
    relations,
    relationByKey,
    nodeTypeNames,
    memberships,
    endpointScopes,
    scopeKeys,
    relationCounts,
  };
}

function nodeScopeKeys(index: TopologyIndex, nodeKey: string): string[] {
  if (index.scopeKeys.has(nodeKey)) return [nodeKey];
  const membership = index.memberships.get(nodeKey);
  if (membership && membership.size > 0) return sortedUnique(membership);
  const endpoints = index.endpointScopes.get(nodeKey);
  return endpoints ? sortedUnique(endpoints) : [];
}

function entity(index: TopologyIndex, node: RawNode): TopologyEntity {
  const facts = asObjectArray(node.facts);
  return {
    key: node.key,
    name: displayName(node, node.key),
    nodeTypeKey: stringValue(node.node_type_key, "unknown"),
    nodeTypeName: index.nodeTypeNames.get(stringValue(node.node_type_key)) ?? stringValue(node.node_type_key, "未知类型"),
    description: stringValue(node.description),
    scopeKeys: nodeScopeKeys(index, node.key),
    relationCount: index.relationCounts.get(node.key) ?? 0,
    factCount: facts.length,
  };
}

function topologyRelation(index: TopologyIndex, relation: RawRelation, relationScope: TopologyRelation["relationScope"]): TopologyRelation {
  const source = index.nodeByKey.get(relation.sourceNodeKey);
  const target = index.nodeByKey.get(relation.targetNodeKey);
  return {
    key: relation.key,
    sourceNodeKey: relation.sourceNodeKey,
    sourceNodeName: source ? displayName(source, relation.sourceNodeKey) : relation.sourceNodeKey,
    relationTypeKey: relation.relationTypeKey,
    targetNodeKey: relation.targetNodeKey,
    targetNodeName: target ? displayName(target, relation.targetNodeKey) : relation.targetNodeKey,
    relationScope,
  };
}

function businessRelationScope(index: TopologyIndex, relation: RawRelation): TopologyRelation["relationScope"] {
  const sourceScopes = nodeScopeKeys(index, relation.sourceNodeKey);
  const targetScopes = nodeScopeKeys(index, relation.targetNodeKey);
  return sourceScopes.some((sourceScope) => targetScopes.some((targetScope) => sourceScope !== targetScope)) ? "CROSS_SCOPE_BUSINESS" : "INTERNAL";
}

function buildBoundaryConnections(index: TopologyIndex): NavigableBoundaryConnection[] {
  const connections = new Map<string, { sourceScopeKey: string; targetScopeKey: string; relationKeys: Set<string>; relationTypeKeys: Set<string>; transportNodeKeys: Set<string> }>();
  const add = (left: string, right: string, relation: RawRelation | null, transportNodeKey: string | null) => {
    if (!left || !right || left === right) return;
    const [sourceScopeKey, targetScopeKey] = [left, right].sort((a, b) => a.localeCompare(b));
    const key = `${sourceScopeKey}__${targetScopeKey}`;
    if (!connections.has(key)) connections.set(key, { sourceScopeKey, targetScopeKey, relationKeys: new Set(), relationTypeKeys: new Set(), transportNodeKeys: new Set() });
    const connection = connections.get(key)!;
    if (relation) {
      connection.relationKeys.add(relation.key);
      connection.relationTypeKeys.add(relation.relationTypeKey);
    }
    if (transportNodeKey) connection.transportNodeKeys.add(transportNodeKey);
  };
  index.endpointScopes.forEach((scopes, transportNodeKey) => {
    const values = sortedUnique(scopes);
    for (let leftIndex = 0; leftIndex < values.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < values.length; rightIndex += 1) {
        const endpointRelations = index.relations.filter((relation) => relation.relationTypeKey === index.endpointRelationTypeKey && (relation.sourceNodeKey === transportNodeKey || relation.targetNodeKey === transportNodeKey) && (relation.sourceNodeKey === values[leftIndex] || relation.targetNodeKey === values[leftIndex] || relation.sourceNodeKey === values[rightIndex] || relation.targetNodeKey === values[rightIndex]));
        endpointRelations.forEach((relation) => add(values[leftIndex], values[rightIndex], relation, transportNodeKey));
      }
    }
  });
  return Array.from(connections.values()).map((connection) => ({
    kind: "NAVIGABLE_BOUNDARY" as const,
    key: `${connection.sourceScopeKey}__${connection.targetScopeKey}`,
    sourceScopeKey: connection.sourceScopeKey,
    targetScopeKey: connection.targetScopeKey,
    relationKeys: sortedUnique(connection.relationKeys),
    relationTypeKeys: sortedUnique(connection.relationTypeKeys),
    transportNodeKeys: sortedUnique(connection.transportNodeKeys),
  })).sort((left, right) => left.key.localeCompare(right.key));
}

function scopeSummary(index: TopologyIndex, scopeKey: string, connections: NavigableBoundaryConnection[]): TopologyScopeSummary | null {
  const scope = index.nodeByKey.get(scopeKey);
  if (!scope || !index.scopeKeys.has(scopeKey)) return null;
  const internalNodeKeys = index.nodes.filter((node) => node.key !== scopeKey && index.memberships.get(node.key)?.has(scopeKey)).map((node) => node.key).sort((left, right) => left.localeCompare(right));
  const externalConnections = connections.filter((connection) => connection.sourceScopeKey === scopeKey || connection.targetScopeKey === scopeKey);
  return {
    key: scopeKey,
    name: displayName(scope, scopeKey),
    nodeTypeKey: stringValue(scope.node_type_key, "unknown"),
    description: stringValue(scope.description),
    internalNodeKeys,
    internalNodeCount: internalNodeKeys.length,
    externalConnectionCount: externalConnections.length,
    neighborScopeKeys: sortedUnique(externalConnections.map((connection) => connection.sourceScopeKey === scopeKey ? connection.targetScopeKey : connection.sourceScopeKey)),
    factCount: asObjectArray(scope.facts).length,
  };
}

function portalForConnection(index: TopologyIndex, scopeKey: string, connection: NavigableBoundaryConnection): TopologyPortal {
  const neighborScopeKey = connection.sourceScopeKey === scopeKey ? connection.targetScopeKey : connection.sourceScopeKey;
  const neighbor = index.nodeByKey.get(neighborScopeKey);
  const relationSummaries = connection.relationTypeKeys.map(relationLabel);
  const transportNodeNames = connection.transportNodeKeys.map((key) => {
    const node = index.nodeByKey.get(key);
    return node ? displayName(node, key) : key;
  });
  return {
    ...connection,
    neighborScopeKey,
    neighborScopeName: neighbor ? displayName(neighbor, neighborScopeKey) : neighborScopeKey,
    relationSummaries,
    transportNodeNames,
  };
}

export function buildScopeOverview(document: Record<string, unknown>): ScopeOverviewProjection {
  const index = buildIndex(document);
  if (!index.configured) return { configured: false, scopes: [], boundaryConnections: [] };
  const boundaryConnections = buildBoundaryConnections(index);
  return {
    configured: true,
    scopes: Array.from(index.scopeKeys).sort((left, right) => left.localeCompare(right)).flatMap((key) => {
      const summary = scopeSummary(index, key, boundaryConnections);
      return summary ? [summary] : [];
    }),
    boundaryConnections,
  };
}

export function buildScopeTopology(document: Record<string, unknown>, scopeKey: string): ScopeTopologyProjection {
  const index = buildIndex(document);
  if (!index.configured) return { configured: false, scope: null, nodes: [], relations: [], portals: [] };
  const boundaryConnections = buildBoundaryConnections(index);
  const scope = scopeSummary(index, scopeKey, boundaryConnections);
  if (!scope) return { configured: true, scope: null, nodes: [], relations: [], portals: [] };
  const internalNodeKeys = new Set(scope.internalNodeKeys);
  const nodes = index.nodes.filter((node) => internalNodeKeys.has(node.key)).map((node) => entity(index, node));
  const relations = index.relations.filter((relation) => internalNodeKeys.has(relation.sourceNodeKey) && internalNodeKeys.has(relation.targetNodeKey) && relation.relationTypeKey !== index.locatedInRelationTypeKey && relation.relationTypeKey !== index.endpointRelationTypeKey).map((relation) => topologyRelation(index, relation, "INTERNAL"));
  const portals = boundaryConnections.filter((connection) => connection.sourceScopeKey === scopeKey || connection.targetScopeKey === scopeKey).map((connection) => portalForConnection(index, scopeKey, connection));
  return { configured: true, scope, nodes, relations, portals };
}

export function buildEntityNeighborhood(document: Record<string, unknown>, entityKey: string): EntityNeighborhoodProjection {
  const index = buildIndex(document);
  if (!index.configured) return { configured: false, entity: null, nodes: [], relations: [] };
  const selectedNode = index.nodeByKey.get(entityKey);
  if (!selectedNode) return { configured: true, entity: null, nodes: [], relations: [] };
  const relations = index.relations.filter((relation) => (relation.sourceNodeKey === entityKey || relation.targetNodeKey === entityKey) && relation.relationTypeKey !== index.locatedInRelationTypeKey && relation.relationTypeKey !== index.endpointRelationTypeKey).map((relation) => topologyRelation(index, relation, businessRelationScope(index, relation)));
  const nodeKeys = new Set<string>([entityKey]);
  relations.forEach((relation) => {
    nodeKeys.add(relation.sourceNodeKey);
    nodeKeys.add(relation.targetNodeKey);
  });
  const nodes = index.nodes.filter((node) => nodeKeys.has(node.key)).map((node) => entity(index, node));
  return { configured: true, entity: entity(index, selectedNode), nodes, relations };
}

export function findScopeForNode(document: Record<string, unknown>, nodeKey: string): string | null {
  const index = buildIndex(document);
  const scopes = nodeScopeKeys(index, nodeKey);
  return scopes.length === 1 ? scopes[0] : scopes[0] ?? null;
}

export function nodeByTopologyKey(document: Record<string, unknown>, nodeKey: string): TopologyEntity | null {
  const index = buildIndex(document);
  const node = index.nodeByKey.get(nodeKey);
  return node ? entity(index, node) : null;
}

export function relationByTopologyKey(document: Record<string, unknown>, relationKey: string): TopologyRelation | null {
  const index = buildIndex(document);
  const relation = index.relationByKey.get(relationKey);
  return relation ? topologyRelation(index, relation, businessRelationScope(index, relation)) : null;
}
