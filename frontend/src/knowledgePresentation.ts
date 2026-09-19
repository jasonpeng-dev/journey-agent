import type {
  PublicActionRequirement,
  PublicRelation,
  PlayerGameState,
} from "./types";
import {
  legacyFactDisplayLabel,
  legacyFactDisplayValue,
  legacyFacilityStatusDisplayValue,
  isLegacyPresentationFact,
  legacyMeaningfulKnownRelations,
  legacyPublicFactRequirementText,
  legacyRelationDescription,
  legacyRelationRequirementDescription,
  legacyResourceDisplayName,
} from "./legacyPresentationCompatibility";
import { uiLabel } from "./ui";

const MACHINE_KEY = /^[a-z0-9][a-z0-9_.@-]*$/i;

function authoredLabel(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const trimmed = value.trim();
  return trimmed && !MACHINE_KEY.test(trimmed) ? trimmed : fallback;
}

export function resourceDisplayName(key: string, candidate?: string): string {
  const normalizedCandidate = candidate?.trim().toLowerCase().replace(/\s+/g, "_");
  const authoredCandidate = normalizedCandidate === key ? undefined : candidate;
  return authoredLabel(authoredCandidate, authoredLabel(key, legacyResourceDisplayName(key, candidate)));
}

function hasSafeFactMetadata(fact: PlayerGameState["known_facts"][number]): boolean {
  return fact.presentation_role != null
    || fact.summary_value_label != null
    || fact.detail_value_label != null
    || fact.value_label != null;
}

export function resourceAvailabilityRequirementText(
  requirement: Record<string, unknown>,
  targetName?: string | null,
): string | null {
  const subject = targetName?.trim() || "相关对象";
  const factLabel = typeof requirement.fact_label === "string"
    ? requirement.fact_label.trim()
    : "";
  return factLabel
    ? `${subject}满足${factLabel}条件`
    : subject + "满足解锁条件";
}

export type DisplayRequirementLine = {
  key: string;
  label: string;
  value: string;
};

export type DisplayActionRequirements = {
  requirement: PublicActionRequirement;
  title: string;
  lines: DisplayRequirementLine[];
};

/**
 * Relation visibility is decided by the safe projection. The renderer does
 * not know a Scenario's relation-key vocabulary; structural relations may be
 * omitted by the projection or marked with a future safe metadata flag.
 */
export function meaningfulKnownRelations(relations: PublicRelation[]): PublicRelation[] {
  return relations.some((relation) => relation.is_structural !== undefined)
    ? relations.filter((relation) => relation.is_structural !== true)
    : legacyMeaningfulKnownRelations(relations);
}

export function knownRelationDescription(
  relationTypeKey: string,
  candidate?: string | null,
): string {
  return authoredLabel(candidate, legacyRelationDescription(relationTypeKey, candidate));
}

function knownRelationRequirementDescription(
  relationTypeKey: string,
  relations: PublicRelation[],
): string {
  const authoredRelation = relations.find(
    (relation) => relation.relation_type_key === relationTypeKey,
  );
  if (authoredRelation && (authoredRelation.relation_type_name || authoredRelation.is_structural !== undefined)) {
    return authoredRelation.relation_type_name
      ? `需要已知的${authoredRelation.relation_type_name}关系`
      : "需要已知的系统关系";
  }
  return legacyRelationRequirementDescription(relationTypeKey);
}

export function factDisplayLabel(fact: PlayerGameState["known_facts"][number]): string {
  return hasSafeFactMetadata(fact) ? authoredLabel(fact.name, "已知状态") : legacyFactDisplayLabel(fact);
}

export function factDisplayValue(
  fact: PlayerGameState["known_facts"][number],
  value = fact.value,
): string {
  const hasSafeMetadata = hasSafeFactMetadata(fact);
  if ((!hasSafeMetadata || isLegacyPresentationFact(fact))) {
    if (value === fact.value && fact.detail_value_label) return fact.detail_value_label;
    if (value === fact.value && fact.value_label) return fact.value_label;
    return legacyFactDisplayValue(fact, value);
  }
  if (value === fact.value && fact.detail_value_label) return fact.detail_value_label;
  if (value === fact.value && fact.value_label) return fact.value_label;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return uiLabel(value);
  return "当前状态已知";
}

type PublicFactValue = PlayerGameState["known_facts"][number]["value"];

function isPublicFactValue(value: unknown): value is PublicFactValue {
  return typeof value === "boolean" || typeof value === "number" || typeof value === "string";
}

export function publicFactRequirementText(
  requirement: Record<string, unknown>,
  fact: PlayerGameState["known_facts"][number],
  subjectName: string,
): string | null {
  if (fact.value === "UNKNOWN") return null;
  const hasSafeMetadata = hasSafeFactMetadata(fact);
  if (!hasSafeMetadata) {
    const legacy = legacyPublicFactRequirementText(requirement, fact, subjectName);
    if (legacy) return legacy;
  }

  const operator = typeof requirement.operator === "string" ? requirement.operator : "EQ";
  const expected = requirement.value;
  if (operator === "IN" || operator === "NOT_IN") {
    if (!Array.isArray(expected) || expected.length === 0 || !expected.every(isPublicFactValue)) {
      return null;
    }
    const expectedText = expected.map((value) => factDisplayValue(fact, value)).join("、");
    return `${subjectName}：${factDisplayLabel(fact)}${operator === "IN" ? "为" : "不为"}${expectedText}`;
  }

  if (!isPublicFactValue(expected)) return null;
  const operatorText: Record<string, string> = {
    EQ: "为",
    NE: "不为",
    GT: "大于",
    GTE: "至少",
    LT: "小于",
    LTE: "至多",
  };
  const relation = operatorText[operator];
  if (!relation) return null;
  return `${subjectName}：${factDisplayLabel(fact)}${relation}${factDisplayValue(fact, expected)}`;
}

export function facilityStatusDisplayValue(
  fact: PlayerGameState["known_facts"][number],
): string {
  if (fact.summary_value_label) return fact.summary_value_label;
  if (!hasSafeFactMetadata(fact) || isLegacyPresentationFact(fact)) {
    return legacyFacilityStatusDisplayValue(fact);
  }
  return fact.value_label ?? factDisplayValue(fact);
}

export function publicFactIdentity(nodeKey: string, factKey: string): string {
  return `${nodeKey}:${factKey}`;
}

function factLookupKey(nodeKey: string, factKey: string): string {
  return `${nodeKey}:${factKey}`;
}

export function displayActionRequirements(
  requirements: PublicActionRequirement[],
  knownFacts: PlayerGameState["known_facts"],
  relations: PublicRelation[],
  resources: PlayerGameState["resources"] = [],
): DisplayActionRequirements[] {
  const meaningfulRelations = meaningfulKnownRelations(relations);
  const knownFactsByKey = new Map(
    knownFacts.map((fact) => [factLookupKey(fact.node_key, fact.fact_key), fact]),
  );
  const resourceNames = new Map(resources.map((resource) => [resource.key, resource.name]));

  const requirementValue = (
    precondition: PublicActionRequirement["known_preconditions"][number],
    fact: PlayerGameState["known_facts"][number],
  ): string | null => {
    const condition = precondition.failure_condition;
    if (!condition || typeof condition.kind !== "string") return null;
    if (condition.kind === "FACT_NOT_EQUALS" && "value" in condition) {
      return factDisplayValue(fact, condition.value as string | number | boolean);
    }
    if (condition.kind === "FACT_EQUALS" && "value" in condition) {
      const expected = condition.value;
      return typeof expected === "boolean"
        ? factDisplayValue(fact, !expected)
        : "需要满足指定状态";
    }
    if (condition.kind === "FACT_IN" || condition.kind === "FACT_COMPARE") {
      return "需要满足指定状态";
    }
    return null;
  };

  const resourceCostLines = (requirement: PublicActionRequirement): DisplayRequirementLine[] => {
    const extended = requirement as PublicActionRequirement & {
      cost?: Record<string, number>;
      resource_costs?: Record<string, number>;
    };
    const costs = extended.resource_costs ?? extended.cost;
    if (!costs) return [];
    const entries = Object.entries(costs).filter(([, amount]) => typeof amount === "number" && amount > 0);
    return entries.length === 0
      ? []
      : [{
        key: "resource-cost",
        label: "资源需求",
        value: entries.map(([key, amount]) => `${resourceDisplayName(key, resourceNames.get(key))} ×${amount}`).join("、"),
      }];
  };

  return requirements.flatMap((requirement) => {
    const lines: DisplayRequirementLine[] = [];
    if (requirement.required_actor_role_name) {
      lines.push({ key: "actor-role", label: "执行队伍", value: requirement.required_actor_role_name });
    }
    if (
      requirement.source_relation_type_key
      && meaningfulRelations.some((relation) => relation.relation_type_key === requirement.source_relation_type_key)
    ) {
      lines.push({
        key: "relation",
        label: "系统条件",
        value: knownRelationRequirementDescription(
          requirement.source_relation_type_key,
          meaningfulRelations,
        ),
      });
    }
    lines.push(...resourceCostLines(requirement));
    requirement.known_preconditions.forEach((precondition) => {
      const fact = knownFactsByKey.get(factLookupKey(precondition.node_key, precondition.fact_key));
      if (!fact) return;
      const value = requirementValue(precondition, fact);
      if (!value) return;
      lines.push({
        key: `fact:${precondition.node_key}:${precondition.fact_key}`,
        label: factDisplayLabel(fact),
        value,
      });
    });
    return lines.length > 0 ? [{ requirement, title: requirement.action_name, lines }] : [];
  });
}

export function relationDisplayKey(relation: PublicRelation): string {
  return relation.relation_key ?? `${relation.source_node_key}:${relation.relation_type_key}:${relation.target_node_key}`;
}
