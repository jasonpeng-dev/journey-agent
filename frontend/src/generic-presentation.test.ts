import { describe, expect, it } from "vitest";

import {
  factDisplayLabel,
  factDisplayValue,
  knownRelationDescription,
  resourceDisplayName,
} from "./knowledgePresentation";
import { resolvePresentationProfilePreview } from "./presentationPolicy";

const SYNTHETIC_DOMAINS = [
  {
    name: "Medical",
    factKeys: ["patient_condition", "treatment_ready", "blood_supply"],
    resourceKey: "clinical_supplies",
    relationKey: "treats_patient",
  },
  {
    name: "Space",
    factKeys: ["oxygen_level", "hull_integrity", "maintenance_crew"],
    resourceKey: "orbital_spares",
    relationKey: "services_module",
  },
  {
    name: "Investigation",
    factKeys: ["clue_status", "suspect_relation", "evidence_ready"],
    resourceKey: "case_files",
    relationKey: "links_clue",
  },
] as const;

describe("generic authored presentation metadata", () => {
  it.each(SYNTHETIC_DOMAINS)("renders $name without an ontology key", (domain) => {
    const fact = {
      node_key: `${domain.name.toLowerCase()}_subject`,
      fact_key: domain.factKeys[0],
      name: `${domain.name} authored fact`,
      value: "KNOWN",
      value_label: "Known",
      node_family: "GENERIC" as const,
      presentation_slot: "SEMANTIC" as const,
    };

    expect(factDisplayLabel(fact)).toBe(`${domain.name} authored fact`);
    expect(factDisplayValue(fact)).toBe("Known");
    expect(resourceDisplayName(domain.resourceKey, `${domain.name} supplies`)).toBe(
      `${domain.name} supplies`,
    );
    expect(knownRelationDescription(domain.relationKey, `${domain.name} relation`)).toBe(
      `${domain.name} relation`,
    );

    const preview = resolvePresentationProfilePreview({
      schema_version: 1,
      template: "standard",
      family_overrides: [{ node_family: "GENERIC", entity_detail: "DETAIL" }],
      semantic_overrides: [{ semantic_key: domain.factKeys[0], summary_slot: "BODY" }],
    });
    expect(preview.entity_detail).toBe("DETAIL");
    expect(preview.summary_slot).toBe("HEADER");
  });
});
