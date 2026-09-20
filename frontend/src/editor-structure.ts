import { rootCollectionDefinitions, type RootCollectionKey } from "./editor-collections";
import { sectionDefinition, type EditorSection, type EntityKind } from "./editor";

export type SectionMode = "SINGLETON" | "COLLECTION" | "HYBRID" | "BROWSER" | "WORKFLOW";
export type MasterSource = "none" | "entities" | "root-collections" | "topology";
export type CreateCapability = "none" | "entity" | "root-collection-item";
export type SelectionStrategy = "none" | "route-object" | "root-owner" | "topology" | "workflow";
export type WorkspaceRenderer = "root" | "entity" | "root-collection" | "hybrid" | "topology" | "workflow" | "initialization";
export type AdvancedJsonCapability = "none" | "nested-only" | "unknown-variant";
export type LocatorStrategy = "singleton" | "entity" | "root-collection" | "mixed-entity" | "browser" | "workflow";

export type SectionStructure = {
  section: EditorSection;
  mode: SectionMode;
  owners: {
    rootPath: string[] | null;
    entityKinds: EntityKind[];
    rootCollections: RootCollectionKey[];
  };
  master: {
    visible: boolean;
    source: MasterSource;
    label: "section" | "structure" | "workflow";
    searchable: boolean;
    create: CreateCapability;
    grouped: boolean;
    itemIdentity: "none" | "route-object" | "collection-identity" | "topology-key";
  };
  workspace: {
    renderer: WorkspaceRenderer;
    title: "section" | "selected-item" | "topology" | "workflow";
    selection: SelectionStrategy;
  };
  capabilities: {
    create: boolean;
    rename: boolean;
    delete: boolean;
    inspector: boolean;
    advancedJson: AdvancedJsonCapability;
  };
  locator: LocatorStrategy;
};

type StructureDeclaration = Omit<SectionStructure, "owners">;

function declaration(
  section: EditorSection,
  mode: SectionMode,
  master: SectionStructure["master"],
  workspace: SectionStructure["workspace"],
  capabilities: SectionStructure["capabilities"],
  locator: LocatorStrategy,
): StructureDeclaration {
  return { section, mode, master, workspace, capabilities, locator };
}

const entityMaster: SectionStructure["master"] = {
  visible: true,
  source: "entities",
  label: "section",
  searchable: true,
  create: "entity",
  grouped: false,
  itemIdentity: "route-object",
};

const entityWorkspace: SectionStructure["workspace"] = {
  renderer: "entity",
  title: "selected-item",
  selection: "route-object",
};

const entityCapabilities: SectionStructure["capabilities"] = {
  create: true,
  rename: true,
  delete: true,
  inspector: true,
  advancedJson: "none",
};

const declarations: Record<EditorSection, StructureDeclaration> = {
  overview: declaration(
    "overview",
    "SINGLETON",
    { visible: false, source: "none", label: "section", searchable: false, create: "none", grouped: false, itemIdentity: "none" },
    { renderer: "root", title: "section", selection: "none" },
    { create: false, rename: false, delete: false, inspector: false, advancedJson: "none" },
    "singleton",
  ),
  world: declaration(
    "world",
    "BROWSER",
    { visible: true, source: "topology", label: "structure", searchable: true, create: "none", grouped: false, itemIdentity: "topology-key" },
    { renderer: "topology", title: "topology", selection: "topology" },
    { create: false, rename: false, delete: false, inspector: true, advancedJson: "none" },
    "browser",
  ),
  "node-types": declaration("node-types", "COLLECTION", entityMaster, entityWorkspace, entityCapabilities, "entity"),
  "world-entities": declaration("world-entities", "COLLECTION", entityMaster, entityWorkspace, { ...entityCapabilities, advancedJson: "nested-only" }, "entity"),
  "relation-types": declaration("relation-types", "COLLECTION", entityMaster, entityWorkspace, entityCapabilities, "entity"),
  relations: declaration("relations", "COLLECTION", entityMaster, entityWorkspace, entityCapabilities, "entity"),
  resources: declaration("resources", "COLLECTION", entityMaster, entityWorkspace, entityCapabilities, "entity"),
  roles: declaration("roles", "COLLECTION", entityMaster, entityWorkspace, entityCapabilities, "entity"),
  actors: declaration("actors", "COLLECTION", entityMaster, entityWorkspace, { ...entityCapabilities, advancedJson: "nested-only" }, "entity"),
  interactions: declaration("interactions", "COLLECTION", entityMaster, entityWorkspace, entityCapabilities, "entity"),
  actions: declaration("actions", "COLLECTION", entityMaster, entityWorkspace, { ...entityCapabilities, advancedJson: "nested-only" }, "entity"),
  rules: declaration("rules", "COLLECTION", entityMaster, entityWorkspace, { ...entityCapabilities, advancedJson: "unknown-variant" }, "entity"),
  "derived-states": declaration("derived-states", "COLLECTION", entityMaster, entityWorkspace, { ...entityCapabilities, advancedJson: "unknown-variant" }, "entity"),
  initialization: declaration(
    "initialization",
    "BROWSER",
    { visible: false, source: "none", label: "structure", searchable: false, create: "none", grouped: false, itemIdentity: "none" },
    { renderer: "initialization", title: "section", selection: "none" },
    { create: false, rename: false, delete: false, inspector: false, advancedJson: "none" },
    "browser",
  ),
  "goal-resolution": declaration(
    "goal-resolution",
    "SINGLETON",
    { visible: false, source: "none", label: "section", searchable: false, create: "none", grouped: false, itemIdentity: "none" },
    { renderer: "root", title: "section", selection: "none" },
    { create: false, rename: false, delete: false, inspector: false, advancedJson: "none" },
    "singleton",
  ),
  planning: declaration(
    "planning",
    "HYBRID",
    { visible: true, source: "root-collections", label: "section", searchable: true, create: "root-collection-item", grouped: true, itemIdentity: "collection-identity" },
    { renderer: "hybrid", title: "section", selection: "root-owner" },
    { create: true, rename: false, delete: true, inspector: false, advancedJson: "none" },
    "root-collection",
  ),
  "public-knowledge": declaration(
    "public-knowledge",
    "COLLECTION",
    { visible: true, source: "root-collections", label: "section", searchable: true, create: "root-collection-item", grouped: true, itemIdentity: "collection-identity" },
    { renderer: "root-collection", title: "selected-item", selection: "root-owner" },
    { create: true, rename: false, delete: true, inspector: false, advancedJson: "none" },
    "root-collection",
  ),
  "public-references": declaration("public-references", "COLLECTION", entityMaster, entityWorkspace, { ...entityCapabilities, rename: false }, "mixed-entity"),
  validation: declaration(
    "validation",
    "WORKFLOW",
    { visible: false, source: "none", label: "workflow", searchable: false, create: "none", grouped: false, itemIdentity: "none" },
    { renderer: "workflow", title: "workflow", selection: "workflow" },
    { create: false, rename: false, delete: false, inspector: false, advancedJson: "none" },
    "workflow",
  ),
};

function resolveOwners(section: EditorSection): SectionStructure["owners"] {
  const definition = sectionDefinition(section);
  return {
    rootPath: definition?.rootPath ? [...definition.rootPath] : null,
    entityKinds: definition?.entityKinds ? [...definition.entityKinds] : [],
    rootCollections: rootCollectionDefinitions(section).map((item) => item.key),
  };
}

export const sectionStructureRegistry: Record<EditorSection, SectionStructure> = Object.fromEntries(
  Object.entries(declarations).map(([section, value]) => [section, { ...value, owners: resolveOwners(section as EditorSection) }]),
) as Record<EditorSection, SectionStructure>;

export function sectionStructure(section: EditorSection): SectionStructure {
  return sectionStructureRegistry[section];
}
