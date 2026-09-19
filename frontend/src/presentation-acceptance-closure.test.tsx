import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { factDisplayValue, facilityStatusDisplayValue } from "./knowledgePresentation";
import { KnownWorldAccordions } from "./pages/GamePage";
import { buildFacilityDetailRows, resolveFacilityFactRole } from "./playFacilityPresentation";
import { resolvePresentationProfilePreview } from "./presentationPolicy";
import type { PlayerGameState, PresentationTemplate, PublicProducerBinding } from "./types";

type PublicFact = PlayerGameState["known_facts"][number];

const profile = (template: PresentationTemplate) => resolvePresentationProfilePreview({
  schema_version: 1,
  template,
  family_overrides: [],
  semantic_overrides: [],
});

const authoredFact = (overrides: Partial<PublicFact> = {}): PublicFact => ({
  node_key: "clinic",
  fact_key: "clinical_readiness",
  name: "Clinical readiness",
  value: "BLOCKED",
  summary_value_label: "Needs treatment",
  detail_value_label: "Treatment not started",
  presentation_role: "HEADER_PRIMARY",
  node_family: "FACILITY",
  ...overrides,
});

describe("PLAY presentation acceptance closure", () => {
  afterEach(cleanup);

  it("keeps authored semantic identity and wording stable across all presets", () => {
    const fact = authoredFact();
    for (const template of ["compact", "standard", "detailed"] as const) {
      expect(profile(template).template).toBe(template);
      expect(resolveFacilityFactRole(fact)).toBe("HEADER_PRIMARY");
      expect(facilityStatusDisplayValue(fact)).toBe("Needs treatment");
      expect(factDisplayValue(fact)).toBe("Treatment not started");
    }
  });

  it("treats UNKNOWN as unknown rather than false", () => {
    const fact = authoredFact({
      value: "UNKNOWN",
      summary_value_label: "Unknown",
      detail_value_label: "Not yet observed",
    });
    expect(facilityStatusDisplayValue(fact)).toBe("Unknown");
    expect(factDisplayValue(fact)).toBe("Not yet observed");
    expect(factDisplayValue(fact)).not.toBe("No");

    const unlabeledUnknown = authoredFact({ value: "UNKNOWN", summary_value_label: null, detail_value_label: null });
    const unlabeledUnavailable = authoredFact({ value: "UNAVAILABLE", summary_value_label: null, detail_value_label: null });
    expect(factDisplayValue(unlabeledUnknown)).toBe("未知");
    expect(factDisplayValue(unlabeledUnavailable)).toBe("不可用");
  });

  it("makes detailed supporting rows additive without changing the standard main row", () => {
    const facts = [
      authoredFact(),
      authoredFact({ fact_key: "triage_note", name: "Triage note", value: "KNOWN", detail_value_label: "Stable", presentation_role: "SUPPORTING" }),
    ];
    const rows = (template: PresentationTemplate) => buildFacilityDetailRows({
      nodeKey: "clinic",
      knownFacts: facts,
      knownRelations: [],
      knownActionRequirements: [],
      producerBindings: [],
      facilityResourceRows: [],
      resourceName: (key) => key,
      resolveNodeName: (key) => key,
      presentation: profile(template),
    });

    expect(rows("standard").map((row) => row.key)).toEqual(["clinic:fact:clinical_readiness"]);
    expect(rows("detailed").map((row) => row.key)).toEqual([
      "clinic:fact:clinical_readiness",
      "clinic:fact:triage_note",
    ]);
  });

  it("uses resolved per-entity overrides for placement and depth", () => {
    const facility = {
      key: "clinic",
      name: "Clinic",
      accessible: true,
      node_family: "FACILITY" as const,
      presentation: {
        summary_slot: "BODY" as const,
        detail_level: "SUMMARY" as const,
        default_open: "COLLAPSED" as const,
        knowledge_level: "A" as const,
        semantic_order: ["STATUS" as const],
      },
    };
    const producer: PublicProducerBinding = {
      binding_key: "treat:clinic",
      action_key: "treat",
      action_name: "Treat clinic",
      target_key: "clinic",
      producer_kind: "ACTION_PRODUCED_STATE",
      outputs: [{ semantic_key: "clinic.clinical_readiness", target_key: "clinic", fact_key: "clinical_readiness", desired_value: "READY", status: "UNSATISFIED" }],
      requirements: [{ key: "team", kind: "ROLE", display_name: "Medical team", status: "UNKNOWN" }],
    };
    render(
      <KnownWorldAccordions
        presentation={profile("detailed")}
        resources={[]}
        visibleNodes={[facility]}
        actors={[]}
        knownFacts={[authoredFact()]}
        knownProducerBindings={[producer]}
      />,
    );
    fireEvent.click(screen.getByTestId("knowledge-accordion-locations").querySelector("button")!);
    const card = screen.getByTestId("facility-card-clinic");
    expect(card.querySelector("summary .knowledge-facility-statuses")).not.toBeInTheDocument();
    fireEvent.click(card.querySelector("summary")!);
    expect(card).not.toHaveTextContent("Treat clinic");
    expect(card).not.toHaveTextContent("Medical team");
  });
});
