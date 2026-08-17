import type { ActionLocation, PlayerGameState } from "./types";

export type DisplayNode = PlayerGameState["visible_nodes"][number];
export type DisplayFact = PlayerGameState["known_facts"][number];
export type DisplayResource = PlayerGameState["resources"][number];

export type RegionGroup<T> = {
  key: string;
  name: string;
  items: T[];
};

export type FactRegionGroup = {
  key: string;
  name: string;
  facts: DisplayFact[];
};

const FALLBACK_REGION_KEY = "__all__";

export function groupResourcesByRegion(resources: DisplayResource[]): RegionGroup<DisplayResource>[] {
  const groups = new Map<string, RegionGroup<DisplayResource>>();
  for (const resource of resources) {
    const key = resource.scope_region_key ?? FALLBACK_REGION_KEY;
    const name = resource.scope_region_name ?? (key === FALLBACK_REGION_KEY ? "全局资源" : resource.scope_node_name ?? "其他区域");
    const group = groups.get(key) ?? { key, name, items: [] };
    group.items.push(resource);
    groups.set(key, group);
  }
  return [...groups.values()];
}

export function groupNodesByRegion(nodes: DisplayNode[]): RegionGroup<DisplayNode>[] {
  const hasSpatialMetadata = nodes.some(
    (node) => node.node_type_key || node.region_key || (node.endpoint_region_keys?.length ?? 0) > 0,
  );
  if (!hasSpatialMetadata) {
    return [{ key: FALLBACK_REGION_KEY, name: "全部地点", items: nodes }];
  }

  const groups = new Map<string, RegionGroup<DisplayNode>>();
  const isRegion = (node: DisplayNode) =>
    node.node_type_key === "region" ||
    (node.region_key === node.key && (node.endpoint_region_keys?.length ?? 0) === 0);
  for (const node of nodes) {
    if (!isRegion(node)) continue;
    groups.set(node.key, { key: node.key, name: node.name, items: [] });
  }

  const addToRegion = (regionKey: string, regionName: string | null | undefined, node: DisplayNode) => {
    const group = groups.get(regionKey) ?? {
      key: regionKey,
      name: regionName ?? regionKey,
      items: [],
    };
    if (!group.items.some((item) => item.key === node.key)) group.items.push(node);
    groups.set(regionKey, group);
  };

  for (const node of nodes) {
    if (isRegion(node)) continue;
    const endpoints = node.endpoint_region_keys ?? [];
    if (endpoints.length > 0) {
      endpoints.forEach((regionKey, index) => {
        addToRegion(regionKey, node.endpoint_region_names?.[index], node);
      });
    } else if (node.region_key) {
      addToRegion(node.region_key, node.region_name, node);
    } else {
      addToRegion(FALLBACK_REGION_KEY, "其他地点", node);
    }
  }
  return [...groups.values()];
}

export function groupFactsByRegion(facts: DisplayFact[]): FactRegionGroup[] {
  const groups = new Map<string, FactRegionGroup>();
  for (const fact of facts) {
    const endpoints = fact.endpoint_region_keys ?? [];
    const regionEntries = endpoints.length > 0
      ? endpoints.map((key, index) => ({ key, name: fact.endpoint_region_names?.[index] }))
      : fact.region_key
        ? [{ key: fact.region_key, name: fact.region_name }]
        : [{ key: FALLBACK_REGION_KEY, name: "其他地点" }];
    for (const region of regionEntries) {
      const group = groups.get(region.key) ?? {
        key: region.key,
        name: region.name ?? region.key,
        facts: [],
      };
      group.facts.push(fact);
      groups.set(region.key, group);
    }
  }
  return [...groups.values()];
}

export function actionLocationText(location?: ActionLocation | null): string | null {
  if (!location) return null;
  return location.detail ? `${location.summary} · ${location.detail}` : location.summary;
}

export function factDisplayName(fact: DisplayFact): string {
  return `${fact.node_name ?? "未知地点"} · ${fact.name}`;
}

export function meaningfulResult(result: string | null): string | null {
  if (!result) return null;
  if (["行动已完成", "行动未完成", "行动未能达到预期结果"].includes(result)) return null;
  return result;
}
