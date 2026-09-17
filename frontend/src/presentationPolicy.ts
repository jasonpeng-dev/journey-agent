import type {
  PresentationActorField,
  PresentationDefaultOpen,
  PresentationDensity,
  PresentationEntityDetail,
  PresentationKnowledgeLevel,
  PresentationPlanDetail,
  PresentationProfileDocument,
  PresentationResourceSlot,
  PresentationRelationSlot,
  PresentationSemanticSlot,
  PresentationSummarySlot,
  PresentationTemplate,
  PresentationTimelineDensity,
  PublicPresentation,
} from "./types";

type PresentationDefaults = Omit<PublicPresentation, "revision" | "template">;

const SAFE_ACTOR_FIELDS: PresentationActorField[] = [
  "NAME",
  "ROLE",
  "LOCATION",
  "STATUS",
  "TASK",
  "COMMAND_REACHABILITY",
];

const TEMPLATE_DEFAULTS: Record<PresentationTemplate, PresentationDefaults> = {
  compact: {
    density: "COMPACT",
    default_open: "COLLAPSED",
    summary_slot: "HEADER",
    entity_detail: "SUMMARY",
    knowledge_level: "A",
    semantic_order: ["NAME", "STATUS"],
    resource_order: ["NAME", "AMOUNT"],
    relation_order: ["TYPE", "TARGET"],
    actor_fields: ["NAME", "ROLE", "STATUS"],
    roadmap_detail: "SUMMARY",
    plan_default: "COLLAPSED",
    timeline_density: "COMPACT",
  },
  standard: {
    density: "STANDARD",
    default_open: "COMPACT",
    summary_slot: "HEADER",
    entity_detail: "DETAIL",
    knowledge_level: "A+B",
    semantic_order: ["NAME", "STATUS", "FACTS", "RESOURCES", "RELATIONS"],
    resource_order: ["NAME", "AMOUNT", "STATUS", "UNIT"],
    relation_order: ["TYPE", "TARGET", "VISIBILITY"],
    actor_fields: ["NAME", "ROLE", "LOCATION", "STATUS", "COMMAND_REACHABILITY"],
    roadmap_detail: "DETAIL",
    plan_default: "COMPACT",
    timeline_density: "STANDARD",
  },
  detailed: {
    density: "DETAILED",
    default_open: "FULL",
    summary_slot: "BOTH",
    entity_detail: "CAUSALITY",
    knowledge_level: "A+B+C",
    semantic_order: [
      "NAME",
      "NODE_TYPE",
      "VISIBILITY",
      "ACCESS",
      "STATUS",
      "FACTS",
      "RESOURCES",
      "RELATIONS",
    ],
    resource_order: ["NAME", "AMOUNT", "STATUS", "UNIT"],
    relation_order: ["TYPE", "TARGET", "VISIBILITY"],
    actor_fields: ["NAME", "ROLE", "LOCATION", "STATUS", "TASK", "COMMAND_REACHABILITY"],
    roadmap_detail: "CAUSALITY",
    plan_default: "FULL",
    timeline_density: "DETAILED",
  },
};

export const PRESENTATION_OPTIONS = {
  templates: ["compact", "standard", "detailed"] as PresentationTemplate[],
  densities: ["COMPACT", "STANDARD", "DETAILED"] as PresentationDensity[],
  defaultOpen: ["COLLAPSED", "COMPACT", "FULL"] as PresentationDefaultOpen[],
  summarySlots: ["HEADER", "BODY", "BOTH"] as PresentationSummarySlot[],
  entityDetails: ["SUMMARY", "DETAIL", "CAUSALITY"] as PresentationEntityDetail[],
  knowledgeLevels: ["A", "A+B", "A+B+C"] as PresentationKnowledgeLevel[],
  planDetails: ["COLLAPSED", "COMPACT", "FULL"] as PresentationPlanDetail[],
  timelineDensities: ["COMPACT", "STANDARD", "DETAILED"] as PresentationTimelineDensity[],
  semanticSlots: ["NAME", "NODE_TYPE", "VISIBILITY", "ACCESS", "STATUS", "FACTS", "RESOURCES", "RELATIONS"] as PresentationSemanticSlot[],
  resourceSlots: ["NAME", "AMOUNT", "STATUS", "UNIT"] as PresentationResourceSlot[],
  relationSlots: ["TYPE", "TARGET", "VISIBILITY"] as PresentationRelationSlot[],
  actorFields: SAFE_ACTOR_FIELDS,
};

export const PRESENTATION_PROFILE_REFRESH_INTERVAL_MS = 15_000;

export function defaultPresentationProfile(): PresentationProfileDocument {
  return {
    schema_version: 1,
    template: "standard",
    family_overrides: [],
    semantic_overrides: [],
  };
}

export function clonePresentationProfile(
  profile: PresentationProfileDocument,
): PresentationProfileDocument {
  return JSON.parse(JSON.stringify(profile)) as PresentationProfileDocument;
}

function nonEmpty<T>(value: T[] | undefined, fallback: T[]): T[] {
  return value && value.length > 0 ? [...value] : [...fallback];
}

function validTemplate(value: unknown): PresentationTemplate {
  return value === "compact" || value === "detailed" ? value : "standard";
}

function safeProfile(document: PresentationProfileDocument | null | undefined): PresentationProfileDocument {
  if (!document || document.schema_version !== 1) return defaultPresentationProfile();
  return {
    ...defaultPresentationProfile(),
    ...document,
    template: validTemplate(document.template),
  };
}

/**
 * Resolve the same bounded public policy used by the server for local UI
 * preview. This function only rearranges controls already present in the
 * profile; it never derives or exposes Scenario/runtime data.
 */
export function resolvePresentationProfilePreview(
  document: PresentationProfileDocument | null | undefined,
  revision = 1,
): PublicPresentation {
  const profile = safeProfile(document);
  const template = validTemplate(profile.template);
  const defaults = TEMPLATE_DEFAULTS[template];
  const global = profile.global_display;
  const world = profile.world_entities;
  const actor = profile.actor_team;
  const goal = profile.goal_execution;
  const actorOrder: PresentationActorField[] = actor?.field_order?.length
    ? actor.field_order
    : actor?.visible_fields?.length
      ? actor.visible_fields
      : defaults.actor_fields as PresentationActorField[];
  let actorFields = actorOrder.filter((field) => SAFE_ACTOR_FIELDS.includes(field));
  if (actor?.visible_fields?.length) {
    actorFields = actorFields.filter((field) => actor.visible_fields!.includes(field));
  }
  return {
    revision,
    template,
    density: global?.density ?? defaults.density,
    default_open: global?.default_open ?? defaults.default_open,
    summary_slot: global?.summary_slot ?? defaults.summary_slot,
    entity_detail: world?.entity_detail ?? defaults.entity_detail,
    knowledge_level: world?.knowledge_level ?? defaults.knowledge_level,
    semantic_order: nonEmpty(global?.semantic_order, defaults.semantic_order),
    resource_order: nonEmpty(world?.resource_order, defaults.resource_order),
    relation_order: nonEmpty(world?.relation_order, defaults.relation_order),
    actor_fields: actorFields,
    roadmap_detail: goal?.roadmap_detail ?? defaults.roadmap_detail,
    plan_default: goal?.plan_default ?? defaults.plan_default,
    timeline_density: goal?.timeline_density ?? defaults.timeline_density,
  };
}

export function presentationDefaults(template: PresentationTemplate): PresentationDefaults {
  return { ...TEMPLATE_DEFAULTS[template] };
}
