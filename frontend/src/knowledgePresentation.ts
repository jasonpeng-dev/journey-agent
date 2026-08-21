import type { PublicActionRequirement, PublicRelation, PlayerGameState } from "./types";
import { uiLabel } from "./ui";

const STRUCTURAL_RELATION_TYPES = new Set(["located_in", "endpoint"]);

const RELATION_DESCRIPTIONS: Record<string, string> = {
  supplies_power_to: "可向其供电",
  contains: "包含目标",
  supports: "提供系统支援",
  reveals: "可发现目标",
  unlocks: "可解锁目标",
  enables: "支持目标行动",
};

const RELATION_REQUIREMENT_DESCRIPTIONS: Record<string, string> = {
  supplies_power_to: "需要已知的直接供电关系",
  supports: "需要已知的系统支援关系",
  reveals: "需要已知的信息发现关系",
  unlocks: "需要已知的解锁关系",
  enables: "需要已知的行动支持关系",
};

export type DisplayRequirementLine = {
  key: string;
  label: string;
  value: string;
};

export type DisplayActionRequirements = {
  requirement: PublicActionRequirement;
  lines: DisplayRequirementLine[];
};

export function meaningfulKnownRelations(relations: PublicRelation[]): PublicRelation[] {
  return relations.filter((relation) => !STRUCTURAL_RELATION_TYPES.has(relation.relation_type_key));
}

export function knownRelationDescription(relationTypeKey: string): string {
  return RELATION_DESCRIPTIONS[relationTypeKey] ?? "当前已掌握的系统关系";
}

function knownRelationRequirementDescription(relationTypeKey: string): string {
  return RELATION_REQUIREMENT_DESCRIPTIONS[relationTypeKey] ?? "需要已知的系统关系";
}

function displayValue(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") return uiLabel(value);
  return String(value);
}

function factLookupKey(nodeKey: string, factKey: string): string {
  return `${nodeKey}:${factKey}`;
}

export function displayActionRequirements(
  requirements: PublicActionRequirement[],
  knownFacts: PlayerGameState["known_facts"],
  relations: PublicRelation[],
): DisplayActionRequirements[] {
  const meaningfulRelations = meaningfulKnownRelations(relations);
  const knownFactsByKey = new Map(
    knownFacts.map((fact) => [factLookupKey(fact.node_key, fact.fact_key), fact]),
  );

  return requirements
    .map((requirement) => {
      const lines: DisplayRequirementLine[] = [];
      if (requirement.required_actor_role_name) {
        lines.push({
          key: "actor-role",
          label: "执行队伍",
          value: requirement.required_actor_role_name,
        });
      }

      if (
        requirement.source_relation_type_key &&
        meaningfulRelations.some(
          (relation) => relation.relation_type_key === requirement.source_relation_type_key,
        )
      ) {
        const relationTypeKey = requirement.source_relation_type_key;
        lines.push({
          key: "relation",
          label: relationTypeKey === "supplies_power_to" ? "供电条件" : "系统条件",
          value: knownRelationRequirementDescription(relationTypeKey),
        });
      }

      requirement.known_preconditions.forEach((precondition) => {
        const fact = knownFactsByKey.get(factLookupKey(precondition.node_key, precondition.fact_key));
        if (!fact) return;
        lines.push({
          key: `fact:${precondition.node_key}:${precondition.fact_key}`,
          label: fact.node_name ? `${fact.node_name} · ${fact.name}` : fact.name,
          value: displayValue(precondition.current_value),
        });
      });

      return { requirement, lines };
    })
    .filter((item) => item.lines.length > 0);
}

export function relationDisplayKey(relation: PublicRelation): string {
  return relation.relation_key ?? `${relation.source_node_key}:${relation.relation_type_key}:${relation.target_node_key}`;
}
