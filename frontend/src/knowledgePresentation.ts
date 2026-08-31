import type {
  PublicActionRequirement,
  PublicRelation,
  PlayerGameState,
} from "./types";
import { uiLabel } from "./ui";

const RESOURCE_LABELS: Record<string, string> = {
  communication_equipment: "通信设备",
  electrical_repair_parts: "电力维修部件",
  general_engineering_parts: "通用工程部件",
  municipal_repair_materials: "市政维修材料",
  water_system_parts: "水务系统部件",
};

export function resourceDisplayName(key: string, candidate?: string): string {
  const mapped = RESOURCE_LABELS[key];
  if (mapped) return mapped;
  if (candidate && candidate !== key && !/^[a-z0-9_]+$/.test(candidate)) return candidate;
  return candidate ?? "已知资源";
}

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

export function resourceAvailabilityRequirementText(
  requirement: Record<string, unknown>,
  targetName?: string | null,
): string | null {
  const subject = targetName?.trim() || '相关设施';
  const factKey = requirement.fact_key;
  const value = requirement.value;
  if (factKey === 'operational') {
    if (value === true) return subject + '恢复运行';
    if (value === false) return subject + '停止运行';
  }
  if (factKey === 'power_supply') {
    if (value === 'AVAILABLE' || value === true) return subject + '恢复供电';
    if (value === 'UNAVAILABLE' || value === false) return subject + '满足供电条件';
  }
  return subject + '满足解锁条件';
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

export function meaningfulKnownRelations(relations: PublicRelation[]): PublicRelation[] {
  return relations.filter((relation) => !STRUCTURAL_RELATION_TYPES.has(relation.relation_type_key));
}

export function knownRelationDescription(relationTypeKey: string): string {
  return RELATION_DESCRIPTIONS[relationTypeKey] ?? "当前已掌握的系统关系";
}

function knownRelationRequirementDescription(relationTypeKey: string): string {
  return RELATION_REQUIREMENT_DESCRIPTIONS[relationTypeKey] ?? "需要已知的系统关系";
}

const FACT_LABELS: Record<string, string> = {
  operational: "运行状态",
  power_supply: "供电状态",
  power_generation_capable: "发电能力",
  generation_capable: "发电能力",
  emergency_power: "应急供电",
  passable: "通行状态",
  heavy_engineering_support: "重型工程支援",
  heavy_engineering_support_ready: "重型工程支援状态",
  repair_profile: "设施类型",
};

const MACHINE_VALUE_LABELS: Record<string, string> = {
  central_hospital: "医院设施",
  central_communication_core: "通信核心",
  district_service_center: "公用事业保障设施",
  east_distribution_station: "配电设施",
  water_treatment_plant: "水处理设施",
  south_pump_station: "南部泵站",
  east_water_pump_station: "东部供水泵站",
};

export function factDisplayLabel(fact: PlayerGameState["known_facts"][number]): string {
  if (FACT_LABELS[fact.fact_key]) return FACT_LABELS[fact.fact_key];
  if (fact.name && fact.name !== fact.fact_key && !/^[a-z0-9_]+$/.test(fact.name)) return fact.name;
  return "已知状态";
}

export function factDisplayValue(
  fact: PlayerGameState["known_facts"][number],
  value = fact.value,
): string {
  if (typeof value === "boolean") {
    if (fact.fact_key === "operational") return value ? "运行中" : "未运行";
    if (fact.fact_key === "power_supply") return value ? "已供电" : "未供电";
    if (fact.fact_key === "emergency_power") return value ? "已恢复" : "未恢复";
    if (fact.fact_key === "passable") return value ? "可通行" : "待修复";
    if (fact.fact_key === "heavy_engineering_support_ready") return value ? "已部署" : "未部署";
    if (fact.fact_key === "heavy_engineering_support") return value ? "可用" : "不可用";
    if (fact.fact_key === "generation_capable" || fact.fact_key === "power_generation_capable") {
      return value ? "具备" : "不具备";
    }
    return value ? "是" : "否";
  }
  if (typeof value === "number") return String(value);
  if (fact.fact_key === "power_supply") {
    if (value === "AVAILABLE") return "已供电";
    if (value === "UNAVAILABLE") return "未供电";
  }
  if (fact.fact_key === "heavy_engineering_support") {
    if (value === "AVAILABLE") return "可用";
    if (value === "UNAVAILABLE") return "不可用";
  }
  if (fact.fact_key === "repair_profile") {
    return MACHINE_VALUE_LABELS[value] ?? "设施状态已知";
  }
  if (value === "AVAILABLE") return "可用";
  if (value === "UNAVAILABLE") return "不可用";
  if (MACHINE_VALUE_LABELS[value]) return MACHINE_VALUE_LABELS[value];
  return /^[a-z0-9_]+$/i.test(value) ? "当前状态已知" : uiLabel(value);
}

export function facilityStatusDisplayValue(
  fact: PlayerGameState["known_facts"][number],
): string {
  if (fact.fact_key === "operational" && typeof fact.value === "boolean") {
    return fact.value ? "设备正常" : "待修复";
  }
  return factDisplayValue(fact);
}

export function generationCapabilityDisplayValue(
  fact: PlayerGameState["known_facts"][number],
): string {
  if (fact.value === true || fact.value === "AVAILABLE") return "已具备";
  if (fact.value === false || fact.value === "UNAVAILABLE") return "未具备";
  return factDisplayValue(fact);
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
      if (typeof expected === "boolean") return factDisplayValue(fact, !expected);
      return "需要满足指定状态";
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
    if (!entries.length) return [];
    return [{
      key: "resource-cost",
      label: "资源需求",
      value: entries
        .map(([key, amount]) => `${resourceNames.get(key) ?? "所需资源"} ×${amount}`)
        .join("、"),
    }];
  };

  const titleFor = (actionName: string, targetName: string | undefined): string => {
    if (!targetName) return actionName;
    if (actionName.includes(targetName)) return actionName;
    if (actionName.startsWith("修复")) return `维修${targetName}`;
    return `${actionName} · ${targetName}`;
  };

  return requirements.flatMap((requirement) => {
    const targetCandidates = requirement.known_preconditions.flatMap((item) => {
      if (item.fact_key !== "repair_profile") return [];
      const fact = knownFactsByKey.get(factLookupKey(item.node_key, item.fact_key));
      const condition = item.failure_condition;
      const matches = condition?.kind === "FACT_IN"
        && Array.isArray(condition.values)
        ? condition.values.includes(fact?.value)
        : condition?.kind === "FACT_EQUALS"
          && "value" in condition
          ? condition.value === fact?.value
          : false;
      if (!fact || !matches || !fact.node_name) return [];
      return [{ key: fact.node_key, name: fact.node_name }];
    });
    const targets = [...new Map(targetCandidates.map((target) => [target.key, target])).values()];
    const targetGroups = targets.length ? targets : [{ key: undefined, name: undefined }];
    return targetGroups.flatMap((target) => {
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
          label: relationTypeKey === "supplies_power_to" ? "前置条件" : "系统条件",
          value: knownRelationRequirementDescription(relationTypeKey),
        });
      }

      lines.push(...resourceCostLines(requirement));
      requirement.known_preconditions.forEach((precondition) => {
        if (precondition.fact_key === "repair_profile") return;
        if (target.key !== undefined && precondition.node_key !== target.key) return;
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

      return lines.length
        ? [{ requirement, title: titleFor(requirement.action_name, target.name), lines }]
        : [];
    });
  });
}

export function relationDisplayKey(relation: PublicRelation): string {
  return relation.relation_key ?? `${relation.source_node_key}:${relation.relation_type_key}:${relation.target_node_key}`;
}
