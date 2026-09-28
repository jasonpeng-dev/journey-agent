import {
  factDisplayLabel,
  factDisplayValue,
  knownRelationDescription,
  publicFactRequirementText,
} from "./knowledgePresentation";
import {
  legacyFactPresentationPriority,
  legacyFactPresentationRole,
  legacyFacilityRelationDescription,
} from "./legacyPresentationCompatibility";
import type {
  PlayerGameState,
  PublicActionResourceRequirement,
  PublicProducerBinding,
  PublicProducerRequirement,
  PublicRelation,
  PublicTargetActionContract,
  PublicPresentation,
  ResourceIntelligence,
} from "./types";

export type PublicFact = PlayerGameState["known_facts"][number];
type PublicNode = PlayerGameState["visible_nodes"][number];

export type FacilityResourcePresentationRow = {
  key: string;
  resourceKey: string;
  resourceName: string;
  quantity: number;
  availability: "AVAILABLE" | "UNAVAILABLE";
  availabilityRequirement: Record<string, unknown> | null;
  availabilityRequirementStatus: "KNOWN" | "UNKNOWN" | null;
  unlockText?: string;
  displayUnit?: string | null;
};

export type FacilityDetailRow = {
  key: string;
  label: string;
  value: string;
  kind: "DETAIL" | "RECOVERY" | "UNLOCK";
};

export type FacilityPresentationGroup =
  | "HEADER_PRIMARY"
  | "HEADER_SECONDARY"
  | "SEMANTIC"
  | "RESOURCE"
  | "RELATION"
  | "OTHER";

export type FacilityPresentationOrderPolicy = {
  groupOrder?: readonly FacilityPresentationGroup[];
  primaryFactKeys?: readonly string[];
  secondaryFactKeys?: readonly string[];
  factPriority?: (fact: PublicFact) => number;
};

export const DEFAULT_FACILITY_PRESENTATION_ORDER_POLICY: FacilityPresentationOrderPolicy = {
  groupOrder: [
    "HEADER_PRIMARY",
    "HEADER_SECONDARY",
    "SEMANTIC",
    "RESOURCE",
    "RELATION",
    "OTHER",
  ],
};

type PresentationOrderDescriptor = {
  group: FacilityPresentationGroup;
  priority?: number;
  stableKey: string;
};

type FacilityPresentationBlock = {
  rows: FacilityDetailRow[];
  descriptor: PresentationOrderDescriptor;
  producerBindings?: ProducerBinding[];
};

function compareStableText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

export function resolvePresentationOrder<T>(
  items: readonly T[],
  describe: (item: T, index: number) => PresentationOrderDescriptor,
  policy: FacilityPresentationOrderPolicy = DEFAULT_FACILITY_PRESENTATION_ORDER_POLICY,
): T[] {
  const groupOrder = policy.groupOrder ?? DEFAULT_FACILITY_PRESENTATION_ORDER_POLICY.groupOrder!;
  const groupPriority = new Map(groupOrder.map((group, index) => [group, index]));
  return items
    .map((item, index) => ({ item, index, descriptor: describe(item, index) }))
    .sort((left, right) => {
      const leftGroup = groupPriority.get(left.descriptor.group) ?? groupOrder.length;
      const rightGroup = groupPriority.get(right.descriptor.group) ?? groupOrder.length;
      return leftGroup - rightGroup
        || (left.descriptor.priority ?? 0) - (right.descriptor.priority ?? 0)
        || compareStableText(left.descriptor.stableKey, right.descriptor.stableKey)
        || left.index - right.index;
    })
    .map(({ item }) => item);
}

function factPresentationOrderDescriptor(
  fact: PublicFact,
  policy: FacilityPresentationOrderPolicy,
): PresentationOrderDescriptor {
  const role = resolveFacilityFactRole(fact);
  if (role === "HEADER_PRIMARY") {
    return { group: "HEADER_PRIMARY", priority: 0, stableKey: fact.fact_key };
  }
  if (role === "HEADER_SECONDARY") {
    return { group: "HEADER_SECONDARY", priority: 0, stableKey: fact.fact_key };
  }
  if (!fact.presentation_slot && policy === DEFAULT_FACILITY_PRESENTATION_ORDER_POLICY) {
    const legacy = legacyFactPresentationPriority(fact.fact_key);
    return { ...legacy, stableKey: fact.fact_key };
  }
  const primaryIndex = (policy.primaryFactKeys ?? []).indexOf(fact.fact_key);
  if (primaryIndex >= 0) {
    return {
      group: "HEADER_PRIMARY",
      priority: primaryIndex,
      stableKey: fact.fact_key,
    };
  }
  const secondaryIndex = (policy.secondaryFactKeys ?? []).indexOf(fact.fact_key);
  if (secondaryIndex >= 0) {
    return {
      group: "HEADER_SECONDARY",
      priority: secondaryIndex,
      stableKey: fact.fact_key,
    };
  }
  return {
    group: "SEMANTIC",
    priority: policy.factPriority?.(fact) ?? 0,
    stableKey: fact.fact_key,
  };
}

export function resolveFacilityFactRole(fact: PublicFact): NonNullable<PublicFact["presentation_role"]> {
  return fact.presentation_role ?? legacyFactPresentationRole(fact);
}

export type TargetActionRequirementRow = FacilityDetailRow & {
  actionKey: string;
};

type ProducerBinding = PublicProducerBinding;
type ProducerFact = PlayerGameState["known_facts"][number];

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? String(value);
}

export function publicResourceRequirementParts(
  requirements: PublicActionResourceRequirement[] | undefined,
  resourceName: (key: string) => string,
): string[] {
  return (requirements ?? []).flatMap((requirement) => (
    typeof requirement.resource_key === "string" && typeof requirement.minimum === "number"
      ? [`${resourceName(requirement.resource_key)} ×${String(requirement.minimum)}`]
      : []
  ));
}

export function publicResourceRequirementIdentity(
  actionKey: string,
  requirement: PublicActionResourceRequirement,
  targetKey?: string,
): string {
  const scope = requirement.scope ?? {};
  const scopedTargetKey = typeof scope.target_key === "string" ? scope.target_key : undefined;
  return canonicalJson({
    action_key: actionKey,
    target_key: scopedTargetKey ?? targetKey ?? null,
    resource_key: requirement.resource_key,
    scope,
    minimum: requirement.minimum,
  });
}

export function uniquePublicResourceRequirements(
  actionKey: string,
  requirements: PublicActionResourceRequirement[] | undefined,
  targetKey?: string,
): PublicActionResourceRequirement[] {
  const identities = new Set<string>();
  return (requirements ?? []).filter((requirement) => {
    if (typeof requirement.resource_key !== "string" || typeof requirement.minimum !== "number") {
      return false;
    }
    const identity = publicResourceRequirementIdentity(actionKey, requirement, targetKey);
    if (identities.has(identity)) return false;
    identities.add(identity);
    return true;
  });
}

export function targetKeyForPublicActionRequirement(
  action: NonNullable<PlayerGameState["known_action_requirements"]>[number],
  visibleNodes: PlayerGameState["visible_nodes"],
): string | null {
  const resourceRequirements = action.resource_requirements ?? [];
  if (resourceRequirements.length === 0) return null;

  const candidateKeys = new Set<string>();
  resourceRequirements.forEach((requirement) => {
    const scope = requirement.scope;
    if (scope && typeof scope.target_key === "string") candidateKeys.add(scope.target_key);
  });
  action.known_preconditions.forEach((precondition) => {
    if (precondition.selector === "EXPLICIT" && typeof precondition.node_key === "string") {
      candidateKeys.add(precondition.node_key);
    }
  });
  if (candidateKeys.size !== 1) return null;

  const [targetKey] = candidateKeys;
  const target = visibleNodes.find((node) => node.key === targetKey);
  const targetFamily = target?.node_family ?? target?.node_type_key?.toUpperCase();
  if (!target || !["FACILITY", "TRANSPORT"].includes(targetFamily ?? "")) return null;

  const hasIncompatibleExplicitScope = resourceRequirements.some((requirement) => {
    const scope = requirement.scope;
    if (!scope || scope.kind !== "EXPLICIT" || typeof scope.node_key !== "string") return false;
    return scope.node_key !== targetKey && scope.node_key !== target.region_key;
  });
  return hasIncompatibleExplicitScope ? null : targetKey;
}

function uniqueRecordValues(values: Array<Record<string, unknown>> | undefined): Array<Record<string, unknown>> {
  const identities = new Set<string>();
  return (values ?? []).filter((value) => {
    const identity = canonicalJson(value);
    if (identities.has(identity)) return false;
    identities.add(identity);
    return true;
  });
}

export function mergeTargetActionContracts(
  contracts: PublicTargetActionContract[],
): PublicTargetActionContract[] {
  const merged = new Map<string, PublicTargetActionContract>();
  contracts.forEach((contract) => {
    const identity = canonicalJson({ action_key: contract.action_key, target_key: contract.target_key });
    const current = merged.get(identity);
    if (!current) {
      merged.set(identity, {
        ...contract,
        resource_requirements: uniquePublicResourceRequirements(
          contract.action_key,
          contract.resource_requirements,
          contract.target_key,
        ),
        special_requirements: uniqueRecordValues(contract.special_requirements),
        effects: uniqueRecordValues(contract.effects),
      });
      return;
    }
    merged.set(identity, {
      ...current,
      cost: { ...(current.cost ?? {}), ...(contract.cost ?? {}) },
      resource_requirements: uniquePublicResourceRequirements(
        current.action_key,
        [...(current.resource_requirements ?? []), ...(contract.resource_requirements ?? [])],
        current.target_key,
      ),
      special_requirements: uniqueRecordValues([
        ...(current.special_requirements ?? []),
        ...(contract.special_requirements ?? []),
      ]),
      effects: uniqueRecordValues([...(current.effects ?? []), ...(contract.effects ?? [])]),
    });
  });
  return Array.from(merged.values());
}

export type BuildTargetActionRequirementRowsOptions = {
  contracts: PublicTargetActionContract[];
  knownFacts: PlayerGameState["known_facts"];
  visibleNodeKeys: Set<string>;
  resourceName: (key: string) => string;
  nodeName: (key: string, candidate?: string | null) => string;
  nodeByKey: Map<string, PublicNode>;
};

export function buildTargetActionRequirementRows({
  contracts,
  knownFacts,
  visibleNodeKeys,
  resourceName,
  nodeName,
  nodeByKey,
}: BuildTargetActionRequirementRowsOptions): TargetActionRequirementRow[] {
  return contracts.flatMap((contract) => {
    const typedRequirements = uniquePublicResourceRequirements(
      contract.action_key,
      contract.resource_requirements,
      contract.target_key,
    );
    const representedTypedIdentities = new Set<string>();
    const resourceParts = Object.entries(contract.cost ?? {}).flatMap(([resourceKey, amount]) => {
      const matchingRequirements = typedRequirements.filter(
        (requirement) => requirement.resource_key === resourceKey && requirement.minimum === amount,
      );
      if (matchingRequirements.length === 0) {
        return [`${resourceName(resourceKey)} ×${String(amount)}`];
      }
      return matchingRequirements.map((requirement) => {
        representedTypedIdentities.add(
          publicResourceRequirementIdentity(contract.action_key, requirement, contract.target_key),
        );
        return `${resourceName(requirement.resource_key)} ×${String(requirement.minimum)}`;
      });
    });
    const specialParts = (contract.special_requirements ?? []).flatMap((requirement) => {
      const nodeKey = typeof requirement.node_key === "string" ? requirement.node_key : null;
      const factKey = typeof requirement.fact_key === "string" ? requirement.fact_key : null;
      if (!nodeKey || !factKey || !visibleNodeKeys.has(nodeKey)) return [];
      const fact = knownFacts.find((item) => item.node_key === nodeKey && item.fact_key === factKey);
      if (!fact) return [];
      const text = publicFactRequirementText(
        requirement,
        fact,
        nodeName(nodeKey, nodeByKey.get(nodeKey)?.name),
      );
      return text ? [`前置条件：${text}`] : [];
    });
    const parts = [
      ...resourceParts,
      ...publicResourceRequirementParts(
        typedRequirements.filter(
          (requirement) => !representedTypedIdentities.has(
            publicResourceRequirementIdentity(contract.action_key, requirement, contract.target_key),
          ),
        ),
        resourceName,
      ),
      ...specialParts,
    ];
    if (parts.length === 0) return [];
    return [{
      key: `${contract.target_key}:requirement:${contract.action_key}`,
      actionKey: contract.action_key,
      label: `${contract.action_name}：`,
      value: parts.join("、"),
      kind: "DETAIL",
    }];
  });
}

type ResourceCandidate = {
  resourceKey: string;
  resourceName: string;
  poolKey: string;
  quantity: number;
  availability: "AVAILABLE" | "UNAVAILABLE";
  availabilityRequirement: Record<string, unknown> | null;
  availabilityRequirementStatus: "KNOWN" | "UNKNOWN" | null;
  source: "PROJECTION" | "ASSOCIATED";
  hasExplicitPoolKey: boolean;
  displayUnit?: string | null;
};

function resourceCandidateFromPool(
  resourceKey: string,
  resourceName: string,
  displayUnit: string | null | undefined,
  pool: {
    pool_key: string;
    quantity: number;
    facility_key: string | null;
    availability: "AVAILABLE" | "UNAVAILABLE";
    availability_requirement?: Record<string, unknown>;
    availability_requirement_status?: "KNOWN" | "UNKNOWN";
  },
): ResourceCandidate {
  return {
    resourceKey,
    resourceName,
    poolKey: pool.pool_key,
    quantity: pool.quantity,
    availability: pool.availability,
    availabilityRequirement: pool.availability_requirement ?? null,
    availabilityRequirementStatus: pool.availability_requirement_status ?? null,
    source: "PROJECTION",
    hasExplicitPoolKey: true,
    displayUnit,
  };
}

function resourceCandidateFromAssociated(
  resource: Record<string, unknown>,
  fallbackPoolKey: string,
): ResourceCandidate | null {
  const resourceKey = typeof resource.resource_key === "string" ? resource.resource_key : null;
  const quantity = typeof resource.quantity === "number" ? resource.quantity : null;
  if (!resourceKey || quantity === null) return null;
  return {
    resourceKey,
    resourceName: typeof resource.resource_name === "string" ? resource.resource_name : resourceKey,
    poolKey: typeof resource.pool_key === "string" ? resource.pool_key : fallbackPoolKey,
    quantity,
    availability: resource.availability === "UNAVAILABLE" ? "UNAVAILABLE" : "AVAILABLE",
    availabilityRequirement: resource.availability_requirement
      && typeof resource.availability_requirement === "object"
      && !Array.isArray(resource.availability_requirement)
      ? resource.availability_requirement as Record<string, unknown>
      : null,
    availabilityRequirementStatus: resource.availability_requirement_status === "KNOWN"
      ? "KNOWN"
      : resource.availability_requirement_status === "UNKNOWN"
        ? "UNKNOWN"
        : null,
    source: "ASSOCIATED",
    hasExplicitPoolKey: typeof resource.pool_key === "string",
    displayUnit: typeof resource.display_unit === "string"
      ? resource.display_unit
      : typeof resource.unit === "string" ? resource.unit : null,
  };
}

function collectFacilityResourceCandidates(
  nodeKey: string,
  resourceIntelligence: ResourceIntelligence | undefined,
  associatedResources: Array<Record<string, unknown>>,
): ResourceCandidate[] {
  const candidates: ResourceCandidate[] = [];
  if (resourceIntelligence) {
    Object.values(resourceIntelligence.regions).forEach((region) => {
      Object.entries(region.resources ?? {}).forEach(([resourceKey, resource]) => {
        resource.pools.forEach((pool) => {
          if (pool.facility_key !== nodeKey) return;
          candidates.push(resourceCandidateFromPool(
            resourceKey,
            resource.resource_name,
            resource.display_unit ?? resource.unit,
            pool,
          ));
        });
      });
    });
    Object.entries(resourceIntelligence.global_resources).forEach(([resourceKey, resource]) => {
      resource.pools.forEach((pool) => {
        if (pool.facility_key !== nodeKey) return;
        candidates.push(resourceCandidateFromPool(
          resourceKey,
          resource.resource_name,
          resource.display_unit ?? resource.unit,
          pool,
        ));
      });
    });
  }
  associatedResources.forEach((resource, index) => {
    const candidate = resourceCandidateFromAssociated(
      resource,
      `associated:${nodeKey}:${index}`,
    );
    if (candidate) candidates.push(candidate);
  });
  return candidates;
}

function unlockIdentityForRequirement(
  requirement: Record<string, unknown> | null,
  subjectName: string,
  factName?: string,
): string | undefined {
  if (!requirement) return undefined;
  return factName
    ? `${subjectName}满足${factName}条件`
    : `${subjectName}满足解锁条件`;
}

export function buildFacilityResourceRows(
  nodeKey: string,
  resourceIntelligence: ResourceIntelligence | undefined,
  associatedResources: Array<Record<string, unknown>>,
  resolveResourceName: (key: string, candidate?: string) => string,
  resolveNodeName: (key: string, candidate?: string | null) => string = () => "相关设施",
  resolveFactName: (nodeKey: string, factKey: string) => string | undefined = () => undefined,
  producerBindings: PublicProducerBinding[] = [],
): FacilityResourcePresentationRow[] {
  const candidates = collectFacilityResourceCandidates(nodeKey, resourceIntelligence, associatedResources);
  const byResource = new Map<string, ResourceCandidate[]>();
  candidates.forEach((candidate) => {
    const rows = byResource.get(candidate.resourceKey) ?? [];
    rows.push(candidate);
    byResource.set(candidate.resourceKey, rows);
  });

  return Array.from(byResource.entries()).map(([resourceKey, resourceCandidates]) => {
    const projectionCandidates = resourceCandidates.filter((candidate) => candidate.source === "PROJECTION");
    const associatedCandidates = resourceCandidates.filter((candidate) => candidate.source === "ASSOCIATED");
    const quantityCandidates = projectionCandidates.length > 0
      ? projectionCandidates
      : associatedCandidates.some((candidate) => candidate.hasExplicitPoolKey)
        ? associatedCandidates.filter((candidate) => candidate.hasExplicitPoolKey)
        : [associatedCandidates
          .slice()
          .sort((left, right) => {
            const leftScore = (left.availabilityRequirementStatus === "KNOWN" ? 2 : 0)
              + (left.availability === "UNAVAILABLE" ? 1 : 0);
            const rightScore = (right.availabilityRequirementStatus === "KNOWN" ? 2 : 0)
              + (right.availability === "UNAVAILABLE" ? 1 : 0);
            return rightScore - leftScore || right.quantity - left.quantity;
          })[0]];
    const seenPools = new Set<string>();
    const quantity = quantityCandidates.reduce((sum, candidate) => {
      if (seenPools.has(candidate.poolKey)) return sum;
      seenPools.add(candidate.poolKey);
      return sum + candidate.quantity;
    }, 0);
    const metadataCandidates = [...projectionCandidates, ...resourceCandidates.filter(
      (candidate) => candidate.source === "ASSOCIATED",
    )];
    const unavailable = metadataCandidates.some((candidate) => candidate.availability === "UNAVAILABLE");
    const requirementCandidate = metadataCandidates.find((candidate) => candidate.availabilityRequirement);
    const requirementStatus = metadataCandidates.some(
      (candidate) => candidate.availabilityRequirementStatus === "KNOWN",
    )
      ? "KNOWN"
      : metadataCandidates.some((candidate) => candidate.availabilityRequirementStatus === "UNKNOWN")
        ? "UNKNOWN"
        : null;
    const requirement = requirementCandidate?.availabilityRequirement ?? null;
    const requirementNodeKey = requirement && typeof requirement.node_key === "string"
      ? requirement.node_key
      : null;
    const requirementNodeName = requirementNodeKey
      ? resolveNodeName(requirementNodeKey)
      : "相关设施";
    const requirementFactKey = requirement && typeof requirement.fact_key === "string"
      ? requirement.fact_key
      : null;
    const requirementFactName = requirementNodeKey && requirementFactKey
      ? resolveFactName(requirementNodeKey, requirementFactKey)
      : undefined;
    const resourceBinding = producerBindings.find(
      (binding) => binding.producer_kind === "RESOURCE_AVAILABILITY"
        && binding.target_key === nodeKey
        && binding.outputs.some((output) => output.resource_key === resourceKey),
    );
    const resourceOutput = resourceBinding?.outputs.find(
      (output) => output.resource_key === resourceKey,
    );
    const bindingRequirement = resourceBinding?.requirements.find(
      (candidate) => candidate.kind === "FACT"
        || candidate.kind === "STATE"
        || candidate.kind === "SPECIAL",
    );
    const bindingUnlockText = resourceBinding && resourceOutput?.status === "UNSATISFIED"
      ? unlockIdentityForRequirement(
        bindingRequirement ? { ...bindingRequirement } : {},
        requirementNodeName,
        requirementFactName,
      )
      : undefined;
    return {
      key: `${nodeKey}:resource:${resourceKey}`,
      resourceKey,
      resourceName: resolveResourceName(
        resourceKey,
        metadataCandidates.find((candidate) => candidate.resourceName !== resourceKey)?.resourceName,
      ),
      quantity,
      availability: unavailable ? "UNAVAILABLE" : "AVAILABLE",
      availabilityRequirement: requirement,
      availabilityRequirementStatus: requirementStatus,
      displayUnit: metadataCandidates.find((candidate) => candidate.displayUnit)?.displayUnit ?? null,
      unlockText: resourceBinding
        ? bindingUnlockText
        : unavailable && requirementStatus === "KNOWN"
          ? unlockIdentityForRequirement(requirement, requirementNodeName, requirementFactName)
          : undefined,
    };
  });
}

export type BuildFacilityDetailRowsOptions = {
  nodeKey: string;
  knownFacts: PublicFact[];
  knownRelations: PublicRelation[];
  knownActionRequirements: NonNullable<PlayerGameState["known_action_requirements"]>;
  producerBindings: PublicProducerBinding[];
  facilityResourceRows: FacilityResourcePresentationRow[];
  resourceName: (key: string) => string;
  resolveNodeName: (key: string, candidate?: string | null) => string;
  presentationOrderPolicy?: FacilityPresentationOrderPolicy;
  presentation?: Pick<PublicPresentation, "entity_detail" | "knowledge_level" | "semantic_order" | "resource_order" | "relation_order">;
};

function producerRequirementValue(
  requirement: PublicProducerRequirement,
  knownFacts: ProducerFact[],
  resourceName: (key: string) => string,
  resolveNodeName: (key: string, candidate?: string | null) => string,
): string | null {
  if (requirement.kind === "RESOURCE") {
    if (typeof requirement.resource_key !== "string" || typeof requirement.minimum !== "number") {
      return null;
    }
    return `${resourceName(requirement.resource_key)} ×${String(requirement.minimum)}`;
  }
  if (requirement.kind === "ROLE") {
    return requirement.display_name ?? null;
  }
  if (requirement.kind === "SOURCE") {
    if (requirement.source_kind === "POWER_SOURCE_READINESS") return null;
    if (requirement.status === "SATISFIED") return "已满足";
    if (requirement.status === "UNSATISFIED") return "未具备";
    return "未知";
  }
  if (requirement.kind !== "FACT" && requirement.kind !== "STATE" && requirement.kind !== "SPECIAL") {
    return requirement.status === "UNKNOWN" ? "未知" : null;
  }
  const fact = knownFacts.find(
    (item) => item.node_key === requirement.node_key && item.fact_key === requirement.fact_key,
  );
  if (!fact) return null;
  if (requirement.status === "UNKNOWN") return `${factDisplayLabel(fact)}：当前状态未知`;
  const subjectName = requirement.node_key
    ? resolveNodeName(requirement.node_key, fact.node_name)
    : "相关设施";
  const text = publicFactRequirementText(
    {
      operator: requirement.operator,
      value: requirement.value,
      values: requirement.values,
      fact_label: factDisplayLabel(fact),
    },
    fact,
    subjectName,
  );
  return text ?? `${factDisplayLabel(fact)}：当前状态已知`;
}

function producerRequirementRows(
  binding: ProducerBinding,
  knownFacts: ProducerFact[],
  resourceName: (key: string) => string,
  resolveNodeName: (key: string, candidate?: string | null) => string,
): FacilityDetailRow[] {
  const rows: FacilityDetailRow[] = [];
  const resources = binding.requirements
    .filter((requirement) => requirement.kind === "RESOURCE")
    .map((requirement) => producerRequirementValue(
      requirement,
      knownFacts,
      resourceName,
      resolveNodeName,
    ))
    .filter((value): value is string => Boolean(value));
  if (resources.length > 0) {
    rows.push({
      key: `${binding.binding_key}:resources`,
      label: `${binding.action_name}：`,
      value: resources.join("、"),
      kind: "RECOVERY",
    });
  }
  binding.requirements
    .filter((requirement) => requirement.kind !== "RESOURCE")
    .forEach((requirement) => {
      const value = producerRequirementValue(
        requirement,
        knownFacts,
        resourceName,
        resolveNodeName,
      );
      if (!value) return;
      const label = requirement.kind === "ROLE"
        ? "所需队伍："
        : requirement.kind === "FACT" || requirement.kind === "STATE"
          ? "前置条件："
          : requirement.kind === "SOURCE"
            ? "来源条件："
            : "特殊要求：";
      rows.push({
        key: `${binding.binding_key}:${requirement.key}`,
        label,
        value,
        kind: "RECOVERY",
      });
    });
  return rows;
}

export function buildFacilityDetailRows({
  nodeKey,
  knownFacts,
  knownRelations,
  knownActionRequirements,
  producerBindings,
  facilityResourceRows,
  resourceName,
  resolveNodeName,
  presentationOrderPolicy = DEFAULT_FACILITY_PRESENTATION_ORDER_POLICY,
  presentation,
}: BuildFacilityDetailRowsOptions): FacilityDetailRow[] {
  const knowledgeLevel = presentation?.knowledge_level ?? "A+B+C";
  const includeRecovery = knowledgeLevel !== "A";
  const includeRequirements = knowledgeLevel === "A+B+C";
  const factBlocks: FacilityPresentationBlock[] = knownFacts
    .filter((fact) => fact.node_key === nodeKey)
    .filter((fact) => {
      const role = resolveFacilityFactRole(fact);
      if (role === "REQUIREMENT_ONLY") return false;
      return role !== "SUPPORTING" || presentation?.entity_detail === "CAUSALITY";
    })
    .map((fact): FacilityPresentationBlock => {
      const producers = producerBindings
        .filter((binding) => binding.target_key === nodeKey)
        .filter((binding) => binding.outputs.some(
          (output) => output.target_key === nodeKey
            && output.fact_key === fact.fact_key
            && output.status === "UNSATISFIED",
        ))
        .slice()
        .sort((left, right) => compareStableText(left.binding_key, right.binding_key));
      return {
        rows: [
          {
            key: `${nodeKey}:fact:${fact.fact_key}`,
            label: `${factDisplayLabel(fact)}：`,
            value: factDisplayValue(fact),
            kind: "DETAIL" as const,
          },
          ...(includeRecovery ? producers.map((binding) => ({
            key: `${binding.binding_key}:output:${fact.fact_key}`,
            label: "恢复方式：",
            value: binding.action_name,
            kind: "RECOVERY" as const,
          })) : []),
        ],
        producerBindings: producers,
        descriptor: factPresentationOrderDescriptor(fact, presentationOrderPolicy),
      };
    });

  const sourceReadiness = knownActionRequirements
    .flatMap((action) => action.source_requirements ?? [])
    .find(
      (requirement) =>
        requirement.source_node_key === nodeKey
        && requirement.kind === "POWER_SOURCE_READINESS",
    );
  const semanticBlocks: FacilityPresentationBlock[] = sourceReadiness
    ? [{
        rows: [
          {
            key: nodeKey + ":power-readiness",
            label: "送电能力：",
            value: sourceReadiness.status === "SATISFIED" ? "已具备" : "未具备",
            kind: "DETAIL" as const,
          },
          ...(sourceReadiness.status === "SATISFIED"
            ? []
            : [{
              key: nodeKey + ":power-readiness:recovery",
              label: "恢复条件：",
              value: "设施恢复正常运行并恢复供电",
              kind: "RECOVERY" as const,
            }]),
        ],
        descriptor: {
          group: "SEMANTIC" as const,
          priority: Number.MAX_SAFE_INTEGER,
          stableKey: `${nodeKey}:power-readiness`,
        },
      }]
    : [];

  const resourceBlocks: FacilityPresentationBlock[] = facilityResourceRows.map((resource) => {
    const availability = resource.availability === "UNAVAILABLE" ? "暂不可用" : "可用";
    return {
      rows: [
        {
          key: resource.key,
          label: `${resource.resourceName}：`,
          value: `×${String(resource.quantity)}${availability === "可用" ? "" : `，${availability}`}`,
          kind: "DETAIL" as const,
        },
        ...(resource.unlockText
          ? [{
            key: `${resource.key}:unlock`,
            label: "解锁条件：",
            value: resource.unlockText,
            kind: "UNLOCK" as const,
          }]
          : []),
      ],
      descriptor: {
        group: "RESOURCE" as const,
        stableKey: resource.resourceKey,
      },
    };
  });

  const relationEntries = knownRelations
    .map((relation) => ({
      relation,
      stableKey: `${relation.relation_type_key}\u0000${relation.target_node_key}`,
    }))
    .sort((left, right) => compareStableText(left.stableKey, right.stableKey));
  const relationLabels = new Map<string, string[]>();
  const relationStableKeys = new Map<string, string>();
  relationEntries.forEach(({ relation, stableKey }) => {
    const hasSafeRelationMetadata = Boolean(relation.relation_type_name)
      || relation.is_structural !== undefined;
    const label = hasSafeRelationMetadata
      ? knownRelationDescription(relation.relation_type_key, relation.relation_type_name)
      : legacyFacilityRelationDescription(relation.relation_type_key, relation.relation_type_name);
    const targets = relationLabels.get(label) ?? [];
    targets.push(resolveNodeName(relation.target_node_key, relation.target_node_name));
    relationLabels.set(label, targets);
    if (!relationStableKeys.has(label)) relationStableKeys.set(label, stableKey);
  });
  const relationBlocks: FacilityPresentationBlock[] = Array.from(relationLabels.entries()).map(([label, targets], index) => ({
    rows: [{
      key: `${nodeKey}:relation:${index}`,
      label: `${label}：`,
      value: targets.join("、"),
      kind: "DETAIL" as const,
    }],
    descriptor: {
      group: "RELATION" as const,
      stableKey: relationStableKeys.get(label) ?? label,
    },
  }));

  const resourceOrder = presentation?.resource_order ?? ["NAME", "AMOUNT", "STATUS", "UNIT"];
  const orderedResourceBlocks = resourceBlocks.map((block, index) => {
    const resource = facilityResourceRows[index];
    const parts = resourceOrder.flatMap((slot) => {
      if (slot === "AMOUNT") return [`\u00d7${String(resource.quantity)}`];
      if (slot === "STATUS" && resource.availability === "UNAVAILABLE") return ["\u6682\u4e0d\u53ef\u7528"];
      if (slot === "UNIT" && resource.displayUnit) return [resource.displayUnit];
      return [];
    });
    return {
      ...block,
      rows: block.rows.map((row, rowIndex) => rowIndex === 0 ? {
        ...row,
        label: resourceOrder.includes("NAME") ? row.label : "\u8d44\u6e90\uff1a",
        value: parts.join("\uff0c"),
      } : row),
    };
  });
  const relationOrder = presentation?.relation_order ?? ["TYPE", "TARGET", "VISIBILITY"];
  const orderedRelationBlocks = relationBlocks.map((block) => ({
    ...block,
    rows: block.rows.map((row) => ({
      ...row,
      label: relationOrder.includes("TYPE") ? row.label : "\u5173\u7cfb\uff1a",
      value: [
        ...(relationOrder.includes("TARGET") ? [row.value] : []),
      ].join("\u3001"),
    })),
  }));

  const semanticGroup: Partial<Record<PublicPresentation["semantic_order"][number], FacilityPresentationGroup[]>> = {
    STATUS: ["HEADER_PRIMARY", "HEADER_SECONDARY"],
    FACTS: ["SEMANTIC", "OTHER"],
    RESOURCES: ["RESOURCE"],
    RELATIONS: ["RELATION"],
  };
  const configuredGroups = (presentation?.semantic_order ?? [])
    .flatMap((slot) => semanticGroup[slot] ?? []);
  const groupOrder = configuredGroups.length > 0
    ? [...new Set(configuredGroups)]
    : presentationOrderPolicy.groupOrder;

  const blocks = resolvePresentationOrder(
    [
      ...factBlocks,
      ...semanticBlocks,
      ...orderedResourceBlocks,
      ...orderedRelationBlocks,
    ],
    (block) => block.descriptor,
    { ...presentationOrderPolicy, groupOrder },
  );
  const usedBindingKeys = new Set<string>();
  return blocks.flatMap((block) => {
    const rows = [...block.rows];
    block.producerBindings?.forEach((binding) => {
      if (usedBindingKeys.has(binding.binding_key)) return;
      if (includeRequirements) {
        rows.push(...producerRequirementRows(
          binding,
          knownFacts,
          resourceName,
          resolveNodeName,
        ));
      }
      usedBindingKeys.add(binding.binding_key);
    });
    return rows;
  });
}
