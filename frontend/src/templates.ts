type JsonObject = Record<string, unknown>;

const defaults: Record<string, JsonObject> = {
  node_type: { key: "new_node_type", name: "新节点类型", description: "" },
  node: {
    key: "new_node", name: "新节点", description: "", node_type_key: "node_type",
    initial_access: "AVAILABLE", initial_visibility: "KNOWN", interaction_keys: [], facts: [],
  },
  resource: { key: "new_resource", name: "新资源", description: "", initial_value: 0, minimum: 0, maximum: null, reservation_supported: false },
  interaction: { key: "new_interaction", name: "新交互能力", description: "" },
  role: { key: "new_role", name: "新角色", description: "", capabilities: ["EXECUTE_ACTION"] },
  actor: { key: "new_actor", name: "新参与者", role_key: "role", persona: "请描述这个参与者。", doctrine: [], initial_node_key: "node", allowed_action_keys: [], authority_policy: { autonomous_limits: [], approval_required_values: [] } },
  action: {
    key: "new_action", name: "新行动", description: "", required_interaction_key: "interaction",
    execution_mode: "IMMEDIATE", parameters: [], allowed_actor_capabilities: ["EXECUTE_ACTION"],
    behavior: "RULE", locality: "NONE",
    authority_policy: { autonomous_limits: [], approval_required_values: [] },
    expected_outcomes: [{ code: "Success", name: "成功", success: true }],
    planning: { terminal_effects: [], supporting_effects: [], success_outcome_codes: ["Success"], wait_success_outcome_codes: [], hints: [] },
  },
  rule: {
    key: "new_rule", phase: "RESOLVE", action_key: "action", priority: 0, condition: null,
    effects: [{ kind: "EMIT_OUTCOME", outcome_code: "Success", retryable: false }],
  },
  objective: {
    key: "new_objective", name: "新目标", description: "请描述这个目标。",
    completion_requirements: [{ key: "completion", node_key: "node", fact_key: "fact", accepted_values: [true], description: "完成要求" }],
    prerequisites: [], subsumes: [], goal_aliases: [], goal_examples: [],
  },
};

const paths: Record<string, string[]> = {
  node_type: ["world", "node_types"], node: ["world", "nodes"], resource: ["world", "resources"],
  interaction: ["interactions"], role: ["actors", "roles"], actor: ["actors", "actor_profiles"],
  action: ["actions"], rule: ["rules"], objective: ["objectives"],
};

export const kindsBySection: Record<string, string[]> = {
  world: ["node_type", "node", "resource", "interaction"], actors: ["role", "actor"],
  actions: ["action"], rules: ["rule"], objectives: ["objective"],
};

export function addObject(document: JsonObject, kind: string): { document: JsonObject; key: string } {
  const copy = structuredClone(document);
  const path = paths[kind];
  if (!path) throw new Error(`不支持的对象类型：${kind}`);
  let current: JsonObject = copy;
  for (const part of path.slice(0, -1)) {
    const child = current[part];
    if (!child || typeof child !== "object" || Array.isArray(child)) current[part] = {};
    current = current[part] as JsonObject;
  }
  const final = path.at(-1)!;
  if (!Array.isArray(current[final])) current[final] = [];
  const items = current[final] as JsonObject[];
  const base = defaults[kind].key as string;
  let key = base;
  let index = 2;
  while (items.some((item) => item.key === key)) key = `${base}_${index++}`;
  items.push({ ...structuredClone(defaults[kind]), key });
  return { document: copy, key };
}

export function defaultArrayItem(field: string): unknown {
  if (field === "conditions") return { kind: "FACT_EQUALS", node: { kind: "EXPLICIT", node_key: "node" }, fact_key: "fact", value: true, conditions: [], values: [] };
  if (field === "effects") return { kind: "EMIT_OUTCOME", outcome_code: "Success", retryable: false };
  if (field.includes("requirements")) return { key: "requirement", node_key: "node", fact_key: "fact", accepted_values: [true], description: "要求" };
  if (field === "facts") return { key: "new_fact", name: "新事实", description: "", value_type: "BOOLEAN", initial_value: false, initial_visibility: "KNOWN", allowed_values: [] };
  if (field === "relations") return { source_node_key: "node", relation_type_key: "related_to", target_node_key: "node" };
  if (field === "parameters") return { key: "parameter", name: "Parameter", value_type: "STRING", required: true, allowed_values: [] };
  return "";
}
