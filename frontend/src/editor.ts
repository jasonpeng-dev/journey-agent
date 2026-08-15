export const sections = [
  "overview",
  "world",
  "actors",
  "actions",
  "rules",
  "objectives",
  "planning",
  "initial-state",
  "validation",
] as const;

export type EditorSection = (typeof sections)[number];

type JsonObject = Record<string, unknown>;

export type DraftObject = { kind: string; key: string; name: string; value: JsonObject };

function objects(value: unknown, kind: string): DraftObject[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as JsonObject;
    if (typeof record.key !== "string") return [];
    return [{ kind, key: record.key, name: typeof record.name === "string" ? record.name : record.key, value: record }];
  });
}

export function sectionObjects(document: JsonObject, section: string): DraftObject[] {
  const world = (document.world ?? {}) as JsonObject;
  const actors = (document.actors ?? {}) as JsonObject;
  switch (section) {
    case "world":
      return [
        ...objects(world.node_types, "node_type"),
        ...objects(world.nodes, "node"),
        ...objects(world.resources, "resource"),
        ...objects(document.interactions, "interaction"),
      ];
    case "actors":
      return [...objects(actors.roles, "role"), ...objects(actors.actor_profiles, "actor")];
    case "actions": return objects(document.actions, "action");
    case "rules": return objects(document.rules, "rule");
    case "objectives": return objects(document.objectives, "objective");
    default: return [];
  }
}

export function updateObjectName(document: JsonObject, section: string, key: string, name: string): JsonObject {
  const copy = structuredClone(document);
  const target = sectionObjects(copy, section).find((item) => item.key === key);
  if (target) target.value.name = name;
  return copy;
}

export function replaceObject(document: JsonObject, section: string, key: string, value: JsonObject): JsonObject {
  const copy = structuredClone(document);
  const target = sectionObjects(copy, section).find((item) => item.key === key);
  if (target) Object.assign(target.value, structuredClone(value));
  return copy;
}

export function updateSectionRoot(document: JsonObject, section: string, value: unknown): JsonObject {
  const copy = structuredClone(document);
  if (section === "overview") copy.metadata = value;
  else if (section === "planning" && value && typeof value === "object") {
    const record = value as JsonObject;
    copy.goal_resolution = record.goal_resolution;
    copy.planning = record.planning;
  } else if (section === "initial-state") copy.initialization = value;
  return copy;
}

export function sectionRoot(document: JsonObject, section: string): unknown {
  if (section === "overview") return document.metadata ?? {};
  if (section === "planning") return { goal_resolution: document.goal_resolution ?? {}, planning: document.planning ?? {} };
  if (section === "initial-state") return document.initialization ?? {};
  return null;
}
