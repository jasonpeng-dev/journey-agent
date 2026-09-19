import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";

import type { DraftObject, JsonObject } from "./editor";
import { TypedEditor, TypedEntityEditor } from "./components/TypedEditor";

afterEach(cleanup);

const document: JsonObject = {
  world: {
    node_types: [{ key: "facility", name: "Facility" }],
    nodes: [{ key: "target", name: "Target", node_type_key: "facility", facts: [{ key: "operational", name: "Operational" }] }],
    relation_types: [{ key: "contains", name: "Contains" }],
    relations: [],
    resources: [{ key: "fuel", name: "Fuel" }],
  },
  actors: {
    roles: [{ key: "engineer", name: "Engineer" }],
    actor_profiles: [{ key: "agent", name: "Agent" }],
  },
  interactions: [{ key: "inspect", name: "Inspect" }],
  actions: [{ key: "repair", name: "Repair" }],
  objectives: [{ key: "objective", name: "Objective" }],
  derived_states: [{ key: "derived", name: "Derived" }],
  initialization: { resource_pools: [{ pool_key: "fuel_pool" }] },
};

function entity(kind: DraftObject["kind"], value: JsonObject): DraftObject {
  return { kind, key: String(value.key ?? value.term ?? kind), name: String(value.name ?? value.term ?? kind), value, path: [kind] };
}

function EntityHarness({ initial }: { initial: DraftObject }) {
  const [current, setCurrent] = useState(initial);
  return <TypedEntityEditor entity={current} document={document} onChange={(value) => setCurrent({ ...current, value })} />;
}

function expandNestedCards() {
  for (let pass = 0; pass < 5; pass += 1) {
    const collapsed = Array.from(window.document.querySelectorAll<HTMLButtonElement>(".nested-card-toggle")).filter((button) => button.getAttribute("aria-expanded") === "false");
    if (collapsed.length === 0) return;
    collapsed.forEach((button) => fireEvent.click(button));
  }
}

describe("typed ScenarioDefinition v2 authoring", () => {
  it("edits Condition and Effect AST variants through typed controls", () => {
    render(<EntityHarness initial={entity("rule", {
      key: "rule",
      phase: "RESOLVE",
      trigger: "ACTION",
      action_key: "repair",
      priority: 1,
      condition: { kind: "FACT_EQUALS", node: { kind: "CURRENT_TARGET" }, fact_key: "operational", value: true },
      effects: [{ kind: "SET_FACT", node: { kind: "CURRENT_TARGET" }, fact_key: "operational", value: { source: "LITERAL", literal: true } }],
    })} />);
    expandNestedCards();

    fireEvent.change(screen.getByLabelText("条件类型"), { target: { value: "RESOURCE_COMPARE" } });
    expect(screen.getByLabelText("资源")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("效果类型"), { target: { value: "REVEAL_TARGET_REGION_FACILITY_FACTS" } });
    expect(screen.getByText(/此效果没有额外字段/)).toBeInTheDocument();

    const effectKind = screen.getByLabelText("效果类型") as HTMLSelectElement;
    expect(Array.from(effectKind.options).map((option) => option.value)).toContain("SET_RESOURCE_POOL_AVAILABILITY");
  });

  it("renders typed Action planning, target requirements, and an Advanced authority fallback", () => {
    const onChange = vi.fn();
    render(<TypedEntityEditor entity={entity("action", {
      key: "repair",
      name: "Repair",
      description: "Repair target",
      required_interaction_key: "inspect",
      execution_mode: "IMMEDIATE",
      allowed_actor_capabilities: ["EXECUTE_ACTION"],
      required_actor_role_key: "engineer",
      expected_outcomes: [{ code: "SUCCESS", name: "Success", success: true }],
      planning: {
        terminal_effects: [{ node_key: "target", fact_key: "operational" }],
        target_terminal_effects: [{ fact_key: "operational", value: true }],
        supporting_effects: [],
        success_outcome_codes: ["SUCCESS"],
        wait_success_outcome_codes: [],
        hints: ["Repair the target"],
        knowledge_gate: { node_key: "target", fact_key: "operational", accepted_values: [true] },
      },
      authority_policy: { autonomous_limits: [{ parameter_key: "count", maximum: 1 }] },
      unknown_extension: { keep: true },
    })} document={document} onChange={onChange} />);
    expandNestedCards();

    expect(screen.getByLabelText("允许的能力")).toBeInTheDocument();
    expect(screen.getAllByDisplayValue("SUCCESS")).toHaveLength(2);
    expect(screen.getAllByLabelText("节点").some((element) => (element as HTMLSelectElement).value === "target")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /权限策略/ }));
    expect(screen.getByLabelText(/权限策略 JSON/)).toHaveValue(JSON.stringify({ autonomous_limits: [{ parameter_key: "count", maximum: 1 }] }, null, 2));
    expect(screen.getByText("知识门槛")).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("Repair"), { target: { value: "Repair updated" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ name: "Repair updated", unknown_extension: { keep: true } }));
  });

  it("keeps Objective prerequisites and Derived dependencies typed", () => {
    render(<EntityHarness initial={entity("objective", {
      key: "objective",
      name: "Objective",
      description: "Complete objective",
      completion_requirements: [{ key: "complete", kind: "FACT", node_key: "target", fact_key: "operational", accepted_values: [true], description: "Target is operational" }],
      prerequisites: [{ key: "precondition", description: "Precondition", requirements: [{ key: "pre_req", kind: "FACT", node_key: "target", fact_key: "operational", accepted_values: [true], description: "Known requirement" }] }],
      subsumes: [],
    })} />);
    expandNestedCards();
    expect(screen.getByDisplayValue("pre_req")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "完成要求" })).toBeInTheDocument();

    render(<EntityHarness initial={entity("derived_state", {
      key: "derived",
      name: "Derived",
      description: "Derived state",
      value_type: "BOOLEAN",
      available_value: true,
      unavailable_value: false,
      dependencies: [{ kind: "FACT", node_key: "target", fact_key: "operational", accepted_values: [true], knowledge_gate: { node_key: "target", fact_key: "operational", accepted_values: [true] } }],
    })} />);
    expandNestedCards();
    expect(screen.getByLabelText("依赖类型")).toHaveValue("FACT");
    expect(screen.getByText("知识门槛")).toBeInTheDocument();
  });

  it("exposes typed value labels, action target taxonomy, and relation catalog pickers", () => {
    const onChange = vi.fn();
    render(<TypedEntityEditor entity={entity("action", {
      key: "repair",
      name: "Repair",
      target_kind: "NODE",
      target_node_type_keys: [],
      source_relation_type_key: "contains",
      parameters: [{ key: "amount", name: "Amount", value_type: "INTEGER", required: false }],
    })} document={document} onChange={onChange} />);
    expandNestedCards();
    expect(screen.getByLabelText("目标节点类型")).toBeInTheDocument();
    expect(screen.getByLabelText("来源关系类型")).toHaveValue("contains");
    expect(screen.getByLabelText("默认值")).toBeInTheDocument();
    expect(screen.getByLabelText("语义引用")).toBeInTheDocument();

    cleanup();
    render(<EntityHarness initial={entity("derived_state", {
      key: "ready",
      name: "Ready",
      value_type: "BOOLEAN",
      allowed_values: [],
      value_labels: [],
      dependencies: [],
    })} />);
    expect(screen.getByText("值标签")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /添加值标签/ }));
    expect(screen.getByLabelText("显示标签")).toBeInTheDocument();
  });

  it("renders scenario locality as typed overview fields", () => {
    render(<TypedEditor section="overview" value={{ key: "scenario", name: "Scenario", description: "", locality: { enabled: true, scoped_resources: true, region_node_type_key: "facility", facility_node_type_key: "facility", transport_node_type_key: "facility", located_in_relation_type_key: "contains", transport_endpoint_relation_type_key: "contains" } }} document={document} onChange={vi.fn()} />);
    expect(screen.getByLabelText("区域节点类型")).toHaveValue("facility");
    expect(screen.getByLabelText("归属关系类型")).toHaveValue("contains");
  });

  it("authors initialization availability requirements and root planning/goal/public sections", () => {
    render(<TypedEditor section="initialization" value={{
      start_node_key: "target",
      primary_actor_key: "agent",
      resource_initial_states: [],
      resource_pools: [{ pool_key: "fuel_pool", resource_key: "fuel", region_key: "target", facility_key: null, quantity: 5, reserved_value: 0, visibility: "HIDDEN", availability: "UNAVAILABLE", survey_discoverable: true, availability_requirement: { node_key: "target", fact_key: "operational", value: true } }],
      region_resource_knowledge: [],
    }} document={document} onChange={vi.fn()} />);
    expandNestedCards();
    expect(screen.getByLabelText("要求值")).toBeChecked();
    expect(screen.queryByText("可接受值")).not.toBeInTheDocument();

    cleanup();
    render(<TypedEditor section="planning" value={{ instructions: ["Plan safely"], recovery_hints: [{ failure_code: "BLOCKED", hint: "Inspect again" }] }} document={document} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue("Plan safely")).toBeInTheDocument();
    cleanup();
    render(<TypedEditor section="goal-resolution" value={{ allow_llm_fallback: true, clarification_prompt: "Clarify", world_goal_state_catalog: true }} document={document} onChange={vi.fn()} />);
    expect(screen.getByLabelText("澄清提示")).toHaveValue("Clarify");
    cleanup();
    render(<TypedEditor section="public-knowledge" value={{ resource_source_hints: [{ resource_key: "fuel", primary_region_key: "target", candidate_region_keys: [] }] }} document={document} onChange={vi.fn()} />);
    expandNestedCards();
    expect(screen.getByLabelText("资源")).toHaveValue("fuel");
  });

  it("uses master-detail rendering for root collection sections", () => {
    const onCollectionChange = vi.fn();
    render(<TypedEditor section="planning" value={{ instructions: ["Plan safely"], recovery_hints: [{ failure_code: "BLOCKED", hint: "Inspect again" }, { failure_code: "RETRY", hint: "Try again" }] }} document={document} collectionSelection={{ owner: "collection", collection: "recovery_hints", identity: JSON.stringify(["BLOCKED"]) }} onChange={vi.fn()} onCollectionChange={onCollectionChange} onCollectionRemove={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "恢复提示" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("BLOCKED")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Inspect again")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("RETRY")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("Plan safely")).not.toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("BLOCKED"), { target: { value: "MODEL_PROVIDER_TIMEOUT" } });
    expect(onCollectionChange).toHaveBeenCalledWith(expect.objectContaining({ failure_code: "MODEL_PROVIDER_TIMEOUT" }));

    cleanup();
    render(<TypedEditor section="planning" value={{ instructions: ["Plan safely"], recovery_hints: [{ failure_code: "BLOCKED", hint: "Inspect again" }] }} document={document} collectionSelection={{ owner: "singleton", key: "planning-instructions" }} onChange={vi.fn()} onCollectionChange={vi.fn()} onCollectionRemove={vi.fn()} />);
    expect(screen.getByDisplayValue("Plan safely")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("BLOCKED")).not.toBeInTheDocument();

    cleanup();
    render(<TypedEditor section="public-knowledge" value={{ resource_source_hints: [] }} document={document} collectionSelection={null} onChange={vi.fn()} onCollectionChange={vi.fn()} onCollectionRemove={vi.fn()} />);
    expect(screen.queryByRole("heading", { name: "公共知识配置" })).not.toBeInTheDocument();
    expect(screen.getByText("请选择或新建资源发现知识")).toBeInTheDocument();
  });
});
