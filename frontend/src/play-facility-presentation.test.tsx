import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { KnownWorldAccordions } from "./pages/GamePage";
import {
  buildFacilityDetailRows,
  buildFacilityResourceRows,
} from "./playFacilityPresentation";
import type { FacilityPresentationOrderPolicy } from "./playFacilityPresentation";
import type {
  PlayerGameState,
  PublicActionRequirement,
  PublicProducerBinding,
  PublicSourceRequirement,
  ResourceIntelligence,
} from "./types";

const facilityNode: PlayerGameState["visible_nodes"][number] = {
  key: "test_facility",
  name: "测试设施",
  accessible: true,
  node_type_key: "facility",
  region_key: "test_region",
  region_name: "测试区域",
};

const regionNode: PlayerGameState["visible_nodes"][number] = {
  key: "test_region",
  name: "测试区域",
  accessible: true,
  node_type_key: "region",
  region_key: "test_region",
  region_name: "测试区域",
};

function knownFact(
  factKey: string,
  value: string | number | boolean,
  name = factKey,
): PlayerGameState["known_facts"][number] {
  return {
    node_key: facilityNode.key,
    fact_key: factKey,
    name,
    value,
    node_name: facilityNode.name,
    node_type_key: "facility",
    region_key: "test_region",
    region_name: "测试区域",
  };
}

function resourceIntelligence(): ResourceIntelligence {
  return {
    total_regions: 1,
    visible_region_count: 1,
    regions: {
      test_region: {
        region_name: "测试区域",
        resource_inventory_visibility: "VISIBLE",
        resource_survey_completed: true,
        resources: {
          repair_parts: {
            resource_name: "维修零件",
            known_total: 30,
            known_available: 30,
            pools: [{
              pool_key: "test-pool",
              quantity: 30,
              facility_key: facilityNode.key,
              facility_name: facilityNode.name,
              availability: "AVAILABLE",
            }],
          },
        },
      },
    },
    global_resources: {},
  };
}

function powerSourceRequirement(
  status: PublicSourceRequirement["status"],
): PublicSourceRequirement {
  return {
    source_node_key: facilityNode.key,
    kind: "POWER_SOURCE_READINESS",
    status,
    conditions: [
      { fact_key: "operational", operator: "EQ", value: true },
      { fact_key: "power_supply", operator: "EQ", value: "AVAILABLE" },
    ],
  };
}

function supplyPowerRequirement(
  sourceRequirements: PublicSourceRequirement[],
): PublicActionRequirement {
  return {
    action_key: "supply_power",
    action_name: "恢复供电",
    source_relation_type_key: "supplies_power_to",
    known_preconditions: [],
    source_requirements: sourceRequirements,
  };
}

function renderKnownWorld(
  knownFacts: PlayerGameState["known_facts"] = [],
  overrides: Partial<React.ComponentProps<typeof KnownWorldAccordions>> = {},
) {
  return render(
    <KnownWorldAccordions
      resources={[]}
      visibleNodes={[regionNode, facilityNode]}
      actors={[]}
      knownFacts={knownFacts}
      {...overrides}
    />,
  );
}

function openFacility() {
  const locations = within(screen.getByTestId("knowledge-accordion-locations"));
  fireEvent.click(locations.getByRole("button"));
  fireEvent.click(locations.getByText("测试区域"));
  const card = screen.getByTestId("facility-card-test_facility");
  fireEvent.click(card.querySelector("summary")!);
  return card;
}

function detailRows({
  facts = [],
  relations = [],
  resources = [],
  actionRequirements = [],
  producerBindings = [],
  presentationOrderPolicy,
}: {
  facts?: PlayerGameState["known_facts"];
  relations?: NonNullable<PlayerGameState["known_relations"]>;
  resources?: ReturnType<typeof buildFacilityResourceRows>;
  actionRequirements?: NonNullable<PlayerGameState["known_action_requirements"]>;
  producerBindings?: PublicProducerBinding[];
  presentationOrderPolicy?: FacilityPresentationOrderPolicy;
}) {
  const nodes = new Map([[facilityNode.key, facilityNode]]);
  return buildFacilityDetailRows({
    nodeKey: facilityNode.key,
    knownFacts: facts.filter((fact) => fact.node_key === facilityNode.key),
    knownRelations: relations,
    knownActionRequirements: actionRequirements,
    producerBindings,
    facilityResourceRows: resources,
    resourceName: (key) => key === "emergency_fuel" ? "应急燃料" : key,
    resolveNodeName: (key, candidate) => candidate ?? nodes.get(key)?.name ?? "相关设施",
    presentationOrderPolicy,
  });
}

describe("PLAY Facility presentation prototype", () => {
  afterEach(() => cleanup());

  it("keeps base status pills in the header without repeating them in details", () => {
    renderKnownWorld([
      knownFact("operational", true, "运行状态"),
      knownFact("power_supply", "AVAILABLE", "供电状态"),
    ]);

    const facility = openFacility();
    const summary = facility.querySelector("summary")!;
    const details = facility.querySelector(".knowledge-facility-details") as HTMLElement;

    expect(summary).toHaveTextContent("设备正常");
    expect(summary).toHaveTextContent("已供电");
    expect(details).toHaveTextContent("运行状态：运行中");
    expect(details).toHaveTextContent("供电状态：已供电");
    expect(details).not.toHaveTextContent("STATUS");
    expect(details).not.toHaveTextContent("RECOVERY / ACTIONS");
    expect(details).not.toHaveTextContent("RESOURCES");
    expect(details).not.toHaveTextContent("RELATIONS");
    expect(details).not.toHaveTextContent("SPECIAL / DETAIL FACTS");
  });

  it("keeps a revealed special fact in the flat detail list and hides it before reveal", () => {
    const view = renderKnownWorld();
    let facility = openFacility();
    expect(within(facility).queryByText("应急供电：")).not.toBeInTheDocument();

    view.rerender(
      <KnownWorldAccordions
        resources={[]}
        visibleNodes={[regionNode, facilityNode]}
        actors={[]}
        knownFacts={[knownFact("emergency_power", true, "应急供电")]}
      />,
    );
    facility = screen.getByTestId("facility-card-test_facility");
    const details = facility.querySelector(".knowledge-facility-details") as HTMLElement;
    expect(details).toHaveTextContent("应急供电：");
    expect(details).toHaveTextContent("已恢复");
    expect(details).not.toHaveTextContent("SPECIAL / DETAIL FACTS");
  });

  it("deduplicates a resource key across safe sources and merges safe metadata", () => {
    const nodeWithAssociatedResource = {
      ...facilityNode,
      associated_known_resources: [{
        resource_key: "repair_parts",
        resource_name: "维修零件",
        quantity: 30,
        availability: "UNAVAILABLE",
        availability_requirement: {
          node_key: facilityNode.key,
          fact_key: "operational",
          value: true,
        },
        availability_requirement_status: "KNOWN",
      }],
    };
    renderKnownWorld([], {
      resourceIntelligence: resourceIntelligence(),
      visibleNodes: [regionNode, nodeWithAssociatedResource],
    });

    const facility = openFacility();
    const details = facility.querySelector(".knowledge-facility-details") as HTMLElement;
    expect(within(details).getAllByText("维修零件：")).toHaveLength(1);
    expect(details).toHaveTextContent("维修零件：×30");
    expect(details).toHaveTextContent("暂不可用");
    expect(details).toHaveTextContent("解锁条件：测试设施满足解锁条件");
    expect(details).not.toHaveTextContent("RESOURCES");
  });

  it("does not show a facility resource before reveal and shows one revealed safe row", () => {
    const view = renderKnownWorld();
    let facility = openFacility();
    expect(within(facility).queryByText("维修零件：")).not.toBeInTheDocument();

    view.rerender(
      <KnownWorldAccordions
        resources={[]}
        resourceIntelligence={resourceIntelligence()}
        visibleNodes={[regionNode, facilityNode]}
        actors={[]}
        knownFacts={[]}
      />,
    );
    facility = screen.getByTestId("facility-card-test_facility");
    const details = facility.querySelector(".knowledge-facility-details") as HTMLElement;
    expect(within(details).getByText("维修零件：")).toBeVisible();
    expect(details).toHaveTextContent("×30");
    expect(details).not.toHaveTextContent("RESOURCES");
  });

  it("shows a public recovery identity without exposing a hidden dependency state", () => {
    const rows = detailRows({
      facts: [knownFact("rail_freight_capability", "UNAVAILABLE", "铁路货运能力")],
      producerBindings: [{
        binding_key: "stabilize-rail:test_facility",
        action_key: "repair_facility",
        action_name: "修复设施",
        target_key: facilityNode.key,
        producer_kind: "ACTION_PRODUCED_STATE",
        outputs: [{
          semantic_key: "test_facility.rail_freight_capability",
          target_key: facilityNode.key,
          fact_key: "rail_freight_capability",
          desired_value: "AVAILABLE",
          status: "UNSATISFIED",
        }],
        requirements: [],
      }],
    });

    expect(rows).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: "铁路货运能力：", value: "不可用", kind: "DETAIL" }),
      expect.objectContaining({ label: "恢复方式：", value: "修复设施", kind: "RECOVERY" }),
    ]));
    expect(rows.map((row) => `${row.label}${row.value}`).join(" ")).not.toMatch(/当前条件|未满足|已满足/);
    expect(rows.map((row) => row.value).join(" ")).not.toContain("隐藏依赖");
  });

  it("keeps a visible special prerequisite as a normal action requirement row", () => {
    const rows = detailRows({
      facts: [
        knownFact("operational", false, "运行状态"),
        knownFact("power_supply", "UNAVAILABLE", "供电状态"),
      ],
      producerBindings: [{
        binding_key: "power:test_facility",
        action_key: "supply_power",
        action_name: "恢复供电",
        target_key: facilityNode.key,
        producer_kind: "ACTION_PRODUCED_STATE",
        outputs: [{
          semantic_key: "test_facility.power_supply",
          target_key: facilityNode.key,
          fact_key: "power_supply",
          desired_value: "AVAILABLE",
          status: "UNSATISFIED",
        }],
        requirements: [{
          key: "power:test_facility:fact:test_facility:operational",
          kind: "FACT",
          node_key: facilityNode.key,
          fact_key: "operational",
          operator: "EQ",
          value: true,
          status: "UNSATISFIED",
        }],
      }],
    });

    expect(rows).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: "前置条件：", value: expect.stringContaining("运行状态") }),
    ]));
  });

  it.each([
    ["rail_freight_capability", "UNAVAILABLE", "修复设施", "修复设施"],
    ["emergency_delivery_support", "UNAVAILABLE", "修复设施", "修复设施"],
    ["external_relief_supply_ready", false, "接收外部救援物资", "接收外部救援物资"],
  ] as const)("shows and removes the action-produced recovery method for %s", (factKey, unavailableValue, helper, actionName) => {
    const binding = {
      binding_key: `${actionName}:test_facility`,
      action_key: factKey === "external_relief_supply_ready" ? "receive_external_relief_supplies" : "repair_facility",
      action_name: actionName,
      target_key: facilityNode.key,
      producer_kind: "ACTION_PRODUCED_STATE" as const,
      outputs: [{
        semantic_key: `test_facility.${factKey}`,
        target_key: facilityNode.key,
        fact_key: factKey,
        desired_value: factKey === "external_relief_supply_ready" ? true : "AVAILABLE",
        status: "UNSATISFIED" as const,
      }],
      requirements: [],
    };
    const unavailable = detailRows({
      facts: [knownFact(factKey, unavailableValue, factKey)],
      producerBindings: [binding],
    });
    expect(unavailable).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: "恢复方式：", value: helper, kind: "RECOVERY" }),
    ]));

    const satisfiedValue = factKey === "external_relief_supply_ready" ? true : "AVAILABLE";
    const satisfied = detailRows({
      facts: [knownFact(factKey, satisfiedValue, factKey)],
      producerBindings: [{
        ...binding,
        outputs: [{ ...binding.outputs[0], status: "SATISFIED" as const }],
      }],
    });
    expect(satisfied.some((row) => row.value === helper)).toBe(false);
  });

  it("keeps generating=false as two ordinary rows without inventing a recovery status", () => {
    const rows = detailRows({
      facts: [knownFact("generating", false, "发电状态")],
      producerBindings: [{
        binding_key: "generate_power:test_facility",
        target_key: facilityNode.key,
        action_key: "generate_power",
        action_name: "启动燃料应急发电",
        producer_kind: "ACTION_PRODUCED_STATE",
        outputs: [{
          semantic_key: "test_facility.generating",
          target_key: facilityNode.key,
          fact_key: "generating",
          desired_value: true,
          status: "UNSATISFIED",
        }],
        requirements: [
          { key: "generate_power:fuel", kind: "RESOURCE", resource_key: "emergency_fuel", scope: { kind: "CURRENT_TARGET_REGION" }, minimum: 50, status: "UNKNOWN" },
          { key: "generate_power:role", kind: "ROLE", display_name: "电力抢修队" },
        ],
      }],
    });

    expect(rows).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: "启动燃料应急发电：", value: "应急燃料 ×50" }),
      expect.objectContaining({ label: "发电状态：", value: "否" }),
      expect.objectContaining({ label: "所需队伍：", value: "电力抢修队" }),
    ]));
    expect(rows.some((row) => row.label === "恢复条件：")).toBe(false);
  });

  it("orders generic A/B/C blocks deterministically and anchors shared C after the ordered A rows", () => {
    const binding: PublicProducerBinding = {
      binding_key: "stabilize_facility:test_facility",
      action_key: "stabilize_facility",
      action_name: "稳定设施",
      target_key: facilityNode.key,
      producer_kind: "ACTION_PRODUCED_STATE",
      outputs: [
        {
          semantic_key: "test_facility.operational",
          target_key: facilityNode.key,
          fact_key: "operational",
          desired_value: true,
          status: "UNSATISFIED",
        },
        {
          semantic_key: "test_facility.custom_capability",
          target_key: facilityNode.key,
          fact_key: "custom_capability",
          desired_value: "READY",
          status: "UNSATISFIED",
        },
      ],
      requirements: [
        {
          key: "stabilize_facility:parts",
          kind: "RESOURCE",
          resource_key: "repair_parts",
          minimum: 2,
          status: "UNKNOWN",
        },
        {
          key: "stabilize_facility:role",
          kind: "ROLE",
          display_name: "通用设施队",
        },
      ],
    };
    const facts = [
      knownFact("custom_capability", "NOT_READY", "自定义能力"),
      knownFact("power_supply", "AVAILABLE", "供电状态"),
      knownFact("operational", false, "运行状态"),
    ];
    const rows = detailRows({ facts, producerBindings: [binding] });
    const reversedRows = detailRows({ facts: facts.slice().reverse(), producerBindings: [binding] });
    const rowIdentity = (row: { label: string; value: string }) => `${row.label}${row.value}`;

    expect(rows.map(rowIdentity)).toEqual(reversedRows.map(rowIdentity));
    const labels = rows.map((row) => row.label);
    const primaryIndex = labels.indexOf("运行状态：");
    const secondaryIndex = labels.indexOf("供电状态：");
    const customIndex = labels.indexOf("自定义能力：");
    const firstRecoveryIndex = rows.findIndex((row) => row.label === "恢复方式：");
    const sharedResourceIndex = rows.findIndex((row) => row.label === "稳定设施：");

    expect(primaryIndex).toBeGreaterThanOrEqual(0);
    expect(primaryIndex).toBeLessThan(secondaryIndex);
    expect(secondaryIndex).toBeLessThan(customIndex);
    expect(firstRecoveryIndex).toBeGreaterThan(primaryIndex);
    expect(sharedResourceIndex).toBeGreaterThan(firstRecoveryIndex);
    expect(sharedResourceIndex).toBeLessThan(secondaryIndex);
    expect(rows.filter((row) => row.label === "稳定设施：")).toHaveLength(1);
    expect(rows.filter((row) => row.label === "恢复方式：")).toHaveLength(2);
    expect(rows.some((row) => row.value === "通用设施队")).toBe(true);
  });

  it("supports a synthetic non-Linjiang policy seam without sorting by display text", () => {
    const policy: FacilityPresentationOrderPolicy = {
      primaryFactKeys: ["primary_summary"],
      secondaryFactKeys: ["secondary_summary"],
    };
    const resources = [{
      key: "test_facility:resource:repair_parts",
      resourceKey: "repair_parts",
      resourceName: "Repair parts",
      quantity: 1,
      availability: "AVAILABLE" as const,
      availabilityRequirement: null,
      availabilityRequirementStatus: null,
    }];
    const relations = [{
      source_node_key: facilityNode.key,
      relation_type_key: "supplies_power_to",
      target_node_key: "target",
      target_node_name: "Target",
    }];
    const facts = [
      knownFact("custom_capability", true, "Custom capability"),
      knownFact("secondary_summary", true, "Secondary summary"),
      knownFact("primary_summary", true, "Primary summary"),
    ];
    const defaultRows = detailRows({ facts, resources, relations, presentationOrderPolicy: policy });
    const alternateRows = detailRows({
      facts,
      resources,
      relations,
      presentationOrderPolicy: {
        ...policy,
        groupOrder: ["SEMANTIC", "HEADER_PRIMARY", "HEADER_SECONDARY", "RESOURCE", "RELATION", "OTHER"],
      },
    });
    const defaultLabels = defaultRows.map((row) => row.label);
    const alternateLabels = alternateRows.map((row) => row.label);

    expect(defaultLabels.indexOf("Primary summary：")).toBeLessThan(defaultLabels.indexOf("Secondary summary："));
    expect(defaultLabels.indexOf("Secondary summary：")).toBeLessThan(defaultLabels.indexOf("Custom capability："));
    expect(defaultLabels.indexOf("Custom capability：")).toBeLessThan(defaultLabels.indexOf("Repair parts："));
    expect(defaultLabels.indexOf("Repair parts：")).toBeLessThan(defaultLabels.indexOf("可供电："));
    expect(alternateLabels.indexOf("Custom capability：")).toBeLessThan(alternateLabels.indexOf("Primary summary："));
    expect(alternateLabels.indexOf("Primary summary：")).toBeLessThan(alternateLabels.indexOf("Repair parts："));
    expect(alternateLabels.indexOf("Repair parts：")).toBeLessThan(alternateLabels.indexOf("可供电："));
  });

  it("renders power readiness only from the shared source requirement contract", () => {
    const relation = {
      source_node_key: facilityNode.key,
      relation_type_key: "supplies_power_to",
      target_node_key: "hospital",
      target_node_name: "中央医院",
    };
    const unknown = detailRows({
      facts: [
        knownFact("operational", true, "运行状态"),
        knownFact("power_supply", "AVAILABLE", "供电状态"),
      ],
      relations: [relation],
    });
    expect(unknown.some((row) => row.label === "送电能力：")).toBe(false);
    expect(unknown).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: "可供电：", value: "中央医院" }),
    ]));

    const notReady = detailRows({
      facts: [
        knownFact("operational", false, "运行状态"),
        knownFact("power_supply", "UNAVAILABLE", "供电状态"),
      ],
      relations: [relation],
      actionRequirements: [supplyPowerRequirement([powerSourceRequirement("UNSATISFIED")])],
    });
    expect(notReady).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: "送电能力：", value: "未具备" }),
      expect.objectContaining({ label: "恢复条件：", value: "设施恢复正常运行并恢复供电" }),
    ]));

    const ready = detailRows({
      facts: [
        knownFact("operational", true, "运行状态"),
        knownFact("power_supply", "AVAILABLE", "供电状态"),
      ],
      relations: [relation],
      actionRequirements: [supplyPowerRequirement([powerSourceRequirement("SATISFIED")])],
    });
    expect(ready).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: "送电能力：", value: "已具备" }),
    ]));
    expect(ready.some((row) => row.label === "恢复条件：")).toBe(false);
  });

  it.each([
    [false, "UNAVAILABLE"],
    [false, "AVAILABLE"],
    [true, "UNAVAILABLE"],
  ] as const)("uses the complete power helper for every known not-ready combination", (operational, powerSupply) => {
    const rows = detailRows({
      facts: [
        knownFact("operational", operational, "运行状态"),
        knownFact("power_supply", powerSupply, "供电状态"),
      ],
      actionRequirements: [supplyPowerRequirement([powerSourceRequirement("UNSATISFIED")])],
    });

    expect(rows).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: "送电能力：", value: "未具备" }),
      expect.objectContaining({ label: "恢复条件：", value: "设施恢复正常运行并恢复供电" }),
    ]));
  });

  it("does not infer readiness from raw facts when the shared source contract is absent", () => {
    const rows = detailRows({
      facts: [
        knownFact("operational", true, "运行状态"),
        knownFact("power_supply", "AVAILABLE", "供电状态"),
      ],
      relations: [{
        source_node_key: facilityNode.key,
        relation_type_key: "supplies_power_to",
        target_node_key: "hospital",
        target_node_name: "中央医院",
      }],
    });

    expect(rows.some((row) => row.label === "送电能力：")).toBe(false);
    expect(rows).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: "可供电：", value: "中央医院" }),
    ]));
  });

  it("does not accept an unscoped target effect as a facility producer", () => {
    const rows = detailRows({
      facts: [knownFact("rail_freight_capability", "UNAVAILABLE", "铁路货运能力")],
      producerBindings: [{
        binding_key: "repair_facility:test_facility",
        target_key: facilityNode.key,
        action_key: "repair_facility",
        action_name: "修复设施",
        producer_kind: "ACTION_PRODUCED_STATE",
        outputs: [{
          semantic_key: "other_facility.rail_freight_capability",
          target_key: "other_facility",
          fact_key: "rail_freight_capability",
          desired_value: "AVAILABLE",
          status: "UNSATISFIED",
        }],
        requirements: [],
      }],
    });

    expect(rows.some((row) => row.label === "恢复方式：")).toBe(false);
  });

  it("binds generic multi-output causal details once and reanchors them after regression", () => {
    const binding: PublicProducerBinding = {
      binding_key: "stabilize_core:test_facility",
      action_key: "stabilize_core",
      action_name: "Stabilize core",
      target_key: facilityNode.key,
      producer_kind: "ACTION_PRODUCED_STATE",
      outputs: [
        {
          semantic_key: "test_facility.coolant_pressure",
          target_key: facilityNode.key,
          fact_key: "coolant_pressure",
          desired_value: 80,
          status: "UNSATISFIED",
        },
        {
          semantic_key: "test_facility.system_mode",
          target_key: facilityNode.key,
          fact_key: "system_mode",
          desired_value: "ONLINE",
          status: "UNSATISFIED",
        },
      ],
      requirements: [
        {
          key: "stabilize_core:coolant",
          kind: "RESOURCE",
          resource_key: "coolant_cartridge",
          minimum: 3,
          status: "UNKNOWN",
        },
        {
          key: "stabilize_core:role",
          kind: "ROLE",
          display_name: "Core Stabilization Team",
        },
      ],
    };
    const facts = [
      knownFact("coolant_pressure", 10, "Coolant pressure"),
      knownFact("system_mode", "OFF", "System mode"),
    ];
    const rows = detailRows({ facts, producerBindings: [binding] });
    expect(rows.filter((row) => row.value === "Stabilize core")).toHaveLength(2);
    expect(rows.filter((row) => row.label === "Stabilize core：")).toHaveLength(1);
    expect(rows.some((row) => row.value === "Core Stabilization Team")).toBe(true);

    const reanchored = detailRows({
      facts,
      producerBindings: [{
        ...binding,
        outputs: [
          { ...binding.outputs[0], status: "SATISFIED" },
          { ...binding.outputs[1], status: "UNSATISFIED" },
        ],
      }],
    });
    expect(reanchored.filter((row) => row.value === "Stabilize core")).toHaveLength(1);
    expect(reanchored.filter((row) => row.label === "Stabilize core：")).toHaveLength(1);
    expect(reanchored.some((row) => row.value === "Core Stabilization Team")).toBe(true);

    const satisfied = detailRows({
      facts,
      producerBindings: [{
        ...binding,
        outputs: binding.outputs.map((output) => ({ ...output, status: "SATISFIED" as const })),
      }],
    });
    expect(satisfied.some((row) => row.value === "Stabilize core")).toBe(false);
    expect(satisfied.some((row) => row.value === "Core Stabilization Team")).toBe(false);
  });

  it("renders every public producer choice for one unsatisfied A", () => {
    const rows = detailRows({
      facts: [knownFact("system_mode", "OFF", "System mode")],
      producerBindings: [
        {
          binding_key: "stabilize_core:option-a",
          action_key: "stabilize_core_a",
          action_name: "Stabilize via grid",
          target_key: facilityNode.key,
          producer_kind: "ACTION_PRODUCED_STATE",
          outputs: [{
            semantic_key: "test_facility.system_mode",
            target_key: facilityNode.key,
            fact_key: "system_mode",
            desired_value: "ONLINE",
            status: "UNSATISFIED",
          }],
          requirements: [],
        },
        {
          binding_key: "stabilize_core:option-b",
          action_key: "stabilize_core_b",
          action_name: "Stabilize via backup",
          target_key: facilityNode.key,
          producer_kind: "ACTION_PRODUCED_STATE",
          outputs: [{
            semantic_key: "test_facility.system_mode",
            target_key: facilityNode.key,
            fact_key: "system_mode",
            desired_value: "ONLINE",
            status: "UNSATISFIED",
          }],
          requirements: [],
        },
      ],
    });

    expect(rows.filter((row) => row.label === "恢复方式：")).toEqual([
      expect.objectContaining({ value: "Stabilize via grid" }),
      expect.objectContaining({ value: "Stabilize via backup" }),
    ]);
  });

  it("keeps a public UNKNOWN requirement unknown instead of inferring false", () => {
    const rows = detailRows({
      facts: [knownFact("system_mode", "UNKNOWN", "System mode")],
      producerBindings: [{
        binding_key: "stabilize_core:test_facility",
        action_key: "stabilize_core",
        action_name: "Stabilize core",
        target_key: facilityNode.key,
        producer_kind: "ACTION_PRODUCED_STATE",
        outputs: [{
          semantic_key: "test_facility.system_mode",
          target_key: facilityNode.key,
          fact_key: "system_mode",
          desired_value: "ONLINE",
          status: "UNSATISFIED",
        }],
        requirements: [{
          key: "stabilize_core:mode",
          kind: "FACT",
          node_key: facilityNode.key,
          fact_key: "system_mode",
          status: "UNKNOWN",
        }],
      }],
    });

    expect(rows).toEqual(expect.arrayContaining([
      expect.objectContaining({ value: expect.stringContaining("当前状态未知") }),
    ]));
    expect(rows.map((row) => row.value).join(" ")).not.toContain("不可用");
    expect(rows.map((row) => row.value).join(" ")).not.toContain("未满足");
  });

  it("removes a resource unlock helper when the projected resource is available", () => {
    const resourceRows = buildFacilityResourceRows(
      facilityNode.key,
      undefined,
      [{
        resource_key: "coolant_cartridge",
        resource_name: "Coolant cartridge",
        quantity: 4,
        availability: "AVAILABLE",
        availability_requirement: {
          node_key: facilityNode.key,
          fact_key: "system_mode",
          value: "ONLINE",
        },
        availability_requirement_status: "KNOWN",
      }],
      (key, candidate) => candidate ?? key,
      (key) => key === facilityNode.key ? facilityNode.name : key,
      () => "System mode",
      [{
        binding_key: "resource_availability:test_facility:coolant_cartridge",
        action_key: "resource_availability:coolant_cartridge",
        action_name: "Coolant cartridge",
        target_key: facilityNode.key,
        producer_kind: "RESOURCE_AVAILABILITY",
        outputs: [{
          semantic_key: "test_facility.coolant_cartridge.availability",
          target_key: facilityNode.key,
          resource_key: "coolant_cartridge",
          desired_value: "AVAILABLE",
          status: "SATISFIED",
        }],
        requirements: [],
      }],
    );
    const rows = detailRows({ resources: resourceRows });

    expect(resourceRows[0]?.availability).toBe("AVAILABLE");
    expect(resourceRows[0]?.unlockText).toBeUndefined();
    expect(rows.some((row) => row.key.endsWith(":unlock"))).toBe(false);
  });
});
