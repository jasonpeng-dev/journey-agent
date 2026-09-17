import type { EntityKind, EditorSection } from "./editor";

export type FieldType = "text" | "textarea" | "boolean" | "integer" | "number" | "enum" | "multi-enum" | "reference" | "multi-reference" | "json";

export type ReferenceDomain =
  | "node_type"
  | "node"
  | "relation_type"
  | "fact"
  | "resource"
  | "resource_pool"
  | "role"
  | "actor"
  | "interaction"
  | "action"
  | "objective"
  | "derived_state"
  | "relation";

export type FieldMetadata = {
  path: string;
  type: FieldType;
  label?: string;
  enum?: readonly string[];
  referenceDomain?: ReferenceDomain;
  multiline?: boolean;
  advanced?: boolean;
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
  requirementKind: ["FACT", "RESOURCE_AT_LEAST", "DERIVED_STATE"],
  publicReferenceType: ["NODE", "REGION", "RESOURCE", "DERIVED_STATE", "ACTION", "ACTOR"],
} as const;

const field = (path: string, type: FieldType, metadata: Omit<FieldMetadata, "path" | "type"> = {}): FieldMetadata => ({ path, type, ...metadata });

export const entityRegistry: Record<EntityKind, EntityMetadata> = {
  node_type: {
    kind: "node_type", section: "node-types", label: "节点类型", collectionPath: ["world", "node_types"],
    fields: [field("key", "text"), field("name", "text"), field("description", "textarea")],
  },
  node: {
    kind: "node", section: "world-entities", label: "节点", collectionPath: ["world", "nodes"],
    fields: [field("key", "text"), field("name", "text"), field("description", "textarea"), field("node_type_key", "reference", { referenceDomain: "node_type" }), field("initial_access", "enum", { enum: V2_ENUMS.access }), field("initial_visibility", "enum", { enum: V2_ENUMS.visibility }), field("interaction_keys", "multi-reference", { referenceDomain: "interaction" })],
    nested: ["facts"],
  },
  relation_type: {
    kind: "relation_type", section: "relations", label: "关系类型", collectionPath: ["world", "relation_types"],
    fields: [field("key", "text"), field("name", "text"), field("description", "textarea")],
  },
  relation: {
    kind: "relation", section: "relations", label: "关系", collectionPath: ["world", "relations"],
    fields: [field("key", "text"), field("source_node_key", "reference", { referenceDomain: "node" }), field("relation_type_key", "reference", { referenceDomain: "relation_type" }), field("target_node_key", "reference", { referenceDomain: "node" }), field("initial_visibility", "enum", { enum: V2_ENUMS.relationVisibility })],
  },
  resource: {
    kind: "resource", section: "resources", label: "资源", collectionPath: ["world", "resources"],
    fields: [field("key", "text"), field("name", "text"), field("description", "textarea"), field("initial_value", "integer"), field("minimum", "integer"), field("maximum", "integer"), field("reservation_supported", "boolean"), field("unit", "text"), field("display_unit", "text")],
  },
  role: {
    kind: "role", section: "roles", label: "角色", collectionPath: ["actors", "roles"],
    fields: [field("key", "text"), field("name", "text"), field("description", "textarea"), field("capabilities", "multi-enum", { enum: V2_ENUMS.capabilities })],
  },
  actor: {
    kind: "actor", section: "actors", label: "参与者档案", collectionPath: ["actors", "actor_profiles"],
    fields: [field("key", "text"), field("name", "text"), field("role_key", "reference", { referenceDomain: "role" }), field("persona", "textarea"), field("initial_node_key", "reference", { referenceDomain: "node" }), field("allowed_action_keys", "multi-reference", { referenceDomain: "action" }), field("command_reachability", "enum", { enum: ["ONLINE", "DISCONNECTED"] })],
    nested: ["doctrine", "authority_policy"],
  },
  interaction: {
    kind: "interaction", section: "interactions", label: "交互能力", collectionPath: ["interactions"],
    fields: [field("key", "text"), field("name", "text"), field("description", "textarea")],
  },
  action: {
    kind: "action", section: "actions", label: "行动", collectionPath: ["actions"],
    fields: [field("key", "text"), field("name", "text"), field("description", "textarea"), field("required_interaction_key", "reference", { referenceDomain: "interaction" }), field("execution_mode", "enum", { enum: V2_ENUMS.executionMode }), field("behavior", "enum", { enum: V2_ENUMS.behavior }), field("locality", "enum", { enum: V2_ENUMS.locality }), field("target_kind", "enum", { enum: V2_ENUMS.targetKind }), field("target_node_type_keys", "multi-reference", { referenceDomain: "node_type" }), field("target_semantic_reference_type", "enum", { enum: V2_ENUMS.actionTargetReference }), field("required_actor_role_key", "reference", { referenceDomain: "role" }), field("allowed_actor_capabilities", "multi-enum", { enum: V2_ENUMS.capabilities }), field("source_relation_type_key", "reference", { referenceDomain: "relation_type" })],
    nested: ["parameters", "expected_outcomes", "planning", "target_actor_roles", "operation_bindings", "goal_required_slots", "authority_policy"],
  },
  rule: {
    kind: "rule", section: "rules", label: "规则", collectionPath: ["rules"],
    fields: [field("key", "text"), field("phase", "enum", { enum: V2_ENUMS.phase }), field("trigger", "enum", { enum: V2_ENUMS.trigger }), field("action_key", "reference", { referenceDomain: "action" }), field("priority", "integer")],
    nested: ["condition", "effects"],
  },
  objective: {
    kind: "objective", section: "objectives", label: "目标", collectionPath: ["objectives"],
    fields: [field("key", "text"), field("name", "text"), field("description", "textarea"), field("goal_aliases", "text"), field("goal_examples", "text"), field("planning_guidance", "textarea")],
    nested: ["completion_requirements", "prerequisites", "subsumes"],
  },
  derived_state: {
    kind: "derived_state", section: "derived-states", label: "派生状态", collectionPath: ["derived_states"],
    fields: [field("key", "text"), field("name", "text"), field("description", "textarea"), field("value_type", "enum", { enum: V2_ENUMS.factType }), field("available_value", "text"), field("unavailable_value", "text")],
    nested: ["dependencies"],
  },
  public_reference: {
    kind: "public_reference", section: "public-references", label: "公共引用", collectionPath: ["public_references"],
    fields: [field("term", "text"), field("ref_type", "enum", { enum: V2_ENUMS.publicReferenceType }), field("ref_key", "text")],
  },
};

export const rootFieldRegistry: Record<string, readonly FieldMetadata[]> = {
  metadata: [field("key", "text"), field("name", "text"), field("description", "textarea"), field("locality", "json", { advanced: true })],
  goal_resolution: [field("allow_llm_fallback", "boolean"), field("clarification_prompt", "textarea"), field("world_goal_state_catalog", "boolean")],
  planning: [field("instructions", "text"), field("recovery_hints", "json", { advanced: true })],
  public_knowledge: [field("resource_source_hints", "json", { advanced: true })],
  initialization: [field("start_node_key", "reference", { referenceDomain: "node" }), field("primary_actor_key", "reference", { referenceDomain: "actor" }), field("resource_initial_states", "json", { advanced: true }), field("resource_pools", "json", { advanced: true }), field("region_resource_knowledge", "json", { advanced: true })],
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
