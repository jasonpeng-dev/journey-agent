import type { EntityKind, EditorSection } from "./editor";

export type FieldType = "text" | "textarea" | "boolean" | "integer" | "number" | "enum" | "multi-enum" | "reference" | "multi-reference" | "json";

export type ReferenceDomain =
  | "node_type"
  | "node"
  | "region"
  | "relation_type"
  | "fact"
  | "resource"
  | "resource_pool"
  | "role"
  | "actor"
  | "interaction"
  | "action"
  | "derived_state"
  | "relation";

export type RequirednessClass = "SCHEMA_REQUIRED" | "VARIANT_REQUIRED" | "PUBLISH_REQUIRED" | "RUNTIME_REQUIRED" | "REFERENCE_REQUIRED" | "DEFAULTED" | "OPTIONAL" | "SYSTEM" | "DERIVED" | "LEGACY";
export type NavigationClass = "FORWARD_REQUIRED" | "FORWARD_OPTIONAL" | "BACKREFERENCE" | "WORKFLOW_HANDOFF" | "INTERNAL_ONLY" | "NO_UI_NAVIGATION";

export type FieldMetadata = {
  path: string;
  type: FieldType;
  label?: string;
  enum?: readonly string[];
  referenceDomain?: ReferenceDomain;
  multiline?: boolean;
  advanced?: boolean;
  requiredness?: RequirednessClass;
  navigation?: NavigationClass;
  minItems?: number;
};

export type EntityMetadata = {
  kind: EntityKind;
  section: EditorSection;
  label: string;
  collectionPath: string[];
  fields: readonly FieldMetadata[];
  nested?: readonly string[];
};

export const V2_ENUMS = {
  access: ["LOCKED", "AVAILABLE"],
  visibility: ["KNOWN", "HIDDEN"],
  relationVisibility: ["VISIBLE", "HIDDEN"],
  resourceInventoryVisibility: ["VISIBLE", "HIDDEN"],
  resourcePoolVisibility: ["VISIBLE", "HIDDEN"],
  resourceAvailability: ["AVAILABLE", "UNAVAILABLE"],
  capabilities: ["PLAN", "EXECUTE_ACTION", "INSPECT_STATE", "LOGISTICS"],
  executionMode: ["IMMEDIATE", "ASYNC"],
  behavior: ["RULE", "TRAVEL", "INSPECT", "REPAIR_COMMUNICATIONS", "CLEAR_TRANSPORT", "TRANSPORT_RESOURCE", "RELAY_MESSAGE", "SURVEY_RESOURCES", "SUPPLY_POWER", "DEPLOY_HEAVY_ENGINEERING_SUPPORT"],
  locality: ["NONE", "LOCAL_TARGET", "FACILITY_REGION", "TRANSPORT_ENDPOINT", "ACTOR_REGION", "REGION"],
  targetKind: ["NODE", "ACTOR"],
  parameterType: ["STRING", "ENUM", "INTEGER", "BOOLEAN"],
  factType: ["STRING", "ENUM", "INTEGER", "BOOLEAN"],
  actionTargetReference: ["NODE", "REGION", "FACILITY", "RESOURCE", "ACTOR"],
  phase: ["PREFLIGHT", "RESOLVE"],
  trigger: ["ACTION", "STATE"],
  conditionKind: ["ALL", "ANY", "NOT", "FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE", "RESOURCE_COMPARE", "PARAMETER_COMPARE", "NODE_VISIBLE", "NODE_ACCESSIBLE", "RELATION_EXISTS"],
  effectKind: ["SET_FACT", "REVEAL_FACT", "HIDE_FACT", "REVEAL_NODE", "HIDE_NODE", "SET_NODE_ACCESS", "ADJUST_RESOURCE", "RESERVE_RESOURCE", "RELEASE_RESOURCE", "EMIT_OUTCOME", "EMIT_FAILURE", "WRITE_MEMORY_EVENT", "SET_ACTOR_COMMAND_REACHABILITY", "SET_RELATION_VISIBILITY", "SET_REGION_RESOURCE_VISIBILITY", "SET_RESOURCE_POOL_VISIBILITY", "SET_RESOURCE_POOL_AVAILABILITY", "REVEAL_TARGET_REGION_FACILITY_FACTS"],
  comparison: ["EQ", "NE", "LT", "LTE", "GT", "GTE"],
  valueSource: ["LITERAL", "PARAMETER"],
  selectorKind: ["CURRENT_TARGET", "ACTION_SOURCE", "EXPLICIT", "RELATED"],
  relationDirection: ["SOURCE", "TARGET"],
  resourceScope: ["EXPLICIT", "ACTOR_CURRENT_REGION", "CURRENT_TARGET_REGION"],
  derivedDependency: ["FACT", "RESOURCE_AT_LEAST", "DERIVED_STATE"],
  publicReferenceType: ["NODE", "REGION", "RESOURCE", "DERIVED_STATE", "ACTION", "ACTOR"],
} as const;

const field = (path: string, type: FieldType, metadata: Omit<FieldMetadata, "path" | "type"> = {}): FieldMetadata => ({ path, type, ...metadata });
const requiredField = (path: string, type: FieldType, metadata: Omit<FieldMetadata, "path" | "type" | "requiredness"> = {}): FieldMetadata => field(path, type, { ...metadata, requiredness: type === "reference" ? "REFERENCE_REQUIRED" : "SCHEMA_REQUIRED" });
const conditionalField = (path: string, type: FieldType, metadata: Omit<FieldMetadata, "path" | "type" | "requiredness"> = {}): FieldMetadata => field(path, type, { ...metadata, requiredness: "VARIANT_REQUIRED" });

export const entityRegistry: Record<EntityKind, EntityMetadata> = {
  node_type: {
    kind: "node_type", section: "node-types", label: "节点类型", collectionPath: ["world", "node_types"],
    fields: [field("key", "text"), requiredField("name", "text"), field("description", "textarea")],
  },
  node: {
    kind: "node", section: "world-entities", label: "节点", collectionPath: ["world", "nodes"],
    fields: [field("key", "text"), requiredField("name", "text"), field("description", "textarea"), requiredField("node_type_key", "reference", { referenceDomain: "node_type", navigation: "FORWARD_REQUIRED" }), field("interaction_keys", "multi-reference", { referenceDomain: "interaction", navigation: "FORWARD_OPTIONAL" })],
  },
  relation_type: {
    kind: "relation_type", section: "relation-types", label: "关系类型", collectionPath: ["world", "relation_types"],
    fields: [field("key", "text"), requiredField("name", "text"), field("description", "textarea")],
  },
  relation: {
    kind: "relation", section: "relations", label: "关系实例", collectionPath: ["world", "relations"],
    fields: [field("key", "text"), requiredField("source_node_key", "reference", { referenceDomain: "node", navigation: "FORWARD_REQUIRED" }), requiredField("relation_type_key", "reference", { referenceDomain: "relation_type", navigation: "FORWARD_REQUIRED" }), requiredField("target_node_key", "reference", { referenceDomain: "node", navigation: "FORWARD_REQUIRED" })],
  },
  resource: {
    kind: "resource", section: "resources", label: "资源", collectionPath: ["world", "resources"],
    fields: [field("key", "text"), requiredField("name", "text"), field("description", "textarea"), requiredField("initial_value", "integer"), requiredField("minimum", "integer"), field("maximum", "integer"), field("reservation_supported", "boolean"), field("unit", "text"), field("display_unit", "text")],
  },
  role: {
    kind: "role", section: "roles", label: "角色", collectionPath: ["actors", "roles"],
    fields: [field("key", "text"), requiredField("name", "text"), field("description", "textarea"), requiredField("capabilities", "multi-enum", { enum: V2_ENUMS.capabilities, minItems: 1 })],
  },
  actor: {
    kind: "actor", section: "actors", label: "参与者档案", collectionPath: ["actors", "actor_profiles"],
    fields: [field("key", "text"), requiredField("name", "text"), requiredField("role_key", "reference", { referenceDomain: "role", navigation: "FORWARD_REQUIRED" }), requiredField("persona", "textarea"), field("allowed_action_keys", "multi-reference", { referenceDomain: "action", navigation: "FORWARD_OPTIONAL", requiredness: "DEFAULTED" })],
    nested: ["doctrine", "authority_policy"],
  },
  interaction: {
    kind: "interaction", section: "interactions", label: "交互能力", collectionPath: ["interactions"],
    fields: [field("key", "text"), requiredField("name", "text"), field("description", "textarea")],
  },
  action: {
    kind: "action", section: "actions", label: "行动", collectionPath: ["actions"],
    fields: [field("key", "text"), requiredField("name", "text"), field("description", "textarea"), requiredField("required_interaction_key", "reference", { referenceDomain: "interaction", navigation: "FORWARD_REQUIRED" }), requiredField("execution_mode", "enum", { enum: V2_ENUMS.executionMode }), field("behavior", "enum", { enum: V2_ENUMS.behavior }), field("locality", "enum", { enum: V2_ENUMS.locality }), field("target_kind", "enum", { enum: V2_ENUMS.targetKind }), field("target_node_type_keys", "multi-reference", { referenceDomain: "node_type", navigation: "FORWARD_OPTIONAL" }), field("target_semantic_reference_type", "enum", { enum: V2_ENUMS.actionTargetReference }), field("required_actor_role_key", "reference", { referenceDomain: "role", navigation: "FORWARD_OPTIONAL" }), requiredField("allowed_actor_capabilities", "multi-enum", { enum: V2_ENUMS.capabilities, minItems: 1 }), field("source_relation_type_key", "reference", { referenceDomain: "relation_type", navigation: "FORWARD_OPTIONAL" })],
    nested: ["parameters", "expected_outcomes", "planning", "target_actor_roles", "target_contracts", "operation_bindings", "goal_required_slots", "authority_policy"],
  },
  rule: {
    kind: "rule", section: "rules", label: "规则", collectionPath: ["rules"],
    fields: [field("key", "text"), requiredField("phase", "enum", { enum: V2_ENUMS.phase }), field("trigger", "enum", { enum: V2_ENUMS.trigger }), conditionalField("action_key", "reference", { referenceDomain: "action", navigation: "FORWARD_REQUIRED" }), field("applicable_target_keys", "multi-reference", { navigation: "FORWARD_OPTIONAL" }), requiredField("priority", "integer")],
    nested: ["condition", "effects"],
  },
  derived_state: {
    kind: "derived_state", section: "derived-states", label: "派生状态", collectionPath: ["derived_states"],
    fields: [field("key", "text"), requiredField("name", "text"), field("description", "textarea"), requiredField("value_type", "enum", { enum: V2_ENUMS.factType }), requiredField("available_value", "text"), requiredField("unavailable_value", "text")],
    nested: ["dependencies"],
  },
  public_reference: {
    kind: "public_reference", section: "terminology-references", label: "术语引用", collectionPath: ["public_references"],
    fields: [field("term", "text"), field("ref_type", "enum", { enum: V2_ENUMS.publicReferenceType }), field("ref_key", "text")],
  },
};

export const rootFieldRegistry: Record<string, readonly FieldMetadata[]> = {
  metadata: [field("key", "text"), field("name", "text"), field("description", "textarea")],
  goal_resolution: [field("allow_llm_fallback", "boolean"), field("clarification_prompt", "textarea"), field("quick_inputs", "text"), field("world_goal_state_catalog", "boolean")],
  planning: [field("instructions", "text")],
  initialization: [requiredField("start_node_key", "reference", { referenceDomain: "node", navigation: "FORWARD_REQUIRED" }), requiredField("primary_actor_key", "reference", { referenceDomain: "actor", navigation: "FORWARD_REQUIRED" })],
};

export function metadataForKind(kind: EntityKind): EntityMetadata {
  return entityRegistry[kind];
}

export function factInitialValueMetadata(valueType: string, allowedValues: unknown[] = []): FieldMetadata {
  if (valueType === "BOOLEAN") return field("initial_value", "boolean");
  if (valueType === "INTEGER") return field("initial_value", "integer");
  if (valueType === "ENUM") return field("initial_value", "enum", { enum: allowedValues.filter((item): item is string => typeof item === "string") });
  return field("initial_value", "text");
}
