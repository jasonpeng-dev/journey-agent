import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  factDisplayValue,
  facilityStatusDisplayValue,
} from "./knowledgePresentation";
import {
  legacyFactDisplayValue,
  legacyFacilityStatusDisplayValue,
} from "./legacyPresentationCompatibility";
import { KnownWorldAccordions } from "./pages/GamePage";
import { buildFacilityDetailRows } from "./playFacilityPresentation";
import { resolvePresentationProfilePreview } from "./presentationPolicy";
import type {
  PlayerGameState,
  PublicProducerBinding,
} from "./types";

type PublicFact = PlayerGameState["known_facts"][number];

const region = {
  key: "east_region",
  name: "东部地区",
  accessible: true,
  node_type_key: "region",
  node_family: "REGION" as const,
  region_key: "east_region",
  region_name: "东部地区",
};

const facility = {
  key: "accepted_facility",
  name: "东部配电站",
  accessible: true,
  node_type_key: "facility",
  node_family: "FACILITY" as const,
  region_key: "east_region",
  region_name: "东部地区",
};

function fact(
  factKey: string,
  name: string,
  value: string | number | boolean,
  presentationSlot: PublicFact["presentation_slot"] = "SEMANTIC",
): PublicFact {
  return {
    node_key: facility.key,
    fact_key: factKey,
    name,
    value,
    value_label: null,
    presentation_slot: presentationSlot,
    node_name: facility.name,
    node_type_key: "facility",
    node_family: "FACILITY",
    region_key: region.key,
    region_name: region.name,
  };
}

function standardPresentation(
  overrides: Partial<NonNullable<PlayerGameState["presentation"]>> = {},
): NonNullable<PlayerGameState["presentation"]> {
  return {
    ...resolvePresentationProfilePreview({
      schema_version: 1,
      template: "standard",
      family_overrides: [],
      semantic_overrides: [],
    }),
    ...overrides,
  };
}

function renderFacility(
  facts: PublicFact[],
  overrides: Partial<React.ComponentProps<typeof KnownWorldAccordions>> = {},
) {
  render(
    <KnownWorldAccordions
      presentation={standardPresentation()}
      resources={[]}
      visibleNodes={[region, facility]}
      actors={[]}
      knownFacts={facts}
      {...overrides}
    />,
  );
  fireEvent.click(screen.getByTestId("knowledge-accordion-locations").querySelector("button")!);
  fireEvent.click(screen.getByText(region.name));
  return screen.getByTestId(`facility-card-${facility.key}`);
}

describe("accepted standard PLAY presentation contract", () => {
  afterEach(cleanup);

  it("locks the accepted historical summary and detail wording", () => {
    const operational = fact("operational", "运行状态", false, "HEADER_PRIMARY");
    const power = fact("power_supply", "供电状态", "UNAVAILABLE", "HEADER_SECONDARY");

    expect(legacyFacilityStatusDisplayValue(operational)).toBe("待修复");
    expect(legacyFactDisplayValue(operational)).toBe("未运行");
    expect(legacyFactDisplayValue(power)).toBe("未供电");
  });

  it("applies accepted wording to historical facts carrying safe projection metadata", () => {
    const operational = fact("operational", "运行状态", false, "HEADER_PRIMARY");
    const power = fact("power_supply", "供电状态", "UNAVAILABLE", "HEADER_SECONDARY");

    expect(facilityStatusDisplayValue(operational)).toBe("待修复");
    expect(factDisplayValue(operational)).toBe("未运行");
    expect(factDisplayValue(power)).toBe("未供电");
    expect(factDisplayValue(power)).not.toMatch(/UNAVAILABLE|AVAILABLE/);
  });

  it("keeps A, B, and producer-owned C adjacent after final ordering", () => {
    const operational = fact("operational", "运行状态", false, "HEADER_PRIMARY");
    const power = fact("power_supply", "供电状态", "UNAVAILABLE", "HEADER_SECONDARY");
    const producer: PublicProducerBinding = {
      binding_key: "restore:accepted_facility",
      action_key: "restore_facility",
      action_name: "修复东部配电站",
      target_key: facility.key,
      producer_kind: "ACTION_PRODUCED_STATE",
      outputs: [{
        semantic_key: "accepted_facility.operational",
        target_key: facility.key,
        fact_key: "operational",
        desired_value: true,
        status: "UNSATISFIED",
      }],
      requirements: [
        {
          key: "parts",
          kind: "RESOURCE",
          resource_key: "repair_parts",
          minimum: 5,
          scope: { kind: "CURRENT_TARGET_REGION" },
          status: "UNKNOWN",
        },
        {
          key: "team",
          kind: "ROLE",
          display_name: "电力抢修队",
          status: "UNKNOWN",
        },
      ],
    };

    const rows = buildFacilityDetailRows({
      nodeKey: facility.key,
      knownFacts: [power, operational],
      knownRelations: [],
      knownActionRequirements: [],
      producerBindings: [producer],
      facilityResourceRows: [],
      resourceName: (key) => key === "repair_parts" ? "维修部件" : key,
      resolveNodeName: (_key, candidate) => candidate ?? facility.name,
    });

    expect(rows.map((row) => row.label)).toEqual([
      "运行状态：",
      "恢复方式：",
      "修复东部配电站：",
      "所需队伍：",
      "供电状态：",
    ]);
  });

  it("does not promote a historical supporting-only Fact into a main A row", () => {
    const rows = buildFacilityDetailRows({
      nodeKey: facility.key,
      knownFacts: [
        fact("operational", "运行状态", false, "HEADER_PRIMARY"),
        fact("repair_profile", "设施修复类型", "distribution_station"),
      ],
      knownRelations: [],
      knownActionRequirements: [],
      producerBindings: [],
      facilityResourceRows: [],
      resourceName: (key) => key,
      resolveNodeName: (_key, candidate) => candidate ?? facility.name,
    });

    expect(rows.some((row) => row.key.includes("repair_profile"))).toBe(false);
  });

  it("uses summary_slot to move Facility summaries out of the header", () => {
    const card = renderFacility(
      [
        fact("operational", "运行状态", false, "HEADER_PRIMARY"),
        fact("power_supply", "供电状态", "UNAVAILABLE", "HEADER_SECONDARY"),
      ],
      { presentation: standardPresentation({ summary_slot: "BODY" }) },
    );

    expect(card.querySelector("summary .knowledge-facility-statuses")).not.toBeInTheDocument();
  });

  it("uses knowledge_level A as a subtractive view of standard causality", () => {
    const producer: PublicProducerBinding = {
      binding_key: "restore:accepted_facility",
      action_key: "restore_facility",
      action_name: "修复东部配电站",
      target_key: facility.key,
      producer_kind: "ACTION_PRODUCED_STATE",
      outputs: [{
        semantic_key: "accepted_facility.operational",
        target_key: facility.key,
        fact_key: "operational",
        desired_value: true,
        status: "UNSATISFIED",
      }],
      requirements: [{
        key: "team",
        kind: "ROLE",
        display_name: "电力抢修队",
        status: "UNKNOWN",
      }],
    };
    const card = renderFacility(
      [fact("operational", "运行状态", false, "HEADER_PRIMARY")],
      {
        presentation: standardPresentation({ knowledge_level: "A" }),
        knownProducerBindings: [producer],
      },
    );
    fireEvent.click(card.querySelector("summary")!);
    const details = within(card).getByText("运行状态：").closest(".knowledge-facility-details")!;

    expect(details).not.toHaveTextContent("恢复方式");
    expect(details).not.toHaveTextContent("所需队伍");
  });

  it.fails("defines standard as the accepted causal and Actor baseline", () => {
    const resolved = resolvePresentationProfilePreview({
      schema_version: 1,
      template: "standard",
      family_overrides: [],
      semantic_overrides: [],
    });

    expect(resolved.knowledge_level).toBe("A+B+C");
    expect(resolved.actor_fields).toEqual([
      "NAME",
      "ROLE",
      "LOCATION",
      "COMMAND_REACHABILITY",
    ]);
  });

  it("does not treat task failure or disconnected command reachability as Actor activity failure", () => {
    render(
      <KnownWorldAccordions
        presentation={standardPresentation({
          actor_fields: ["NAME", "ROLE", "LOCATION", "STATUS", "COMMAND_REACHABILITY"],
        })}
        resources={[]}
        visibleNodes={[]}
        actors={[{
          key: "repair_team",
          name: "电力抢修队",
          role_name: "电力抢修",
          current_node_name: "东部地区",
          status: "IDLE",
          task_name: null,
          command_reachability: "DISCONNECTED",
        }]}
        knownFacts={[]}
      />,
    );
    fireEvent.click(screen.getByTestId("knowledge-accordion-actors").querySelector("button")!);

    expect(screen.getByText("待命中")).toBeVisible();
    expect(screen.getByText("失联")).toBeVisible();
    expect(screen.queryByText("失败")).not.toBeInTheDocument();
  });

  it("applies semantic_order to Facility semantic/resource/relation groups", () => {
    const rows = buildFacilityDetailRows({
      nodeKey: facility.key,
      knownFacts: [fact("operational", "\u8fd0\u884c\u72b6\u6001", false)],
      knownRelations: [{ source_node_key: facility.key, relation_type_key: "supports", target_node_key: "clinic", relation_type_name: "\u652f\u6301", target_node_name: "\u8bca\u6240" }],
      knownActionRequirements: [], producerBindings: [],
      facilityResourceRows: [{ key: "supply", resourceKey: "supply", resourceName: "\u8865\u7ed9", quantity: 2, availability: "AVAILABLE", availabilityRequirement: null, availabilityRequirementStatus: null }],
      resourceName: (key) => key,
      resolveNodeName: (_key, candidate) => candidate ?? "\u8bca\u6240",
      presentation: standardPresentation({ semantic_order: ["RELATIONS", "RESOURCES", "STATUS"] }),
    });
    expect(rows.map((row) => row.key)).toEqual([`${facility.key}:relation:0`, "supply", `${facility.key}:fact:operational`]);
  });

  it("applies resource_order to resolved Facility resource fields", () => {
    const rows = buildFacilityDetailRows({
      nodeKey: facility.key, knownFacts: [], knownRelations: [], knownActionRequirements: [], producerBindings: [],
      facilityResourceRows: [{ key: "parts", resourceKey: "parts", resourceName: "\u90e8\u4ef6", quantity: 5, availability: "UNAVAILABLE", availabilityRequirement: null, availabilityRequirementStatus: null, displayUnit: "\u7bb1" }],
      resourceName: (key) => key,
      resolveNodeName: (_key, candidate) => candidate ?? facility.name,
      presentation: standardPresentation({ resource_order: ["STATUS", "UNIT", "AMOUNT"] }),
    });
    expect(rows[0]).toMatchObject({ label: "\u8d44\u6e90\uff1a", value: "\u6682\u4e0d\u53ef\u7528\uff0c\u7bb1\uff0c\u00d75" });
  });

  it("applies relation_order to resolved Facility relation fields", () => {
    const rows = buildFacilityDetailRows({
      nodeKey: facility.key, knownFacts: [],
      knownRelations: [{ source_node_key: facility.key, relation_type_key: "supports", target_node_key: "clinic", relation_type_name: "\u652f\u6301", target_node_name: "\u8bca\u6240" }],
      knownActionRequirements: [], producerBindings: [], facilityResourceRows: [],
      resourceName: (key) => key,
      resolveNodeName: (_key, candidate) => candidate ?? "\u8bca\u6240",
      presentation: standardPresentation({ relation_order: ["TARGET"] }),
    });
    expect(rows[0]).toMatchObject({ label: "\u5173\u7cfb\uff1a", value: "\u8bca\u6240" });
  });

  it("uses authored generic semantic metadata for Medical, Space, and Investigation fixtures", () => {
    for (const [domain, key] of [["Medical", "triage"], ["Space", "oxygen"], ["Investigation", "evidence"]]) {
      const authored: PublicFact = { ...fact(key, `${domain} state`, "BLOCKED"), presentation_role: "HEADER_PRIMARY", summary_value_label: `${domain} summary`, detail_value_label: `${domain} detail` };
      expect(facilityStatusDisplayValue(authored)).toBe(`${domain} summary`);
      expect(factDisplayValue(authored)).toBe(`${domain} detail`);
    }
  });
});
