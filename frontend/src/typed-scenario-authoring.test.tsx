import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";

import type { DraftObject, JsonObject } from "./editor";
import { TypedEditor, TypedEntityEditor } from "./components/TypedEditor";
import { useEditorFocusActivation } from "./editor-focus";

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

const document: JsonObject = {
  metadata: { locality: { region_node_type_key: "region" } },
  world: {
    node_types: [{ key: "facility", name: "Facility" }, { key: "region", name: "Region" }],
    nodes: [
      { key: "target", name: "Target", node_type_key: "facility", facts: [{ key: "operational", name: "Operational", value_type: "BOOLEAN", initial_value: true, initial_visibility: "KNOWN" }] },
      { key: "north", name: "North Region", node_type_key: "region" },
    ],
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
  derived_states: [{ key: "derived", name: "Derived", value_type: "BOOLEAN", available_value: true, unavailable_value: false }],
  initialization: { resource_pools: [{ pool_key: "fuel_pool" }] },
};

function entity(kind: DraftObject["kind"], value: JsonObject): DraftObject {
  return { kind, key: String(value.key ?? value.term ?? kind), name: String(value.name ?? value.term ?? kind), value, path: [kind] };
}

function EntityHarness({ initial, document: currentDocument = document, onChange, scenarioId }: { initial: DraftObject; document?: JsonObject; onChange?: (value: JsonObject, intent?: string) => void; scenarioId?: string }) {
  const [current, setCurrent] = useState(initial);
  return <TypedEntityEditor entity={current} document={currentDocument} scenarioId={scenarioId} onChange={(value, intent) => { if (intent) onChange?.(value, intent); else onChange?.(value); setCurrent({ ...current, value }); }} />;
}

function FocusedEntityHarness({ initial, path, currentDocument = document }: { initial: DraftObject; path: string; currentDocument?: JsonObject }) {
  const location = useLocation();
  useEditorFocusActivation(path, location.search, currentDocument);
  return <TypedEntityEditor entity={initial} document={currentDocument} focusPath={path} onChange={vi.fn()} onDeleteFact={vi.fn()} />;
}

function expandNestedCards() {
  for (let pass = 0; pass < 5; pass += 1) {
    const collapsed = Array.from(window.document.querySelectorAll<HTMLButtonElement>(".nested-card-toggle")).filter((button) => button.getAttribute("aria-expanded") === "false");
    if (collapsed.length === 0) return;
    collapsed.forEach((button) => fireEvent.click(button));
  }
}

function expectRequiredPath(path: string) {
  const field = window.document.querySelector(`[data-field-path="${path}"]`);
  expect(field).not.toBeNull();
  expect(field?.querySelector(".required-marker")).toBeInTheDocument();
  expect(field?.querySelector('.field-error[role="alert"]')).toBeInTheDocument();
}

describe("typed ScenarioDefinition v2 authoring", () => {
  it("keeps immutable identity references linked to their exact targets", () => {
    window.history.replaceState({}, "", "/scenarios/scenario-1/edit/actions/repair");
    const action = entity("action", {
      key: "repair", name: "Repair", required_interaction_key: "inspect", execution_mode: "IMMEDIATE",
      parameters: [], expected_outcomes: [], operation_bindings: [],
      target_actor_roles: [{ target_key: "target", required_actor_role_key: "engineer" }], planning: {},
    });
    const { container } = render(<MemoryRouter><TypedEntityEditor entity={action} document={document} scenarioId="scenario-1" onChange={vi.fn()} /></MemoryRouter>);
    expandNestedCards();
    expect(container.querySelector('a[href="/scenarios/scenario-1/edit/world-entities/target"]')).toHaveTextContent("前往Target");
    expect(container.querySelector('a[href="/scenarios/scenario-1/edit/roles/engineer"]')).toHaveTextContent("前往Engineer");

    cleanup();
    window.history.replaceState({}, "", "/scenarios/scenario-1/edit/derived-states/derived");
    const derived = entity("derived_state", {
      key: "derived", name: "Derived", value_type: "BOOLEAN", available_value: true, unavailable_value: false,
      dependencies: [{ kind: "FACT", node_key: "target", fact_key: "operational", accepted_values: [true] }],
    });
    const derivedRender = render(<MemoryRouter><TypedEntityEditor entity={derived} document={document} scenarioId="scenario-1" onChange={vi.fn()} /></MemoryRouter>);
    expandNestedCards();
    expect(derivedRender.container.querySelector('a[href="/scenarios/scenario-1/edit/world-entities/target?focus_path=facts.operational"]')).toBeInTheDocument();

    cleanup();
    const publicReference = entity("public_reference", { term: "fuel", ref_type: "RESOURCE", ref_key: "fuel" });
    const publicReferenceRender = render(<MemoryRouter><TypedEntityEditor entity={publicReference} document={document} scenarioId="scenario-1" onChange={vi.fn()} /></MemoryRouter>);
    expect(publicReferenceRender.container.querySelector('a[href="/scenarios/scenario-1/edit/resources/fuel"]')).toHaveTextContent("前往所选对象");
  });

  it("keeps optional Action planning outcome references linked to exact outcomes and their owner", () => {
    window.history.replaceState({}, "", "/scenarios/scenario-1/edit/actions/repair");
    const action = entity("action", {
      key: "repair", name: "Repair", required_interaction_key: "inspect", execution_mode: "IMMEDIATE",
      expected_outcomes: [{ code: "DONE", name: "Done", success: true }],
      parameters: [], operation_bindings: [], planning: { success_outcome_codes: ["DONE"], wait_success_outcome_codes: [] },
    });
    const { container } = render(<MemoryRouter><TypedEntityEditor entity={action} document={document} scenarioId="scenario-1" onChange={vi.fn()} /></MemoryRouter>);
    const successField = container.querySelector('[data-field-path="action.repair.planning.success_outcome_codes"]');
    const waitField = container.querySelector('[data-field-path="action.repair.planning.wait_success_outcome_codes"]');
    expect(successField).not.toBeNull();
    expect(waitField).not.toBeNull();
    expect(within(successField as HTMLElement).getByRole("link", { name: /前往DONE/ })).toHaveAttribute("href", "/scenarios/scenario-1/edit/actions/repair?focus_path=expected_outcomes.DONE");
    expect(within(successField as HTMLElement).getByRole("link", { name: "前往行动结果" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/actions/repair?focus_path=expected_outcomes");
    expect(within(waitField as HTMLElement).getByRole("link", { name: "前往行动结果" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/actions/repair?focus_path=expected_outcomes");
    expect(successField?.querySelector(".required-marker")).toBeNull();
    expect(waitField?.querySelector('[role="alert"]')).toBeNull();
  });

  it("keeps existing Facts collapsed and expands new or deep-linked Facts", async () => {
    const node = entity("node", {
      key: "target",
      name: "Target",
      node_type_key: "facility",
      facts: [{ key: "operational", name: "Operational", description: "Current status", value_type: "BOOLEAN", initial_value: true, initial_visibility: "KNOWN", allowed_values: [] }],
    });
    render(<MemoryRouter><EntityHarness initial={node} /></MemoryRouter>);

    const existingToggle = screen.getByRole("button", { name: /事实 Operational operational/ });
    expect(existingToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByDisplayValue("Operational")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /未识别结构/ })).not.toBeInTheDocument();
    fireEvent.click(existingToggle);
    expect(screen.getByDisplayValue("Operational")).toBeInTheDocument();

    const onCreate = vi.fn();
    cleanup();
    render(<MemoryRouter><EntityHarness initial={node} onChange={onCreate} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "＋ 添加事实" }));
    const createDialog = screen.getByRole("dialog", { name: "创建事实" });
    fireEvent.click(within(createDialog).getByRole("button", { name: "取消" }));
    expect(onCreate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "＋ 添加事实" }));
    const secondDialog = screen.getByRole("dialog", { name: "创建事实" });
    fireEvent.change(within(secondDialog).getByLabelText(/事实键/), { target: { value: "operational" } });
    fireEvent.click(within(secondDialog).getByRole("button", { name: "创建" }));
    expect(within(secondDialog).getByRole("alert")).toHaveTextContent(/\u5f53\u524d\u8282\u70b9\u5df2\u7ecf\u5b58\u5728\u76f8\u540c\u4e8b\u5b9e\u952e/);
    expect(onCreate).not.toHaveBeenCalled();
    fireEvent.change(within(secondDialog).getByLabelText(/事实键/), { target: { value: "hydration" } });
    fireEvent.click(within(secondDialog).getByRole("button", { name: "创建" }));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ facts: expect.arrayContaining([expect.objectContaining({ key: "hydration", name: "" })]) }), "identity-create");
    expectRequiredPath("node.target.facts.1.name");
    expectRequiredPath("node.target.facts.1.value_type");

    cleanup();
    render(<MemoryRouter><FocusedEntityHarness initial={node} path="node.target.facts.0.name" /></MemoryRouter>);
    expect(await screen.findByDisplayValue("Operational")).toBeInTheDocument();
  });

  it("renders current Fact values and goal metadata only through typed controls", () => {
    const node = entity("node", {
      key: "target",
      name: "Target",
      node_type_key: "facility",
      facts: [{
        key: "repair_mode",
        name: "Repair mode",
        value_type: "ENUM",
        allowed_values: ["STANDARD", "EXPEDITED"],
        goal_addressable: true,
        goal_aliases: ["fix mode"],
        goal_examples: ["standard repair"],
        goal_target_values: ["EXPEDITED"],
      }],
    });
    const { container } = render(<MemoryRouter><TypedEntityEditor entity={node} document={document} onChange={vi.fn()} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /Repair mode repair_mode/ }));

    for (const path of [
      "node.target.facts.0.allowed_values",
      "node.target.facts.0.goal_addressable",
      "node.target.facts.0.goal_aliases",
      "node.target.facts.0.goal_examples",
      "node.target.facts.0.goal_target_values",
    ]) {
      expect(container.querySelector(`[data-field-path="${path}"]`)).not.toBeNull();
    }
    expect(container.querySelector(".advanced-json-control")).toBeNull();
    expect(screen.queryByRole("button", { name: /未识别结构|兼容结构/ })).not.toBeInTheDocument();
  });

  it("edits Condition and Effect AST variants through typed controls", () => {
    const { container } = render(<EntityHarness initial={entity("rule", {
      key: "rule",
      phase: "RESOLVE",
      trigger: "ACTION",
      action_key: "repair",
      priority: 1,
      condition: { kind: "FACT_EQUALS", node: { kind: "CURRENT_TARGET" }, fact_key: "operational", value: true },
      effects: [{ kind: "SET_FACT", node: { kind: "CURRENT_TARGET" }, fact_key: "operational", value: { source: "LITERAL", literal: true } }],
    })} />);
    const keyField = container.querySelector('[data-field-path="rule.rule.key"]');
    expect(keyField?.querySelector("output.stable-identity-value")).toHaveTextContent("rule");
    expect(keyField?.querySelector("input")).toBeNull();
    expandNestedCards();
    expect(screen.queryByRole("button", { name: /未识别结构/ })).not.toBeInTheDocument();
    const conditionKindField = window.document.querySelector('[data-field-path="rule.rule.condition.kind"]');
    expect(conditionKindField).not.toBeNull();
    fireEvent.change(within(conditionKindField as HTMLElement).getByRole("combobox"), { target: { value: "RESOURCE_COMPARE" } });
    expect(window.document.querySelector('[data-field-path="rule.rule.condition.resource_key"]')).not.toBeNull();
    const effectKindField = window.document.querySelector('[data-field-path="rule.rule.effects.0.kind"]');
    expect(effectKindField).not.toBeNull();
    fireEvent.change(within(effectKindField as HTMLElement).getByRole("combobox"), { target: { value: "REVEAL_TARGET_REGION_FACILITY_FACTS" } });
    expect(screen.getByText(/此效果没有额外字段/)).toBeInTheDocument();

    const effectKind = within(effectKindField as HTMLElement).getByRole("combobox") as HTMLSelectElement;
    expect(Array.from(effectKind.options).map((option) => option.value)).toContain("SET_RESOURCE_POOL_AVAILABILITY");
  });

  it("uses the referenced Boolean Fact domain for rule conditions and SET_FACT literals", () => {
    const onChange = vi.fn();
    const rule = entity("rule", {
      key: "boolean_rule",
      phase: "RESOLVE",
      trigger: "ACTION",
      action_key: "repair",
      priority: 1,
      condition: { kind: "FACT_EQUALS", node: { kind: "EXPLICIT", node_key: "target" }, fact_key: "operational" },
      effects: [{ kind: "SET_FACT", node: { kind: "EXPLICIT", node_key: "target" }, fact_key: "operational", value: { source: "LITERAL" } }],
    });
    const { container } = render(<EntityHarness initial={rule} onChange={onChange} />);
    expandNestedCards();

    const conditionValue = container.querySelector('[data-field-path="rule.boolean_rule.condition.value"]');
    const effectLiteral = container.querySelector('[data-field-path="rule.boolean_rule.effects.0.value.literal"]');
    expect(within(conditionValue as HTMLElement).getByRole("combobox")).toHaveDisplayValue("请选择…");
    expect(within(effectLiteral as HTMLElement).getByRole("combobox")).toHaveDisplayValue("请选择…");

    fireEvent.change(within(conditionValue as HTMLElement).getByRole("combobox"), { target: { value: "false" } });
    fireEvent.change(within(effectLiteral as HTMLElement).getByRole("combobox"), { target: { value: "false" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      effects: [expect.objectContaining({ value: { source: "LITERAL", literal: false } })],
    }));
    expect(onChange.mock.calls.some(([next]) => next.condition.value === false)).toBe(true);
  });

  it("uses the referenced Fact domain for FACT_IN values without selecting a default", () => {
    const onChange = vi.fn();
    const rule = entity("rule", {
      key: "boolean_membership_rule",
      phase: "RESOLVE",
      trigger: "ACTION",
      action_key: "repair",
      priority: 1,
      condition: { kind: "FACT_IN", node: { kind: "EXPLICIT", node_key: "target" }, fact_key: "operational", values: [] },
      effects: [],
    });
    const { container } = render(<EntityHarness initial={rule} onChange={onChange} />);
    expandNestedCards();
    const valuesField = container.querySelector('[data-field-path="rule.boolean_membership_rule.condition.values"]');
    fireEvent.click(within(valuesField as HTMLElement).getByRole("button", { name: "＋ 添加值" }));
    const valueControl = within(valuesField as HTMLElement).getByRole("combobox");
    expect(valueControl).toHaveDisplayValue("请选择…");
    fireEvent.change(valueControl, { target: { value: "false" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ condition: expect.objectContaining({ values: [false] }) }));
  });

  it("shows authored enum labels in SET_FACT literals without exposing typed JSON tokens", () => {
    const enumDocument: JsonObject = {
      ...document,
      world: {
        ...(document.world as JsonObject),
        nodes: [{ key: "target", name: "Target", node_type_key: "facility", facts: [{
          key: "mode", name: "Mode", value_type: "ENUM", allowed_values: ["AVAILABLE"],
          value_labels: [{ value: "AVAILABLE", label: "可进入" }],
        }] }],
      },
    };
    const rule = entity("rule", {
      key: "enum_rule", phase: "RESOLVE", trigger: "ACTION", action_key: "repair", priority: 1,
      effects: [{ kind: "SET_FACT", node: { kind: "EXPLICIT", node_key: "target" }, fact_key: "mode", value: { source: "LITERAL", literal: "AVAILABLE" } }],
    });
    render(<MemoryRouter><EntityHarness initial={rule} document={enumDocument} /></MemoryRouter>);
    expandNestedCards();
    const literal = window.document.querySelector('[data-field-path="rule.enum_rule.effects.0.value.literal"]') as HTMLElement;
    const select = within(literal).getByRole("combobox");
    expect(select).toHaveValue('["string","AVAILABLE"]');
    expect(Array.from((select as HTMLSelectElement).options).map((option) => option.textContent)).toEqual(["请选择…", "可进入"]);
  });

  it("renders Action planning and selects AuthorityPolicy parameters from the Action owner", () => {
    window.history.replaceState({}, "", "/scenarios/scenario-1/edit/actions/repair");
    const onChange = vi.fn();
    const action = entity("action", {
      key: "repair",
      name: "Repair",
      description: "Repair target",
      required_interaction_key: "inspect",
      execution_mode: "IMMEDIATE",
      allowed_actor_capabilities: ["EXECUTE_ACTION"],
      required_actor_role_key: "engineer",
      parameters: [{ key: "count", name: "Count", value_type: "INTEGER", required: true, allowed_values: [] }],
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
      authority_policy: { autonomous_limits: [] },
      unknown_extension: { keep: true },
    });
    const actionDocument = { ...document, actions: [action.value] };
    const { container } = render(<EntityHarness initial={action} document={actionDocument} onChange={onChange} scenarioId="scenario-1" />);
    expandNestedCards();

    expect(screen.getByLabelText(/允许的能力/)).toBeInTheDocument();
    const capabilitiesField = container.querySelector('[data-field-path="action.repair.allowed_actor_capabilities"]') as HTMLElement;
    expect(capabilitiesField).toHaveTextContent("执行行动");
    fireEvent.click(within(capabilitiesField).getByRole("button", { name: "允许的能力：添加" }));
    expect(within(capabilitiesField).getByRole("option", { name: /后勤保障/ })).toHaveTextContent("LOGISTICS");
    expect(screen.getAllByText("SUCCESS").length).toBeGreaterThan(0);
    const terminalNodeField = window.document.querySelector('[data-field-path="action.repair.planning.terminal_effects.0.node_key"]');
    expect(terminalNodeField?.querySelector("select")).toHaveValue("target");
    expect(container.querySelector(".advanced-json-control")).toBeNull();
    expect(screen.queryByLabelText(/权限策略 JSON/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /添加权限上限/ }));
    const policyDialog = screen.getByRole("dialog", { name: "创建自主限制" });
    const parameterPicker = within(policyDialog).getByRole("combobox", { name: /行动参数/ }) as HTMLSelectElement;
    expect(parameterPicker).toHaveValue("");
    fireEvent.change(parameterPicker, { target: { value: "count" } });
    expect(within(policyDialog).queryByLabelText(/Maximum/)).not.toBeInTheDocument();
    fireEvent.click(within(policyDialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ authority_policy: expect.objectContaining({ autonomous_limits: [{ parameter_key: "count" }] }) }), "identity-create");
    expect(screen.getByRole("link", { name: "前往行动参数" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/actions/repair?focus_path=parameters.count");
    expectRequiredPath("action.repair.authority_policy.autonomous_limits.0.maximum");
    expect(screen.getByText("知识门槛")).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("Repair"), { target: { value: "Repair updated" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ name: "Repair updated", unknown_extension: { keep: true } }));
  });

  it("creates Parameters, ExpectedOutcomes, and OperationBindings with explicit identities", () => {
    const onChange = vi.fn();
    const action = entity("action", {
      key: "repair", name: "Repair", required_interaction_key: "inspect", execution_mode: "IMMEDIATE",
      parameters: [{ key: "count", name: "Count", value_type: "INTEGER", required: true, allowed_values: [] }],
      expected_outcomes: [], operation_bindings: [], planning: {},
    });
    const actionDocument = { ...document, actions: [action.value] };
    render(<MemoryRouter><EntityHarness initial={action} document={actionDocument} onChange={onChange} /></MemoryRouter>);
    expandNestedCards();
    expect(screen.getByText("count", { selector: "output.stable-identity-value" })).toBeInTheDocument();
    expect(screen.queryByText("SUCCESS", { selector: "output.stable-identity-value" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "＋ 添加参数" }));
    const parameterDialog = screen.getByRole("dialog", { name: "创建参数" });
    expect(within(parameterDialog).getByLabelText(/\u53c2\u6570\u952e/)).toHaveValue("");
    expect(within(parameterDialog).queryByLabelText(/\u663e\u793a\u540d\u79f0/)).not.toBeInTheDocument();
    fireEvent.click(within(parameterDialog).getByRole("button", { name: "取消" }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "＋ 添加参数" }));
    const duplicateDialog = screen.getByRole("dialog", { name: "创建参数" });
    fireEvent.change(within(duplicateDialog).getByLabelText(/\u53c2\u6570\u952e/), { target: { value: "count" } });
    fireEvent.click(within(duplicateDialog).getByRole("button", { name: "创建" }));
    expect(within(duplicateDialog).getByRole("alert")).toHaveTextContent(/\u5df2\u7ecf\u5b58\u5728\u76f8\u540c\u53c2\u6570\u952e/);
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(within(duplicateDialog).getByRole("button", { name: "取消" }));

    fireEvent.click(screen.getByRole("button", { name: "＋ 添加结果" }));
    const outcomeDialog = screen.getByRole("dialog", { name: "创建结果" });
    expect(within(outcomeDialog).getByLabelText(/结果代码/)).toHaveValue("");
    expect(within(outcomeDialog).queryByLabelText(/Success/)).not.toBeInTheDocument();
    fireEvent.change(within(outcomeDialog).getByLabelText(/结果代码/), { target: { value: "REPAIRED" } });
    fireEvent.click(within(outcomeDialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ expected_outcomes: [{ code: "REPAIRED", name: "" }] }), "identity-create");
    expectRequiredPath("action.repair.expected_outcomes.0.name");
    expectRequiredPath("action.repair.expected_outcomes.0.success");

    cleanup();
    const createdOutcomeAction = entity("action", { ...action.value, expected_outcomes: [{ code: "REPAIRED", name: "" }] });
    render(<MemoryRouter><EntityHarness initial={createdOutcomeAction} document={{ ...document, actions: [createdOutcomeAction.value] }} onChange={onChange} /></MemoryRouter>);
    expandNestedCards();
    expect(screen.getByText("REPAIRED", { selector: "output.stable-identity-value" })).toBeInTheDocument();
    expect(screen.queryByDisplayValue("REPAIRED")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "＋ 添加绑定" }));
    const bindingDialog = screen.getByRole("dialog", { name: "创建操作绑定" });
    expect(within(bindingDialog).getByLabelText(/绑定角色/)).toHaveValue("");
    expect(within(bindingDialog).queryByLabelText(/Value type/)).not.toBeInTheDocument();
    expect(within(bindingDialog).queryByLabelText(/^Source/)).not.toBeInTheDocument();
    expect(within(bindingDialog).queryByLabelText(/Description/)).not.toBeInTheDocument();
    fireEvent.change(within(bindingDialog).getByLabelText(/绑定角色/), { target: { value: "target_node" } });
    fireEvent.click(within(bindingDialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ operation_bindings: [{ role: "target_node", source: "EXPLICIT" }] }), "identity-create");
    expectRequiredPath("action.repair.operation_bindings.0.value_type");

    cleanup();
    const createdBindingAction = entity("action", { ...createdOutcomeAction.value, operation_bindings: [{ role: "target_node", source: "EXPLICIT" }] });
    render(<MemoryRouter><TypedEntityEditor entity={createdBindingAction} document={{ ...document, actions: [createdBindingAction.value] }} onChange={onChange} /></MemoryRouter>);
    expandNestedCards();
    expect(screen.getByText("target_node", { selector: "output.stable-identity-value" })).toBeInTheDocument();
    const optionalDescription = window.document.querySelector('[data-field-path="action.repair.operation_bindings.0.description"]');
    expect(optionalDescription?.querySelector(".required-marker")).toBeNull();
    expect(optionalDescription?.querySelector('[role="alert"]')).toBeNull();
  });

  it("creates typed Action target contracts from eligible targets and keeps the identity static", () => {
    const onChange = vi.fn();
    const targetDocument = {
      ...document,
      world: {
        ...(document.world as JsonObject),
        nodes: [{ key: "target", name: "Target", node_type_key: "facility", interaction_keys: ["inspect"], facts: [] }],
      },
    };
    const action = entity("action", {
      key: "repair", name: "Repair", required_interaction_key: "inspect", execution_mode: "IMMEDIATE",
      target_kind: "NODE", parameters: [], expected_outcomes: [{ code: "DONE", name: "Done", success: true }],
      operation_bindings: [], planning: {}, target_contracts: [],
    });
    render(<MemoryRouter><EntityHarness initial={action} document={{ ...targetDocument, actions: [action.value] }} onChange={onChange} /></MemoryRouter>);

    fireEvent.click(screen.getByRole("button", { name: "添加目标信息" }));
    const dialog = screen.getByRole("dialog", { name: "创建目标信息" });
    const picker = within(dialog).getByRole("combobox", { name: /目标节点/ }) as HTMLSelectElement;
    expect(Array.from(picker.options).map((option) => option.value)).toEqual(["", "target"]);
    fireEvent.change(picker, { target: { value: "target" } });
    fireEvent.click(dialog.querySelector<HTMLButtonElement>(".editor-button-primary")!);
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ target_contracts: [{ target_key: "target", initial_visibility: "KNOWN" }] }),
      "identity-create",
    );
    expect(screen.getByText("target", { selector: "output.stable-identity-value" })).toBeInTheDocument();
    expect(window.document.querySelector('[data-field-path="action.repair.target_contracts.0.target_key"] input')).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "添加目标信息" }));
    const duplicateDialog = screen.getByRole("dialog", { name: "创建目标信息" });
    fireEvent.change(within(duplicateDialog).getByRole("combobox", { name: /目标节点/ }), { target: { value: "target" } });
    fireEvent.click(duplicateDialog.querySelector<HTMLButtonElement>(".editor-button-primary")!);
    expect(within(duplicateDialog).getByRole("alert")).toHaveTextContent(/\u5df2\u7ecf\u5b58\u5728\u8be5\u76ee\u6807\u7684\u4fe1\u606f\u914d\u7f6e/);
    expect(onChange).toHaveBeenCalledTimes(1);
    fireEvent.click(duplicateDialog.querySelector<HTMLButtonElement>(".editor-button-secondary")!);
  });

  it("edits Rule applicability through the Action target domain with name and key options", () => {
    const onChange = vi.fn();
    const targetDocument = {
      ...document,
      world: {
        ...(document.world as JsonObject),
        nodes: [{ key: "target", name: "Target", node_type_key: "facility", interaction_keys: ["inspect"], facts: [] }],
      },
      actions: [{ key: "repair", name: "Repair", target_kind: "NODE", required_interaction_key: "inspect", target_node_type_keys: ["facility"] }],
    };
    const rule = entity("rule", {
      key: "repair_target_rule", phase: "RESOLVE", trigger: "ACTION", action_key: "repair", priority: 1,
      applicable_target_keys: [], effects: [{ kind: "EMIT_OUTCOME", outcome_code: "DONE" }],
    });
    const { container } = render(<MemoryRouter><EntityHarness initial={rule} document={targetDocument} onChange={onChange} /></MemoryRouter>);
    const field = container.querySelector('[data-field-path="rule.repair_target_rule.applicable_target_keys"]') as HTMLElement;
    expect(field).not.toBeNull();
    fireEvent.click(within(field).getByRole("button"));
    const option = within(field).getByRole("option", { name: /Target/ });
    expect(option).toHaveTextContent("target");
    fireEvent.click(option);
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ applicable_target_keys: ["target"] }));
  });

  it("resolves an Action parameter deep link by its stable nested identity", () => {
    const action = entity("action", {
      key: "repair", name: "Repair", required_interaction_key: "inspect", execution_mode: "IMMEDIATE",
      parameters: [{ key: "count", name: "Count", value_type: "INTEGER", required: true, allowed_values: [] }],
      expected_outcomes: [], operation_bindings: [], planning: {},
    });
    render(<MemoryRouter><TypedEntityEditor entity={action} document={{ ...document, actions: [action.value] }} focusPath="action.repair.parameters.count.name" onChange={vi.fn()} /></MemoryRouter>);

    const parameterName = window.document.querySelector('[data-field-path="action.repair.parameters.0.name"]');
    expect(parameterName).toBeInTheDocument();
    expect(parameterName?.closest(".nested-card")?.querySelector(".nested-card-toggle")).toHaveAttribute("aria-expanded", "true");
  });

  it("restricts Actor AuthorityPolicy pickers to allowed Actions without preselecting a parameter", () => {
    const onChange = vi.fn();
    const allowed = { key: "allowed", name: "Allowed", parameters: [
      { key: "count", name: "Count", value_type: "INTEGER", required: true, allowed_values: [] },
      { key: "mode", name: "Mode", value_type: "ENUM", required: true, allowed_values: ["AUTO", "MANUAL"] },
    ] };
    const disallowed = { key: "disallowed", name: "Disallowed", parameters: [{ key: "secret", name: "Secret", value_type: "INTEGER", required: true, allowed_values: [] }] };
    const actor = entity("actor", { key: "agent", name: "Agent", allowed_action_keys: ["allowed"], doctrine: [], authority_policy: { autonomous_limits: [], approval_required_values: [] } });
    render(<MemoryRouter><EntityHarness initial={actor} document={{ ...document, actions: [allowed, disallowed] }} onChange={onChange} scenarioId="scenario-1" /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: /未识别结构/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "＋ 添加权限上限" }));
    const limitDialog = screen.getByRole("dialog", { name: "创建自主限制" });
    const limitPicker = within(limitDialog).getByRole("combobox", { name: /行动参数/ }) as HTMLSelectElement;
    expect(limitPicker).toHaveValue("");
    expect(Array.from(limitPicker.options).map((option) => option.value)).toEqual(["", "count"]);
    fireEvent.change(limitPicker, { target: { value: "count" } });
    expect(within(limitDialog).queryByLabelText(/Maximum/)).not.toBeInTheDocument();
    fireEvent.click(within(limitDialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ authority_policy: expect.objectContaining({ autonomous_limits: [{ parameter_key: "count" }] }) }), "identity-create");
    expectRequiredPath("actor.agent.authority_policy.autonomous_limits.0.maximum");
    expect(screen.getByRole("link", { name: "前往Allowed的行动参数" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/actions/allowed?focus_path=parameters.count");

    fireEvent.click(screen.getByRole("button", { name: "＋ 添加审批规则" }));
    const approvalDialog = screen.getByRole("dialog", { name: "创建需要批准的值" });
    const approvalPicker = within(approvalDialog).getByRole("combobox", { name: /行动参数/ }) as HTMLSelectElement;
    expect(approvalPicker).toHaveValue("");
    expect(Array.from(approvalPicker.options).map((option) => option.value)).toEqual(["", "count", "mode"]);
    fireEvent.change(approvalPicker, { target: { value: "mode" } });
    expect(within(approvalDialog).queryByLabelText(/要求值/)).not.toBeInTheDocument();
    fireEvent.click(within(approvalDialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ authority_policy: expect.objectContaining({ approval_required_values: [{ parameter_key: "mode" }] }) }), "identity-create");
    expectRequiredPath("actor.agent.authority_policy.approval_required_values.0.values");
  });

  it("routes an empty Actor AuthorityPolicy parameter picker to Actions without creating a row", () => {
    const onChange = vi.fn();
    const actor = entity("actor", { key: "agent", name: "Agent", allowed_action_keys: [], doctrine: [], authority_policy: { autonomous_limits: [], approval_required_values: [] } });
    const action = { key: "repair", name: "Repair", parameters: [{ key: "count", name: "Count", value_type: "INTEGER", required: true, allowed_values: [] }] };
    const { container } = render(<MemoryRouter><TypedEntityEditor entity={actor} document={{ ...document, actions: [action] }} scenarioId="s1" onChange={onChange} /></MemoryRouter>);

    const policyRows = container.querySelectorAll(".authority-policy-editor .nested-list");
    fireEvent.click(within(policyRows[1] as HTMLElement).getByRole("button"));
    const dialog = screen.getByRole("dialog", { name: "创建需要批准的值" });
    const picker = within(dialog).getByRole("combobox", { name: /行动参数/ }) as HTMLSelectElement;
    expect(Array.from(picker.options).map((option) => option.value)).toEqual([""]);
    expect(within(dialog).getByRole("link")).toHaveAttribute("href", "/scenarios/s1/edit/actions");
    fireEvent.click(dialog.querySelector<HTMLButtonElement>(".editor-button-primary")!);
    expect(screen.getByRole("dialog", { name: "创建需要批准的值" })).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(dialog.querySelector<HTMLButtonElement>(".editor-button-secondary")!);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("rejects duplicate ExpectedOutcome and OperationBinding identities before mutation", () => {
    const onChange = vi.fn();
    const action = entity("action", {
      key: "repair", name: "Repair", required_interaction_key: "inspect", execution_mode: "IMMEDIATE",
      parameters: [], expected_outcomes: [{ code: "COMPLETED", name: "Completed", success: true }],
      operation_bindings: [{ role: "source_region", value_type: "REGION", source: "EXPLICIT", description: "" }], planning: {},
    });
    const { container } = render(<MemoryRouter><TypedEntityEditor entity={action} document={document} onChange={onChange} /></MemoryRouter>);
    const actionLists = container.querySelectorAll(".typed-specialized-editor > .nested-list");

    fireEvent.click((actionLists[1] as HTMLElement).querySelector<HTMLButtonElement>(".typed-array-heading button")!);
    const outcomeDialog = screen.getByRole("dialog", { name: "创建结果" });
    fireEvent.change(within(outcomeDialog).getByLabelText(/结果代码/), { target: { value: "COMPLETED" } });
    fireEvent.click(outcomeDialog.querySelector<HTMLButtonElement>(".editor-button-primary")!);
    expect(within(outcomeDialog).getByRole("alert")).toHaveTextContent(/\u5df2\u7ecf\u5b58\u5728\u76f8\u540c\u7ed3\u679c\u4ee3\u7801/);
    fireEvent.click(outcomeDialog.querySelector<HTMLButtonElement>(".editor-button-secondary")!);

    fireEvent.click((actionLists[4] as HTMLElement).querySelector<HTMLButtonElement>(".typed-array-heading button")!);
    const bindingDialog = screen.getByRole("dialog", { name: "创建操作绑定" });
    fireEvent.change(within(bindingDialog).getByLabelText(/绑定角色/), { target: { value: "source_region" } });
    fireEvent.click(bindingDialog.querySelector<HTMLButtonElement>(".editor-button-primary")!);
    expect(within(bindingDialog).getByRole("alert")).toHaveTextContent(/\u5df2\u7ecf\u5b58\u5728\u76f8\u540c\u7ed1\u5b9a\u89d2\u8272/);
    fireEvent.click(bindingDialog.querySelector<HTMLButtonElement>(".editor-button-secondary")!);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("keeps a newly created ExpectedOutcome selected by identity after reorder", () => {
    const action = entity("action", {
      key: "repair", name: "Repair", required_interaction_key: "inspect", execution_mode: "IMMEDIATE",
      parameters: [], expected_outcomes: [{ code: "STARTED", name: "Started", success: false }, { code: "COMPLETED", name: "Completed", success: true }],
      operation_bindings: [], planning: {},
    });
    const { container } = render(<MemoryRouter><EntityHarness initial={action} /></MemoryRouter>);
    const actionLists = container.querySelectorAll(".typed-specialized-editor > .nested-list");

    fireEvent.click((actionLists[1] as HTMLElement).querySelector<HTMLButtonElement>(".typed-array-heading button")!);
    const dialog = screen.getByRole("dialog", { name: "创建结果" });
    fireEvent.change(within(dialog).getByLabelText(/结果代码/), { target: { value: "REPAIRED" } });
    fireEvent.click(dialog.querySelector<HTMLButtonElement>(".editor-button-primary")!);

    const selectedToggle = Array.from(container.querySelectorAll<HTMLButtonElement>(".nested-card-toggle")).find((button) => button.textContent?.includes("REPAIRED"));
    expect(selectedToggle).toHaveAttribute("aria-expanded", "true");
    const selectedCard = selectedToggle?.closest("article");
    expect(selectedCard).not.toBeNull();
    const reorderButton = selectedCard?.querySelector<HTMLButtonElement>(".reorder-button");
    expect(reorderButton).not.toBeNull();
    if (!reorderButton) throw new Error("Expected selected outcome reorder control.");
    fireEvent.click(reorderButton);
    expect(selectedToggle).toBeInTheDocument();
    expect(selectedToggle).toHaveAttribute("aria-expanded", "true");
    expect(within(selectedCard as HTMLElement).getByText("REPAIRED", { selector: "output.stable-identity-value" })).toBeInTheDocument();
  });

  it("shows a readable immutable summary for Derived dependencies without raw identity", () => {
    const { container } = render(<EntityHarness initial={entity("derived_state", {
      key: "derived",
      name: "Derived",
      description: "Derived state",
      value_type: "BOOLEAN",
      available_value: true,
      unavailable_value: false,
      dependencies: [{ kind: "FACT", node_key: "target", fact_key: "operational", accepted_values: [true], knowledge_gate: { node_key: "target", fact_key: "operational", accepted_values: [true] } }],
    })} />);
    const identity = container.querySelector('[data-field-path="derived_state.derived.key"]');
    expect(identity?.querySelector("output.stable-identity-value")).toHaveTextContent("derived");
    expect(identity?.querySelector("input")).toBeNull();
    expandNestedCards();
    expect(screen.getByRole("region", { name: "事实依赖" })).toBeInTheDocument();
    expect(screen.getByText("Target · Operational = 是")).toBeInTheDocument();
    expect(screen.getByText("要求值").parentElement).toHaveTextContent("是");
    expect(screen.getByText("此依赖的目标与要求值在创建时确定。如需修改依赖身份，请删除后重新创建。")).toBeInTheDocument();
    expect(screen.queryByText("Composite dependency identity")).not.toBeInTheDocument();
    expect(screen.queryByText(/\["FACT","target"/)).not.toBeInTheDocument();
    expect(screen.queryByText(/This dependency identity is fixed/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("依赖类型")).not.toBeInTheDocument();
  });

  it("creates Fact dependencies with Boolean selects, typed knowledge gates, and no early mutation", () => {
    const onChange = vi.fn();
    const derived = entity("derived_state", {
      key: "ready", name: "Ready", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [],
    });
    render(<MemoryRouter><EntityHarness initial={derived} document={document} onChange={onChange} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "＋ 添加事实依赖" }));
    const dialog = screen.getByRole("dialog", { name: "添加事实依赖" });
    const gateFact = within(dialog).getByRole("combobox", { name: "知识条件事实" }) as HTMLSelectElement;
    expect(within(dialog).getByText("选择哪个事实需要满足条件。")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "创建" })).toBeDisabled();
    fireEvent.change(within(dialog).getByRole("combobox", { name: /目标事实/ }), { target: { value: "target.operational" } });
    const acceptedValue = within(dialog).getByRole("combobox", { name: /要求值/ }) as HTMLSelectElement;
    expect(Array.from(acceptedValue.options).map((option) => option.textContent)).toEqual(["请选择…", "是", "否"]);
    fireEvent.change(acceptedValue, { target: { value: "true" } });
    fireEvent.change(gateFact, { target: { value: "target.operational" } });
    const gateValue = within(dialog).getByRole("combobox", { name: /已知时要求值/ }) as HTMLSelectElement;
    expect(gateValue).toHaveValue("");
    expect(within(dialog).getByRole("button", { name: "创建" })).toBeDisabled();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(gateValue, { target: { value: "false" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ dependencies: [expect.objectContaining({
      kind: "FACT", node_key: "target", fact_key: "operational", accepted_values: [true],
      knowledge_gate: { node_key: "target", fact_key: "operational", accepted_values: [false] },
    })] }), "identity-create");
    expect(screen.queryByRole("dialog", { name: "添加事实依赖" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "事实依赖" })).toBeInTheDocument();
  });

  it("localizes all dependency actions and keeps their internal kind values", () => {
    const derived = entity("derived_state", { key: "ready", name: "Ready", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [] });
    render(<MemoryRouter><EntityHarness initial={derived} /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "＋ 添加事实依赖" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "＋ 添加资源下限依赖" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "＋ 添加派生状态依赖" })).toBeInTheDocument();
    expect(screen.queryByText(/Add FACT dependency|Add RESOURCE AT LEAST dependency|Add DERIVED STATE dependency/)).not.toBeInTheDocument();
  });

  it("uses authored enum labels for dependencies while preserving tokens and leaving unlabeled values raw", () => {
    const enumDocument: JsonObject = {
      ...document,
      world: {
        ...(document.world as JsonObject),
        nodes: [{ key: "target", name: "Target", node_type_key: "facility", facts: [{ key: "mode", name: "Mode", value_type: "ENUM", allowed_values: ["AVAILABLE", "LOCKED"], value_labels: [{ value: "AVAILABLE", label: "可进入" }, { value: "LOCKED", label: "封闭" }] }] }],
      },
    };
    const derived = entity("derived_state", { key: "ready", name: "Ready", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [] });
    const onChange = vi.fn();
    render(<MemoryRouter><EntityHarness initial={derived} document={enumDocument} onChange={onChange} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "＋ 添加事实依赖" }));
    const dialog = screen.getByRole("dialog", { name: "添加事实依赖" });
    fireEvent.change(within(dialog).getByRole("combobox", { name: /目标事实/ }), { target: { value: "target.mode" } });
    const accepted = within(dialog).getByRole("combobox", { name: /要求值/ }) as HTMLSelectElement;
    expect(Array.from(accepted.options).map((option) => option.textContent)).toEqual(["请选择…", "可进入", "封闭"]);
    expect(Array.from(accepted.options).slice(1).map((option) => option.value)).toEqual(['["string","AVAILABLE"]', '["string","LOCKED"]']);
    fireEvent.change(accepted, { target: { value: '["string","AVAILABLE"]' } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ dependencies: [expect.objectContaining({ kind: "FACT", accepted_values: ["AVAILABLE"] })] }), "identity-create");

    cleanup();
    const noLabels: JsonObject = {
      ...enumDocument,
      world: { ...(enumDocument.world as JsonObject), nodes: [{ key: "target", name: "Target", node_type_key: "facility", facts: [{ key: "mode", name: "Mode", value_type: "ENUM", allowed_values: ["AVAILABLE", "LOCKED"] }] }] },
    };
    render(<MemoryRouter><EntityHarness initial={derived} document={noLabels} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "＋ 添加事实依赖" }));
    const rawDialog = screen.getByRole("dialog", { name: "添加事实依赖" });
    fireEvent.change(within(rawDialog).getByRole("combobox", { name: /目标事实/ }), { target: { value: "target.mode" } });
    const rawAccepted = within(rawDialog).getByRole("combobox", { name: /要求值/ }) as HTMLSelectElement;
    expect(Array.from(rawAccepted.options).map((option) => option.textContent)).toEqual(["请选择…", "AVAILABLE", "LOCKED"]);
  });

  it("creates Derived State dependencies with the selected target's typed Boolean domain", () => {
    const typedDocument: JsonObject = {
      ...document,
      derived_states: [
        ...(document.derived_states as JsonObject[]),
        { key: "other_state", name: "Other state", value_type: "BOOLEAN", allowed_values: [] },
      ],
    };
    const derived = entity("derived_state", { key: "ready", name: "Ready", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [] });
    const onChange = vi.fn();
    render(<MemoryRouter><EntityHarness initial={derived} document={typedDocument} onChange={onChange} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "＋ 添加派生状态依赖" }));
    const dialog = screen.getByRole("dialog", { name: "添加派生状态依赖" });
    expect(within(dialog).getByText("选择需要满足条件的派生状态。")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole("combobox", { name: /目标派生状态/ }), { target: { value: "other_state" } });
    const acceptedValue = within(dialog).getByRole("combobox", { name: /要求值/ }) as HTMLSelectElement;
    expect(Array.from(acceptedValue.options).map((option) => option.textContent)).toEqual(["请选择…", "是", "否"]);
    fireEvent.change(acceptedValue, { target: { value: "false" } });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ dependencies: [{ kind: "DERIVED_STATE", derived_key: "other_state", accepted_values: [false] }] }), "identity-create");
  });

  it("rejects duplicate dependency identity and cancellation leaves the Working Copy unchanged", () => {
    const existing = { kind: "FACT", node_key: "target", fact_key: "operational", accepted_values: [true] };
    const derived = entity("derived_state", { key: "ready", name: "Ready", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [existing] });
    const onChange = vi.fn();
    render(<MemoryRouter><EntityHarness initial={derived} onChange={onChange} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "＋ 添加事实依赖" }));
    const dialog = screen.getByRole("dialog", { name: "添加事实依赖" });
    fireEvent.change(within(dialog).getByRole("combobox", { name: /目标事实/ }), { target: { value: "target.operational" } });
    fireEvent.change(within(dialog).getByRole("combobox", { name: /要求值/ }), { target: { value: "true" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("此依赖身份已存在");
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog", { name: "添加事实依赖" })).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("clears a typed dependency value when its selected Fact domain changes", () => {
    const typedDocument: JsonObject = {
      ...document,
      world: { ...(document.world as JsonObject), nodes: [{ key: "target", name: "Target", node_type_key: "facility", facts: [
        { key: "ready", name: "Ready", value_type: "BOOLEAN" },
        { key: "mode", name: "Mode", value_type: "ENUM", allowed_values: ["AUTO", "MANUAL"] },
      ] }] },
    };
    const derived = entity("derived_state", { key: "ready_state", name: "Ready state", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [] });
    render(<MemoryRouter><EntityHarness initial={derived} document={typedDocument} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "＋ 添加事实依赖" }));
    const dialog = screen.getByRole("dialog", { name: "添加事实依赖" });
    fireEvent.change(within(dialog).getByRole("combobox", { name: /目标事实/ }), { target: { value: "target.ready" } });
    fireEvent.change(within(dialog).getByRole("combobox", { name: /要求值/ }), { target: { value: "true" } });
    fireEvent.change(within(dialog).getByRole("combobox", { name: /目标事实/ }), { target: { value: "target.mode" } });
    expect(within(dialog).getByRole("combobox", { name: /要求值/ })).toHaveValue("");
    expect(within(dialog).getByRole("button", { name: "创建" })).toBeDisabled();
  });

  it("uses a numeric resource threshold dialog and creates only a complete dependency", () => {
    const derived = entity("derived_state", { key: "ready", name: "Ready", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [] });
    const onChange = vi.fn();
    render(<MemoryRouter><EntityHarness initial={derived} onChange={onChange} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "＋ 添加资源下限依赖" }));
    const dialog = screen.getByRole("dialog", { name: "添加资源下限依赖" });
    expect(within(dialog).getByLabelText(/资源范围（区域）/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/范围类型|Scope kind/)).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole("combobox", { name: /^资源\s*\*/ }), { target: { value: "fuel" } });
    fireEvent.change(within(dialog).getByRole("combobox", { name: /资源范围（区域）/ }), { target: { value: "north" } });
    fireEvent.change(within(dialog).getByRole("spinbutton", { name: /最低数量/ }), { target: { value: "0" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ dependencies: [{ kind: "RESOURCE_AT_LEAST", region_key: "north", resource_key: "fuel", minimum: 0 }] }), "identity-create");
  });

  it("creates Doctrine identity before requiring its schema-required value in detail", () => {
    const onChange = vi.fn();
    const actor = entity("actor", { key: "agent", name: "Agent", role_key: "engineer", persona: "A field agent", doctrine: [], allowed_action_keys: [], authority_policy: { autonomous_limits: [], approval_required_values: [] } });
    const { container } = render(<MemoryRouter><EntityHarness initial={actor} onChange={onChange} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /添加行为准则/ }));
    const dialog = screen.getByRole("dialog", { name: "创建教义项" });
    expect(within(dialog).getByLabelText(/\u6559\u4e49\u952e/)).toHaveValue("");
    expect(within(dialog).queryByLabelText(/Value/)).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText(/\u6559\u4e49\u952e/), { target: { value: "safety" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ doctrine: [{ key: "safety" }] }), "identity-create");
    expectRequiredPath("actor.agent.doctrine.0.value");
    expect(container.querySelector('[data-field-path="actor.agent.doctrine.0.key"] output.stable-identity-value')).toHaveTextContent("safety");
    expect(container.querySelector(".advanced-json-control")).toBeNull();
  });

  it("marks Derived State allowed values only for the ENUM variant", () => {
    render(<EntityHarness initial={entity("derived_state", {
      key: "mode", name: "Mode", value_type: "ENUM", available_value: "AUTO", unavailable_value: "MANUAL", allowed_values: [], dependencies: [{ kind: "FACT", node_key: "target", fact_key: "operational", accepted_values: [true] }],
    })} />);
    let allowedValues = window.document.querySelector('[data-field-path="derived_state.mode.allowed_values"]');
    expect(allowedValues?.querySelector(".required-marker")).toBeInTheDocument();
    expect(allowedValues?.querySelector('[role="alert"]')).toBeInTheDocument();

    fireEvent.change(window.document.querySelector('[data-field-path="derived_state.mode.value_type"] select') as HTMLSelectElement, { target: { value: "BOOLEAN" } });
    allowedValues = window.document.querySelector('[data-field-path="derived_state.mode.allowed_values"]');
    expect(allowedValues?.querySelector(".required-marker")).toBeNull();
    expect(allowedValues?.querySelector('[role="alert"]')).toBeNull();
  });

  it("uses the typed canonical value as ValueLabel identity instead of its list index", () => {
    const onChange = vi.fn();
    const derived = entity("derived_state", {
      key: "operating_mode", name: "Operating mode", value_type: "ENUM", allowed_values: ["AUTO", "MANUAL"], value_labels: [], dependencies: [],
    });
    render(<MemoryRouter><EntityHarness initial={derived} document={document} onChange={onChange} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /添加值标签/ }));
    const dialog = screen.getByRole("dialog", { name: "创建值标签" });
    const canonicalValue = within(dialog).getByRole("combobox", { name: /\u89c4\u8303\u503c/ }) as HTMLSelectElement;
    expect(Array.from(canonicalValue.options).map((option) => option.value)).toEqual(["", '["string","AUTO"]', '["string","MANUAL"]']);
    fireEvent.change(canonicalValue, { target: { value: '["string","AUTO"]' } });
    expect(within(dialog).queryByLabelText(/\u663e\u793a\u6807\u7b7e/)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ value_labels: [{ value: "AUTO" }] }), "identity-create");
    expectRequiredPath("derived_state.operating_mode.value_labels.0.label");
    const displayLabel = window.document.querySelector<HTMLInputElement>('[data-field-path="derived_state.operating_mode.value_labels.0.label"] input');
    expect(displayLabel).not.toBeNull();
    fireEvent.change(displayLabel!, { target: { value: "Automatic" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ value_labels: [{ value: "AUTO", label: "Automatic" }] }));

    cleanup();
    const withLabel = entity("derived_state", { ...derived.value, value_labels: [{ value: "AUTO", label: "Automatic" }] });
    render(<MemoryRouter><TypedEntityEditor entity={withLabel} document={document} onChange={vi.fn()} /></MemoryRouter>);
    expect(screen.getByText("string: AUTO", { selector: "output.stable-identity-value" })).toBeInTheDocument();
    expect(screen.queryByDisplayValue("string: AUTO")).not.toBeInTheDocument();
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
    const labelDialog = screen.getByRole("dialog", { name: "创建值标签" });
    fireEvent.change(within(labelDialog).getByRole("combobox", { name: /\u89c4\u8303\u503c/ }), { target: { value: "true" } });
    expect(within(labelDialog).queryByLabelText(/\u663e\u793a\u6807\u7b7e/)).not.toBeInTheDocument();
    fireEvent.click(within(labelDialog).getByRole("button", { name: "创建" }));
    expect(screen.getByText("boolean: true")).toBeInTheDocument();
    expectRequiredPath("derived_state.ready.value_labels.0.label");
  });

  it("renders scenario locality as typed overview fields", () => {
    render(<TypedEditor section="overview" value={{ key: "scenario", name: "Scenario", description: "", locality: { enabled: true, scoped_resources: true, region_node_type_key: "facility", facility_node_type_key: "facility", transport_node_type_key: "facility", located_in_relation_type_key: "contains", transport_endpoint_relation_type_key: "contains" } }} document={document} onChange={vi.fn()} />);
    expect(screen.getByLabelText(/区域节点类型/)).toHaveValue("facility");
    expect(screen.getByLabelText(/归属关系类型/)).toHaveValue("contains");
  });

  it("shows scoped Fact deletion and locality-only NodeType semantics", () => {
    const onDeleteFact = vi.fn();
    render(<MemoryRouter><TypedEntityEditor entity={entity("node", { key: "target", name: "Target", facts: [{ key: "operational", name: "Operational", value_type: "BOOLEAN", initial_value: false }] })} document={document} onDeleteFact={onDeleteFact} onChange={vi.fn()} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "删除事实" }));
    expect(onDeleteFact).toHaveBeenCalledWith("target", "operational");

    cleanup();
    const syntheticDocument = { ...document, metadata: { locality: { enabled: true, facility_node_type_key: "hospital_like_name" } } };
    render(<MemoryRouter><TypedEntityEditor entity={entity("node_type", { key: "hospital_like_name", name: "Hospital-like name" })} document={syntheticDocument} onChange={vi.fn()} scenarioId="scenario-1" /></MemoryRouter>);
    expect(screen.getByText("设施")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "配置空间语义" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/overview?focus_path=locality");

    cleanup();
    render(<MemoryRouter><TypedEntityEditor entity={entity("node_type", { key: "facility", name: "Facility" })} document={document} onChange={vi.fn()} /></MemoryRouter>);
    expect(screen.getByText("通用节点")).toBeInTheDocument();
  });

  it("keeps optional multi-reference fields unrequired while linking selected targets and flagging stale references", () => {
    const onChange = vi.fn();
    const interactions = [{ key: "inspect", name: "Inspect" }];
    const node = entity("node", { key: "target", name: "Target", node_type_key: "facility", interaction_keys: ["inspect"] });
    render(<MemoryRouter><TypedEntityEditor entity={node} document={{ ...document, interactions }} scenarioId="scenario-1" onChange={onChange} /></MemoryRouter>);

    const field = window.document.querySelector('[data-field-path="node.target.interaction_keys"]');
    expect(field?.querySelector(".required-marker")).toBeNull();
    expect(field?.querySelector('[role="alert"]')).toBeNull();
    expect(within(field as HTMLElement).getByRole("link", { name: "前往Inspect" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/interactions/inspect");
    expect(within(field as HTMLElement).getByRole("link", { name: "前往交互" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/interactions");

    cleanup();
    const staleNode = entity("node", { key: "target", name: "Target", node_type_key: "facility", interaction_keys: ["removed_interaction"] });
    render(<MemoryRouter><TypedEntityEditor entity={staleNode} document={{ ...document, interactions }} scenarioId="scenario-1" onChange={onChange} /></MemoryRouter>);
    const staleField = window.document.querySelector('[data-field-path="node.target.interaction_keys"]');
    expect(staleField?.querySelector(".required-marker")).toBeNull();
    expect(staleField?.querySelector('[role="alert"]')).toHaveTextContent(/\u8bf7\u9009\u62e9\u73b0\u6709\u9009\u9879/);
  });

  it("authors initialization availability requirements and root planning/goal/public sections", () => {
    const onInitializationChange = vi.fn();
    render(<TypedEditor section="initialization" value={{ resource_initial_states: [], resource_pools: [{ pool_key: "fuel_pool", resource_key: "fuel", quantity: 5 }], region_resource_knowledge: [] }} document={document} onChange={onInitializationChange} />);
    expect(screen.getByText(/\u521d\u59cb\u5316\u6761\u76ee\u5728\u5404\u81ea\u7684\u89c4\u8303\u5f52\u5c5e\u9875\u9762\u521b\u5efa\u548c\u7f16\u8f91/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往初始化" })).toHaveAttribute("href", "../initialization");
    expect(screen.queryByRole("button", { name: /Add pool|Add initial state|Add region state/ })).not.toBeInTheDocument();
    expect(onInitializationChange).not.toHaveBeenCalled();
    cleanup();
    render(<TypedEditor section="planning" value={{ instructions: ["Plan safely"], recovery_hints: [{ failure_code: "BLOCKED", hint: "Inspect again" }] }} document={document} onChange={vi.fn()} />);
    expect(screen.getByText("从左侧选择一个对象", { exact: true })).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Plan safely")).not.toBeInTheDocument();
    cleanup();
    const onGoalResolutionChange = vi.fn();
    render(<TypedEditor section="goal-resolution" value={{ quick_inputs: ["First", "Second"] }} document={document} collectionSelection={{ owner: "quick-input", index: 1 }} onChange={onGoalResolutionChange} onQuickInputChange={(index, content) => onGoalResolutionChange({ quick_inputs: ["First", content] })} onQuickInputMove={() => onGoalResolutionChange({ quick_inputs: ["Second", "First"] })} onQuickInputRemove={vi.fn()} />);
    expect(screen.getByLabelText("目标文本")).toHaveValue("Second");
    expect(screen.queryByLabelText("????????")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("????")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("????????")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("目标文本"), { target: { value: "Updated" } });
    expect(onGoalResolutionChange).toHaveBeenLastCalledWith({ quick_inputs: ["First", "Updated"] });
  });

  it("edits and clears the optional source hint on its Resource owner", () => {
    const onChange = vi.fn();
    const resource = entity("resource", {
      key: "fuel",
      name: "Fuel",
      source_hint: { primary_region_key: "north", candidate_region_keys: [] },
    });
    render(<MemoryRouter><TypedEntityEditor entity={resource} document={document} onChange={onChange} /></MemoryRouter>);

    const primaryRegion = screen.getByLabelText("主要区域");
    expect(primaryRegion).toHaveValue("north");
    expect(Array.from((primaryRegion as HTMLSelectElement).options).map((option) => option.value)).toContain("north");
    expect(Array.from((primaryRegion as HTMLSelectElement).options).map((option) => option.value)).not.toContain("target");
    fireEvent.change(primaryRegion, { target: { value: "north" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      key: "fuel",
      source_hint: { primary_region_key: "north", candidate_region_keys: [] },
    }));

    fireEvent.click(screen.getByRole("button", { name: "清除来源提示" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.not.objectContaining({ source_hint: expect.anything() }));
  });

  it("renders the same empty source-hint state for a generic Resource", () => {
    const resource = entity("resource", { key: "generic_supply", name: "Generic Supply" });
    const onChange = vi.fn();
    render(<MemoryRouter><EntityHarness initial={resource} onChange={onChange} /></MemoryRouter>);

    expect(screen.getByText(/\u6765\u6e90\u63d0\u793a\u7531\u8d44\u6e90\u5b9a\u4e49/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /\u914d\u7f6e\u6765\u6e90\u63d0\u793a/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ source_hint: { primary_region_key: null, candidate_region_keys: [] } }));
    expect(screen.getByRole("combobox", { name: /主要区域/ })).toHaveValue("");
    fireEvent.change(screen.getByRole("combobox", { name: /主要区域/ }), { target: { value: "north" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ source_hint: { primary_region_key: "north", candidate_region_keys: [] } }));
  });

  it("does not expose retired Goal Resolution platform policy fields", () => {
    render(<TypedEditor section="goal-resolution" value={{ quick_inputs: [] }} document={document} onChange={vi.fn()} />);
    expect(screen.getByText("从左侧选择一个对象")).toBeInTheDocument();
    expect(screen.queryByLabelText("????????")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("????")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("????????")).not.toBeInTheDocument();
  });

  it("removes recovery hint authoring and falls back to planning instructions", () => {
    render(<TypedEditor section="planning-recovery" value={{ instructions: ["Plan safely"], recovery_hints: [{ failure_code: "BLOCKED", hint: "Inspect again" }] }} document={document} collectionSelection={null} onChange={vi.fn()} onCollectionChange={vi.fn()} onCollectionRemove={vi.fn()} />);
    expect(screen.getByText("从左侧选择一个对象")).toBeInTheDocument();
    expect(screen.queryByText("BLOCKED")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("Plan safely")).not.toBeInTheDocument();

    cleanup();
    const onInstructionChange = vi.fn();
    render(<TypedEditor section="planning-instructions" value={{ instructions: ["Plan safely", "Recover safely"] }} document={document} collectionSelection={{ owner: "instruction", index: 1 }} onChange={vi.fn()} onCollectionChange={vi.fn()} onCollectionRemove={vi.fn()} onInstructionChange={onInstructionChange} />);
    expect(screen.getByDisplayValue("Recover safely")).toBeInTheDocument();
    expect(screen.queryByText("BLOCKED")).not.toBeInTheDocument();
  });

  it("keeps unknown future AST variants editable through an isolated JSON fallback", () => {
    render(<EntityHarness initial={entity("rule", {
      key: "future-rule",
      phase: "RESOLVE",
      trigger: "STATE",
      priority: 0,
      condition: { kind: "FUTURE_CONDITION", payload: { enabled: true } },
      effects: [{ kind: "FUTURE_EFFECT", payload: 7 }],
    })} />);
    expandNestedCards();
    const unknownStructureButtons = screen.getAllByRole("button", { name: /未识别结构/ });
    expect(unknownStructureButtons).toHaveLength(2);
    expect(screen.getAllByText("当前结构无法由可视编辑器完整编辑；此处 JSON 仅用于兼容或高级修复。已折叠时保持原内容。")).toHaveLength(2);
    fireEvent.click(unknownStructureButtons[0]);
    fireEvent.click(unknownStructureButtons[1]);

    const fallbackEditors = Array.from(window.document.querySelectorAll<HTMLTextAreaElement>(".advanced-json-control"));
    expect(fallbackEditors).toHaveLength(2);
    expect(fallbackEditors[0].value).toContain("FUTURE_CONDITION");
    expect(fallbackEditors[1].value).toContain("FUTURE_EFFECT");
    expect(screen.queryByLabelText("条件类型")).not.toBeInTheDocument();
  });
});
