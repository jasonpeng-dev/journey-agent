/*
 * Legacy hand-built DTO compatibility only.
 *
 * The live Player contract always carries authored semantic metadata. These
 * adapters exist for old snapshots and narrow test fixtures that predate the
 * typed presentation fields; generic renderers must not add new callers.
 */
import type { PublicRelation, PlayerGameState } from "./types";
import { uiLabel } from "./ui";

const LEGACY_RESOURCE_LABELS: Record<string, string> = {
  communication_equipment: "通信维修部件",
  electrical_repair_parts: "电力维修部件",
  general_engineering_parts: "通用维修部件",
  municipal_repair_materials: "市政维修材料",
  water_system_parts: "水务维修部件",
};
const LEGACY_RELATION_LABELS: Record<string, string> = {
  supplies_power_to: "可向其供电",
  contains: "包含目标",
  supports: "提供系统支援",
  reveals: "可发现目标",
  unlocks: "可解锁目标",
  enables: "支持目标行动",
};
const LEGACY_FACILITY_RELATION_LABELS: Record<string, string> = {
  supplies_power_to: "可供电",
};
const LEGACY_RELATION_REQUIREMENTS: Record<string, string> = {
  supplies_power_to: "需要已知的直接供电关系",
  supports: "需要已知的系统支援关系",
  reveals: "需要已知的信息发现关系",
  unlocks: "需要已知的解锁关系",
  enables: "需要已知的行动支持关系",
};
const LEGACY_FACT_LABELS: Record<string, string> = {
  operational: "运行状态",
  power_supply: "供电状态",
  emergency_power: "应急供电",
  passable: "通行状态",
  heavy_engineering_support: "重型工程支援",
  heavy_engineering_support_ready: "重型工程支援状态",
  repair_profile: "设施类型",
};
const LEGACY_MACHINE_VALUE_LABELS: Record<string, string> = {
  central_hospital: "医院设施",
  central_communication_core: "通信核心",
  district_service_center: "公用事业保障设施",
  east_distribution_station: "配电设施",
  water_treatment_plant: "水处理设施",
  south_pump_station: "南部泵站",
  east_water_pump_station: "东部供水泵站",
};
const LEGACY_STRUCTURAL_RELATIONS = new Set(["located_in", "endpoint"]);

type PublicFact = PlayerGameState["known_facts"][number];

export function legacyResourceDisplayName(key: string, candidate?: string): string {
  return LEGACY_RESOURCE_LABELS[key] ?? candidate ?? "已知资源";
}

export function legacyRelationDescription(key: string, candidate?: string | null): string {
  return candidate && candidate !== key
    ? candidate
    : LEGACY_RELATION_LABELS[key] ?? uiLabel(key);
}

export function legacyFacilityRelationDescription(key: string, candidate?: string | null): string {
  return candidate && candidate !== key
    ? candidate
    : LEGACY_FACILITY_RELATION_LABELS[key] ?? legacyRelationDescription(key, candidate);
}

export function legacyRelationRequirementDescription(key: string): string {
  return LEGACY_RELATION_REQUIREMENTS[key] ?? "需要已知的系统关系";
}

export function legacyMeaningfulKnownRelations(relations: PublicRelation[]): PublicRelation[] {
  return relations.filter((relation) => !LEGACY_STRUCTURAL_RELATIONS.has(relation.relation_type_key));
}

export function legacyFactDisplayLabel(fact: PublicFact): string {
  return LEGACY_FACT_LABELS[fact.fact_key]
    ?? (fact.name && fact.name !== fact.fact_key && !/^[a-z0-9_]+$/i.test(fact.name) ? fact.name : "已知状态");
}

export function legacyFactDisplayValue(fact: PublicFact, value = fact.value): string {
  if (typeof value === "boolean") {
    if (fact.fact_key === "operational") return value ? "运行中" : "未运行";
    if (fact.fact_key === "power_supply") return value ? "已供电" : "未供电";
    if (fact.fact_key === "emergency_power") return value ? "已恢复" : "未恢复";
    if (fact.fact_key === "passable") return value ? "可通行" : "待修复";
    if (fact.fact_key === "heavy_engineering_support_ready") return value ? "已部署" : "未部署";
    if (fact.fact_key === "heavy_engineering_support") return value ? "可用" : "不可用";
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
  if (fact.fact_key === "repair_profile") return LEGACY_MACHINE_VALUE_LABELS[value] ?? "设施状态已知";
  if (value === "AVAILABLE") return "可用";
  if (value === "UNAVAILABLE") return "不可用";
  return LEGACY_MACHINE_VALUE_LABELS[value] ?? (/^[a-z0-9_]+$/i.test(value) ? "当前状态已知" : uiLabel(value));
}

export function legacyFacilityStatusDisplayValue(fact: PublicFact): string {
  return fact.fact_key === "operational" && typeof fact.value === "boolean"
    ? fact.value ? "设备正常" : "待修复"
    : legacyFactDisplayValue(fact);
}

export function legacyPublicFactRequirementText(
  requirement: Record<string, unknown>,
  fact: PublicFact,
  subjectName: string,
): string | null {
  const expected = requirement.value;
  if (requirement.operator === "EQ" && (
    (fact.fact_key === "operational" && typeof expected === "boolean")
    || (fact.fact_key === "power_supply" && (
      expected === true || expected === false || expected === "AVAILABLE" || expected === "UNAVAILABLE"
    ))
    || (fact.fact_key === "passable" && typeof expected === "boolean")
  )) {
    const factLabel = typeof requirement.fact_label === "string" ? requirement.fact_label : legacyFactDisplayLabel(fact);
    return `${subjectName}满足${factLabel}条件`;
  }
  return null;
}

export function legacyFactStateDisplayText(
  factName: string,
  currentValue: string | number | boolean | undefined,
  acceptedValues: Array<string | number | boolean>,
): string | null {
  const stateName = factName === "目标状态" ? "状态" : factName;
  if (currentValue === true) {
    if (factName.includes("运行")) return "正在运行";
    if (factName.includes("供电")) return "已供电";
    if (factName.includes("通行")) return "可通行";
    if (factName.includes("发电")) return "正在发电";
    return `${stateName}已达到目标状态`;
  }
  if (currentValue === false) {
    if (factName.includes("运行")) return "尚未恢复运行";
    if (factName.includes("供电")) return "尚未供电";
    if (factName.includes("通行")) return "尚未恢复通行";
    if (factName.includes("发电")) return "尚未发电";
    return `${stateName}尚未达到目标状态`;
  }
  if (currentValue === "AVAILABLE") return `${stateName}可用`;
  if (currentValue === "UNAVAILABLE") return `${stateName}不可用`;
  if (acceptedValues.some((value) => value === currentValue)) return `${stateName}已达到目标状态`;
  return `${stateName}待确认`;
}

export function legacyFactPresentationPriority(factKey: string): { group: "HEADER_PRIMARY" | "HEADER_SECONDARY" | "SEMANTIC"; priority: number } {
  if (factKey === "operational") return { group: "HEADER_PRIMARY", priority: 0 };
  if (factKey === "power_supply") return { group: "HEADER_SECONDARY", priority: 0 };
  return { group: "SEMANTIC", priority: 0 };
}
