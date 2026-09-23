import { sectionForKind, sectionRoot, type EditorSection, type EntityKind, type JsonObject } from "./editor";
import { rootCollectionSelectionForPath, type RootCollectionKey } from "./editor-collections";
import type { Locator } from "./types";

export type EditorLocator =
  | { owner: "singleton"; section: EditorSection; fieldPath: string | null }
  | { owner: "entity"; section: EditorSection; kind: EntityKind; objectKey: string; fieldPath: string | null }
  | { owner: "root-collection"; section: EditorSection; collection: RootCollectionKey; identity: string; fieldPath: string | null }
  | { owner: "browser"; section: "world"; contextKind: "scope" | "node" | "relation"; contextKey: string }
  | { owner: "workflow"; section: "validation"; issuePath: string | null };

const rootSections: Record<string, EditorSection> = {
  metadata: "overview",
  initialization: "initialization",
  goal_resolution: "goal-resolution",
  planning: "planning-instructions",
  public_knowledge: "public-knowledge",
  engine_contract: "overview",
};

const entityKinds = new Set<EntityKind>([
  "node_type", "node", "relation_type", "relation", "resource", "role", "actor",
  "interaction", "action", "rule", "derived_state", "public_reference",
]);

export function editorLocatorFromValidation(locator: Locator, document: JsonObject): EditorLocator | null {
  if (locator.object_kind === "planning" && locator.field_path?.startsWith("recovery_hints")) {
    const root = sectionRoot(document, "planning-recovery");
    const selection = root && typeof root === "object" && !Array.isArray(root) ? rootCollectionSelectionForPath(root as JsonObject, locator.field_path) : null;
    if (selection) return { owner: "root-collection", section: "planning-recovery", collection: selection.collection, identity: selection.identity, fieldPath: locator.field_path };
  }
  const rootSection = rootSections[locator.object_kind];
  if (rootSection) {
    const root = sectionRoot(document, rootSection);
    const selection = root && typeof root === "object" && !Array.isArray(root)
      ? rootCollectionSelectionForPath(root as JsonObject, locator.field_path)
      : null;
    if (selection) {
      return {
        owner: "root-collection",
        section: rootSection,
        collection: selection.collection,
        identity: selection.identity,
        fieldPath: locator.field_path,
      };
    }
    return { owner: "singleton", section: rootSection, fieldPath: locator.field_path };
  }
  if (entityKinds.has(locator.object_kind as EntityKind) && locator.object_key) {
    const kind = locator.object_kind as EntityKind;
    return { owner: "entity", section: sectionForKind(kind), kind, objectKey: locator.object_key, fieldPath: locator.field_path };
  }
  return null;
}

export function editorLocatorHref(locator: EditorLocator, scenarioId: string): string {
  if (locator.owner === "browser") {
    const query = new URLSearchParams({ context: locator.contextKind, key: locator.contextKey });
    return `/scenarios/${scenarioId}/edit/world?${query}`;
  }
  if (locator.owner === "workflow") {
    const query = new URLSearchParams();
    if (locator.issuePath) query.set("focus_path", locator.issuePath);
    return `/scenarios/${scenarioId}/edit/validation${query.size ? `?${query}` : ""}`;
  }
  const query = new URLSearchParams();
  if (locator.fieldPath) query.set("focus_path", locator.fieldPath);
  if (locator.owner === "root-collection") {
    query.set("owner", "collection");
    query.set("collection", locator.collection);
    query.set("item", locator.identity);
  }
  if (locator.owner === "entity" && locator.kind === "public_reference") {
    query.set("kind", locator.kind);
  }
  const objectPath = locator.owner === "entity" ? `/${encodeURIComponent(locator.objectKey)}` : "";
  const section = locator.owner === "entity" && locator.kind === "relation_type" ? "relation-types" : locator.section;
  return `/scenarios/${scenarioId}/edit/${section}${objectPath}${query.size ? `?${query}` : ""}`;
}
