import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { TypedEditor, TypedEntityEditor } from "../components/TypedEditor";
import { InitializationWorkspace } from "../components/InitializationWorkspace";
import { FactDeleteDialog, type FactDeleteDialogState, type FactDeleteReference } from "../components/editor/FactDeleteDialog";
import { AuthoringOperationDialog, type AuthoringOperationDialogState, type AuthoringReference } from "../components/editor/AuthoringOperationDialog";
import { EditorConfirmDialog, EditorRenameDialog } from "../components/editor/EditorConfirmDialog";
import { CompletenessPanel, CompletenessSummary, ContextualCompleteness } from "../components/editor/CompletenessPanel";
import { ResourceSourceHintCreateDialog } from "../components/editor/ResourceSourceHintCreateDialog";
import { WorldGraph, type TopologyContext, type TopologySelection } from "../components/WorldGraph";
import {
  addObject,
  draftObjectIdentity,
  filterDraftObjects,
  nodeSemanticView,
  objectByKindAndKey,
  objectIdentity,
  replaceObject,
  sectionForKind,
  sectionObjects,
  sectionRoot,
  legacySections,
  sections,
  updateObjectName,
  updateSectionRoot,
  type EditorSection,
  type EntityKind,
  type JsonObject,
} from "../editor";
import { kindsBySection } from "../templates";
import { appendRootCollectionItem, moveRootCollectionItem, replaceRootCollectionItem, rootCollectionDefinitions, rootCollectionIdentity, rootCollectionIdentityLabel, rootCollectionItems, rootCollectionLabel, rootSingletonOwner, rootSingletonOwners, type RootCollectionKey, type RootCollectionSelection, type RootOwnerSelection } from "../editor-collections";
import { sectionStructure } from "../editor-structure";
import { editorLocatorFromValidation, editorLocatorHref } from "../editor-locator";
import { cloneWorkingDocument, deriveWorkingCopySaveState, workingCopyIsDirty, workingDocumentsEqual, type WorkingCopySaveState } from "../editor-working-copy";
import { buildEntityNeighborhood, buildScopeOverview, buildScopeTopology, findScopeForNode, nodeByTopologyKey, relationByTopologyKey } from "../topology-projection";
import type { CompletenessItem, Draft, DraftSandboxResult, InitializationPreview, ScenarioVersionDetail, ValidationResult } from "../types";
import { diagnosticMessage, editorSectionTaxonomy, editorTaxonomyGroups, errorText, kindLabels, sectionLabels, uiLabel } from "../ui";

type SaveState = WorkingCopySaveState;
const saveLabels: Record<SaveState, string> = { UNCHANGED: "未修改", DIRTY: "有未保存修改", SAVING: "保存中", CONFLICT: "草稿冲突", ERROR: "保存失败" };
type WorldView = "all" | "regions" | "facilities" | "transports";
const worldViewLabels: Record<WorldView, string> = { all: "全部节点", regions: "区域", facilities: "设施", transports: "交通" };
type ScopedCollectionSelection = { section: EditorSection; selection: RootOwnerSelection };

function isEditorSection(value: string): value is EditorSection {
  return [...sections, ...legacySections].includes(value as (typeof sections)[number] | (typeof legacySections)[number]);
}

function objectDisplayValue(value: JsonObject, fallback: string): string {
  if (typeof value.name === "string" && value.name.trim()) return value.name;
  if (typeof value.term === "string" && value.term.trim()) return value.term;
  return fallback;
}

function authoredReferenceDetails(document: JsonObject, locator: { object_kind: string; object_key: string | null; field_path: string | null }): Omit<FactDeleteReference, "href"> {
  const entityKinds = ["node_type", "node", "relation_type", "relation", "resource", "role", "actor", "interaction", "action", "rule", "derived_state", "public_reference"];
  const object = locator.object_key && entityKinds.includes(locator.object_kind)
    ? objectByKindAndKey(document, locator.object_kind as EntityKind, locator.object_key)
    : null;
  const nestedKinds = new Set(["action_parameter", "action_outcome", "action_binding", "action_target_role", "action_authority_limit", "action_authority_approval", "actor_doctrine", "actor_authority_limit", "actor_authority_approval"]);
  const nestedParts = locator.object_key?.split(":") ?? [];
  const parentAction = nestedKinds.has(locator.object_kind) && nestedParts[0]
    ? objectByKindAndKey(document, "action", nestedParts[0])
    : null;
  const name = object
    ? objectDisplayValue(object.value, locator.object_key ?? "(root)")
    : parentAction
      ? `${objectDisplayValue(parentAction.value, nestedParts[0])} · ${nestedParts.slice(1).join(":")}`
      : locator.object_key ?? (locator.field_path?.split(".").at(-1) ?? "(root)");
  const type = kindLabels[locator.object_kind] ?? locator.object_kind;
  return { type, name, path: locator.field_path ?? "(object)" };
}

function authoredReferenceHref(scenarioId: string, locator: { object_kind: string; object_key: string | null; field_path: string | null }): string | null {
  const entityKinds = ["node_type", "node", "relation_type", "relation", "resource", "role", "actor", "interaction", "action", "rule", "derived_state", "public_reference"];
  if (locator.object_key && entityKinds.includes(locator.object_kind)) {
    return editorLocatorHref({ owner: "entity", section: sectionForKind(locator.object_kind), kind: locator.object_kind as EntityKind, objectKey: locator.object_key, fieldPath: locator.field_path }, scenarioId);
  }
  if (locator.object_kind === "metadata" && locator.field_path?.startsWith("locality")) return `/scenarios/${scenarioId}/edit/overview?focus_path=${encodeURIComponent(locator.field_path)}`;
  if (locator.object_kind === "initialization" && locator.field_path) {
    const [collection, identity] = locator.field_path.split(".");
    if (collection && identity) return `/scenarios/${scenarioId}/edit/initialization?domain=resources&group=${encodeURIComponent(collection.replaceAll("_", "-"))}&item=${encodeURIComponent(`pool:${identity}`)}`;
    return `/scenarios/${scenarioId}/edit/initialization`;
  }
  if (locator.object_kind === "planning" && locator.field_path?.startsWith("recovery_hints.")) return `/scenarios/${scenarioId}/edit/planning-recovery?owner=collection&collection=recovery_hints&item=${encodeURIComponent(JSON.stringify([locator.field_path.split(".").at(-1)]))}`;
  if (locator.object_kind === "planning") return `/scenarios/${scenarioId}/edit/planning-instructions`;
  if (locator.object_kind === "public_knowledge") return `/scenarios/${scenarioId}/edit/public-knowledge`;
  if (locator.object_kind.startsWith("action_") && locator.object_key) {
    const parentKey = locator.object_key.split(":", 1)[0];
    return editorLocatorHref({ owner: "entity", section: "actions", kind: "action", objectKey: parentKey, fieldPath: locator.field_path }, scenarioId);
  }
  return null;
}

function authoredFactName(document: JsonObject, nodeKey: string, factKey: string): string {
  const node = objectByKindAndKey(document, "node", nodeKey);
  const facts = node?.value.facts;
  const fact = Array.isArray(facts) ? facts.find((item) => item && typeof item === "object" && !Array.isArray(item) && (item as JsonObject).key === factKey) as JsonObject | undefined : undefined;
  return typeof fact?.name === "string" && fact.name.trim() ? fact.name : factKey;
}

function factDeletePreflightError(error: unknown): { reason: string; code?: string } {
  if (error instanceof ApiError) {
    const details = error.details && typeof error.details === "object" && !Array.isArray(error.details)
      ? error.details as Record<string, unknown>
      : null;
    const issues = details && Array.isArray(details.errors)
      ? details.errors
      : details && Array.isArray(details.issues)
        ? details.issues
        : [];
    const firstIssue = issues.find((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)));
    if (firstIssue) {
      const rawPath = firstIssue.path ?? firstIssue.loc;
      const path = Array.isArray(rawPath)
        ? rawPath.filter((part) => part !== "body").map(String).join(".")
        : typeof rawPath === "string" ? rawPath : "";
      const message = typeof firstIssue.message === "string" ? firstIssue.message : typeof firstIssue.msg === "string" ? firstIssue.msg : "请求字段不符合接口要求";
      return {
        code: error.code,
        reason: `接口校验未通过${path ? `（${path}）` : ""}：${message}`,
      };
    }
    if (error.code === "SCENARIO_DRAFT_CONFLICT") {
      return { code: error.code, reason: "服务器上的草稿版本已变化，请重新加载后再试。" };
    }
    const message = error.message && error.message !== "Request validation failed" ? `：${error.message}` : "。请检查当前工作副本后重试。";
    return { code: error.code, reason: `${error.code === "VALIDATION_ERROR" ? "接口校验失败" : uiLabel(error.code)}（${error.code}）${message}` };
  }
  if (error instanceof Error && error.message) return { reason: error.message };
  return { reason: "服务端未能完成删除预检，请稍后重试。" };
}

function authoringReferencesFromError(error: unknown, document: JsonObject, scenarioId: string): AuthoringReference[] {
  if (!(error instanceof ApiError)) return [];
  const details = error.details && typeof error.details === "object" && !Array.isArray(error.details)
    ? error.details as { references?: unknown }
    : {};
  if (!Array.isArray(details.references)) return [];
  return details.references.flatMap((item): AuthoringReference[] => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const source = (item as { source?: unknown }).source;
    if (!source || typeof source !== "object" || Array.isArray(source)) return [];
    const locator = source as { object_kind?: unknown; object_key?: unknown; field_path?: unknown };
    const authoredLocator = {
      object_kind: typeof locator.object_kind === "string" ? locator.object_kind : "unknown",
      object_key: typeof locator.object_key === "string" ? locator.object_key : null,
      field_path: typeof locator.field_path === "string" ? locator.field_path : null,
    };
    return [{
      ...authoredReferenceDetails(document, authoredLocator),
      href: authoredReferenceHref(scenarioId, authoredLocator),
    }];
  });
}

function documentDifferenceCount(left: unknown, right: unknown): number {
  if (Object.is(left, right)) return 0;
  if (Array.isArray(left) && Array.isArray(right)) {
    const shared = Math.min(left.length, right.length);
    return Math.abs(left.length - right.length) + Array.from({ length: shared }, (_, index) => documentDifferenceCount(left[index], right[index])).reduce((sum, value) => sum + value, 0);
  }
  if (left && right && typeof left === "object" && typeof right === "object" && !Array.isArray(left) && !Array.isArray(right)) {
    const keys = new Set([...Object.keys(left as object), ...Object.keys(right as object)]);
    return Array.from(keys).reduce((sum, key) => sum + documentDifferenceCount((left as Record<string, unknown>)[key], (right as Record<string, unknown>)[key]), 0);
  }
  return 1;
}

function validationStatusLabel(status: string): string {
  if (status === "FAILED") return "验证失败";
  if (status === "PASSED" || status === "VALID" || status === "VALIDATED") return "已验证";
  return "未验证";
}

function VersionStatusBadges({ draft, published, saveState }: { draft: Draft; published: ScenarioVersionDetail | null; saveState: SaveState }) {
  const differenceCount = published ? documentDifferenceCount(draft.definition_document, published.definition_document) : null;
  const draftAhead = differenceCount !== null && differenceCount > 0;
  const draftConflict = saveState === "CONFLICT";
  const draftTitle = draftConflict ? "当前草稿与服务器版本发生冲突。" : draftAhead ? "当前草稿与最新已发布版本不同。" : "当前保存的草稿版本。";
  return <span className="version-badges" aria-label="版本状态">
    <span className={`version-badge version-badge-draft${draftConflict ? " conflict" : draftAhead ? " warning" : ""}`} title={draftTitle}>{draftConflict ? "草稿冲突" : `草稿 r${draft.revision} · ${validationStatusLabel(draft.validation_status)}`}</span>
    <span className={`version-badge version-badge-published${published ? "" : " empty"}`} title={published ? `最新已发布版本 v${published.version_number}` : "当前没有已发布版本。"}>{published ? `已发布 v${published.version_number}` : "暂无发布版本"}</span>
  </span>;
}

function rootEditorKey(section: EditorSection): string | null {
  if (section === "overview") return "metadata";
  if (section === "goal-resolution") return "goal_resolution";
  if (section === "public-knowledge") return "public_knowledge";
  if (section === "initialization") return section;
  if (section === "planning" || section === "planning-instructions" || section === "planning-recovery") return "planning";
  return null;
}

function normalizeEditorNavigation(to: string, scenarioId: string): string {
  const legacyWorldObjectPrefix = `/scenarios/${scenarioId}/edit/world/`;
  if (to.startsWith(legacyWorldObjectPrefix)) {
    return `/scenarios/${scenarioId}/edit/world-entities/${to.slice(legacyWorldObjectPrefix.length)}`;
  }

  const legacyRelationPrefix = `/scenarios/${scenarioId}/edit/relations/`;
  if (!to.startsWith(legacyRelationPrefix)) return to;
  const suffix = to.slice(legacyRelationPrefix.length);
  const queryStart = suffix.indexOf("?");
  if (queryStart < 0) return to;
  const objectPath = suffix.slice(0, queryStart);
  const query = new URLSearchParams(suffix.slice(queryStart + 1));
  const kind = query.get("kind");
  if (kind === "relation_type") {
    query.delete("kind");
    const serialized = query.toString();
    return `/scenarios/${scenarioId}/edit/relation-types/${objectPath}${serialized ? `?${serialized}` : ""}`;
  }
  if (kind === "relation") {
    query.delete("kind");
    const serialized = query.toString();
    return `/scenarios/${scenarioId}/edit/relations/${objectPath}${serialized ? `?${serialized}` : ""}`;
  }
  return to;
}

export function EditorPage() {
  const { scenarioId = "", section: routeSection = "overview", objectKey } = useParams();
  const requestedSection: EditorSection = isEditorSection(routeSection) ? routeSection : "overview";
  const section: EditorSection = requestedSection === "world" && objectKey ? "world-entities" : requestedSection === "planning" ? "planning-instructions" : requestedSection;
  const structure = sectionStructure(section);
  const singletonOwner = useMemo(() => rootSingletonOwner(section), [section]);
  const singletonOwners = useMemo(() => rootSingletonOwners(section), [section]);
  const navigateRouter = useNavigate();
  const navigate = (to: string, options?: { replace?: boolean }) => navigateRouter(normalizeEditorNavigation(to, scenarioId), options);
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    if (requestedSection !== "planning") return;
    const recovery = searchParams.get("collection") === "recovery_hints" || searchParams.get("owner") === "planning-recovery";
    navigateRouter(`/scenarios/${scenarioId}/edit/${recovery ? "planning-recovery" : "planning-instructions"}${searchParams.size ? `?${searchParams}` : ""}`, { replace: true });
  }, [navigateRouter, requestedSection, scenarioId, searchParams]);
  const requestedKind = searchParams.get("kind");
  const routeKind = structure.owners.entityKinds.includes(requestedKind as EntityKind) ? requestedKind as EntityKind : null;
  const queryClient = useQueryClient();
  const draftQuery = useQuery({ queryKey: ["draft", scenarioId], queryFn: () => api.draft(scenarioId) });
  const scenarioQuery = useQuery({ queryKey: ["scenario", scenarioId], queryFn: () => api.scenario(scenarioId), enabled: Boolean(scenarioId && typeof api.scenario === "function"), retry: false });
  const publishedVersionId = scenarioQuery.data?.current_published_version_id ?? null;
  const publishedVersionQuery = useQuery({ queryKey: ["scenario-version", scenarioId, publishedVersionId], queryFn: () => api.scenarioVersion(scenarioId, publishedVersionId!), enabled: Boolean(publishedVersionId && typeof api.scenarioVersion === "function"), retry: false });
  const [serverDraft, setServerDraft] = useState<Draft | null>(null);
  const [workingDocument, setWorkingDocument] = useState<Record<string, unknown> | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("UNCHANGED");
  const [message, setMessage] = useState("");
  const [factDeleteDialog, setFactDeleteDialog] = useState<FactDeleteDialogState | null>(null);
  const [authoringDialog, setAuthoringDialog] = useState<AuthoringOperationDialogState | null>(null);
  const [discardRequest, setDiscardRequest] = useState<string | null>(null);
  const [renameRequest, setRenameRequest] = useState<{ subject: string; currentKey: string } | null>(null);
  const [resourceSourceCreateOpen, setResourceSourceCreateOpen] = useState(false);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [sandboxGoal, setSandboxGoal] = useState("");
  const [sandbox, setSandbox] = useState<DraftSandboxResult | null>(null);
  const [worldView, setWorldView] = useState<WorldView>("all");
  const [objectSearch, setObjectSearch] = useState("");
  const [kindFilter, setKindFilter] = useState("all");
  const [collectionSelectionState, setCollectionSelectionState] = useState<ScopedCollectionSelection | null>(null);
  const setCollectionSelection = (selection: RootOwnerSelection | null, replace = false) => {
    setCollectionSelectionState(selection ? { section, selection } : null);
    const next = new URLSearchParams(searchParams);
    next.delete("owner");
    next.delete("collection");
    next.delete("item");
    if (selection?.owner === "singleton") next.set("owner", selection.key);
    if (selection?.owner === "instruction") {
      next.set("owner", "instruction");
      next.set("item", String(selection.index));
    }
    if (selection?.owner === "collection") {
      next.set("owner", "collection");
      next.set("collection", selection.collection);
      next.set("item", selection.identity);
    }
    setSearchParams(next, { replace });
  };
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [topologyContext, setTopologyContext] = useState<TopologyContext>({ kind: "overview" });
  const [topologySelection, setTopologySelection] = useState<TopologySelection>(null);
  const [topologyFocusNodeKey, setTopologyFocusNodeKey] = useState<string | null>(null);
  const serverDraftRef = useRef<Draft | null>(null);
  const workingDocumentRef = useRef<Record<string, unknown> | null>(null);

  const hydrateDraft = (draft: Draft) => {
    const document = cloneWorkingDocument(draft.definition_document);
    serverDraftRef.current = draft;
    workingDocumentRef.current = document;
    setServerDraft(draft);
    setWorkingDocument(document);
    setSaveState("UNCHANGED");
    setValidation(null);
    setSandbox(null);
    setMessage("");
    setFactDeleteDialog(null);
    setAuthoringDialog(null);
    setDiscardRequest(null);
    setRenameRequest(null);
  };

  useEffect(() => {
    if (draftQuery.data && serverDraftRef.current?.scenario_id !== draftQuery.data.scenario_id) hydrateDraft(draftQuery.data);
  }, [draftQuery.data, scenarioId]);
  useEffect(() => { setObjectSearch(""); setKindFilter("all"); }, [section, worldView]);
  useEffect(() => {
    setInspectorOpen(false);
    if (section === "world") {
      if (!objectKey) {
        setTopologyContext({ kind: "overview" });
        setTopologySelection(null);
      }
    } else {
      setTopologyContext({ kind: "overview" });
      setTopologySelection(null);
      setTopologyFocusNodeKey(null);
    }
  }, [section, objectKey, singletonOwner]);

  const save = useMutation({
    mutationFn: (value: { revision: number; document: Record<string, unknown> }) => api.saveDraft(scenarioId, value.revision, value.document),
    onMutate: () => setSaveState("SAVING"),
    onSuccess: (saved, variables) => {
      serverDraftRef.current = saved;
      setServerDraft(saved);
      queryClient.setQueryData(["draft", scenarioId], saved);
      if (workingDocumentsEqual(workingDocumentRef.current, variables.document)) {
        const document = cloneWorkingDocument(saved.definition_document);
        workingDocumentRef.current = document;
        setWorkingDocument(document);
        setSaveState("UNCHANGED");
      } else {
        setSaveState("DIRTY");
      }
      setMessage("");
      setValidation(null);
      setSandbox(null);
    },
    onError: (error) => {
      const conflict = error instanceof ApiError && error.code === "SCENARIO_DRAFT_CONFLICT";
      setSaveState(conflict ? "CONFLICT" : "ERROR");
      setMessage(errorText(error, conflict ? "服务器上的草稿已更新，请重新加载。" : "草稿保存失败。"));
    },
  });

  const refsQuery = useQuery({
    queryKey: ["working-copy-references", scenarioId, serverDraft?.revision, workingDocument],
    queryFn: () => api.analyzeWorkingCopyReferences(scenarioId, serverDraft!.revision, workingDocument!),
    enabled: Boolean(serverDraft && workingDocument && saveState !== "CONFLICT"),
  });
  const completenessQuery = useQuery({
    queryKey: ["working-copy-completeness", scenarioId, serverDraft?.revision, workingDocument],
    queryFn: () => api.completeness(scenarioId, serverDraft!.revision, workingDocument!),
    enabled: Boolean(serverDraft && workingDocument && saveState !== "CONFLICT" && typeof api.completeness === "function"),
    retry: false,
  });
  const initializationPreviewQuery = useQuery({
    queryKey: ["initialization-preview", scenarioId, serverDraft?.revision, workingDocument],
    queryFn: () => api.initializationPreview(scenarioId, serverDraft!.revision, workingDocument!),
    enabled: Boolean(serverDraft && workingDocument && saveState !== "CONFLICT" && (section === "initialization" || section === "validation")),
    retry: false,
    placeholderData: (previous) => previous,
  });
  const local = useMemo(() => serverDraft && workingDocument ? { ...serverDraft, definition_document: workingDocument } : null, [serverDraft, workingDocument]);
  const hasUnsavedChanges = workingCopyIsDirty(serverDraft, workingDocument);
  useEffect(() => {
    if (!hasUnsavedChanges) return undefined;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [hasUnsavedChanges]);
  const topologyModeActive = structure.mode === "BROWSER";
  const legacyRelationTarget = useMemo(() => {
    if (requestedSection !== "relations" || !objectKey) return null;
    if (requestedKind === "relation_type") return local ? "relation-types" as const : null;
    if (requestedKind === "relation") return local ? "relations" as const : null;
    if (requestedKind || !local) return null;
    if (objectByKindAndKey(local.definition_document, "relation_type", objectKey)) return "relation-types" as const;
    return null;
  }, [local, objectKey, requestedKind, requestedSection]);
  useEffect(() => {
    if (!legacyRelationTarget || !objectKey) return;
    const next = new URLSearchParams(searchParams);
    next.delete("kind");
    const query = next.toString();
    navigateRouter(`/scenarios/${scenarioId}/edit/${legacyRelationTarget}/${encodeURIComponent(objectKey)}${query ? `?${query}` : ""}`, { replace: true });
  }, [legacyRelationTarget, navigateRouter, objectKey, scenarioId, searchParams]);
  useEffect(() => {
    if (topologyModeActive && topologySelection) setInspectorOpen(true);
  }, [topologyModeActive, topologySelection]);

  const objects = useMemo(() => {
    if (!local) return [];
    return structure.master.source === "topology" ? nodeSemanticView(local.definition_document, worldView) : sectionObjects(local.definition_document, section);
  }, [local, section, structure.master.source, worldView]);
  const availableKinds = useMemo(() => Array.from(new Set(objects.map((item) => item.kind))), [objects]);
  const filteredObjects = useMemo(() => filterDraftObjects(objects, objectSearch, kindFilter), [objects, objectSearch, kindFilter]);
  const sectionValue = local ? sectionRoot(local.definition_document, section) : null;
  const planningInstructions = section === "planning-instructions" && sectionValue && typeof sectionValue === "object" && !Array.isArray(sectionValue) && Array.isArray((sectionValue as JsonObject).instructions)
    ? (sectionValue as JsonObject).instructions as string[]
    : [];
  const collectionDefinitions = useMemo(() => rootCollectionDefinitions(section), [section]);
  const collectionItems = useMemo(() => rootCollectionItems(section, sectionValue, local?.definition_document), [local?.definition_document, section, sectionValue]);
  const collectionSelection = collectionSelectionState?.section === section ? collectionSelectionState.selection : null;
  useEffect(() => {
    const select = (next: ScopedCollectionSelection | null) => {
      setCollectionSelectionState((current) => JSON.stringify(current) === JSON.stringify(next) ? current : next);
    };
    const owner = searchParams.get("owner");
    const collection = searchParams.get("collection");
    const identity = searchParams.get("item");
    if (section === "planning-instructions") {
      const requestedIndex = owner === "instruction" ? Number(identity) : -1;
      const index = Number.isInteger(requestedIndex) && requestedIndex >= 0 && requestedIndex < planningInstructions.length ? requestedIndex : -1;
      select(index >= 0 ? { section, selection: { owner: "instruction", index } } : null);
      return;
    }
    const collectionMatch = collectionDefinitions.find((definition) => definition.key === collection);
    if (owner === "collection" && collectionMatch && identity && collectionItems.some((item) => item.collection === collectionMatch.key && item.identity === identity)) {
      select({ section, selection: { owner: "collection", collection: collectionMatch.key, identity } });
      return;
    }
    const singletonMatch = singletonOwners.find((candidate) => owner === candidate.key);
    if (singletonMatch) {
      select({ section, selection: { owner: "singleton", key: singletonMatch.key } });
      return;
    }
    if (section === "planning-recovery") {
      select(null);
      return;
    }
    select(singletonOwner ? { section, selection: { owner: "singleton", key: singletonOwner.key } } : null);
  }, [collectionDefinitions, collectionItems, planningInstructions.length, searchParams, section, singletonOwner, singletonOwners]);
  const filteredCollectionItems = useMemo(() => {
    const query = objectSearch.trim().toLocaleLowerCase();
    return collectionItems.filter((item) => !query || [item.title, item.summary, item.identityLabel, item.collection].some((value) => value.toLocaleLowerCase().includes(query)));
  }, [collectionItems, objectSearch]);
  const visibleSingletonOwners = singletonOwners.filter((owner) => !objectSearch.trim() || [owner.label, owner.summary].some((value) => value.toLocaleLowerCase().includes(objectSearch.trim().toLocaleLowerCase())));
  const selectedCollectionItem = collectionSelection?.owner === "collection"
    ? collectionItems.find((item) => item.collection === collectionSelection.collection && item.identity === collectionSelection.identity) ?? null
    : null;
  const selectedInstruction = collectionSelection?.owner === "instruction" ? planningInstructions[collectionSelection.index] ?? null : null;
  const selectedSingletonOwner = collectionSelection?.owner === "singleton"
    ? singletonOwners.find((owner) => owner.key === collectionSelection.key) ?? null
    : collectionSelection?.owner === "collection" && collectionSelection.collection === "recovery_hints"
      ? singletonOwners.find((owner) => owner.key === "planning-recovery") ?? null
      : null;
  const planningRecoverySelected = section === "planning-recovery"
    && collectionSelection?.owner === "collection"
    && collectionSelection.collection === "recovery_hints";
  const selected = objects.find((item) => item.key === objectKey && (!routeKind || item.kind === routeKind)) ?? null;
  const focusPath = searchParams.get("focus_path");
  const editorFocusPath = focusPath ? `${selected ? `${selected.kind}.${selected.key}` : rootEditorKey(section) ?? ""}.${focusPath}` : null;
  const usedBy = refsQuery.data?.references.filter((edge) => edge.target.object_kind === selected?.kind && edge.target.object_key === selected?.key) ?? [];
  const topologyDocument = useMemo(() => local?.definition_document ?? {}, [local]);
  const topologyOverview = useMemo(() => buildScopeOverview(topologyDocument), [topologyDocument]);
  const topologyNode = topologySelection?.kind === "node" ? nodeByTopologyKey(topologyDocument, topologySelection.key) : null;
  const topologyScope = topologySelection?.kind === "scope" ? topologyOverview.scopes.find((scope) => scope.key === topologySelection.key) ?? null : null;
  const topologyRelation = topologySelection?.kind === "relation" ? relationByTopologyKey(topologyDocument, topologySelection.key) : null;
  const topologyPortal = topologySelection?.kind === "portal" ? buildScopeTopology(topologyDocument, topologySelection.scopeKey).portals.find((portal) => portal.key === topologySelection.key) ?? null : null;
  const topologyNeighborhood = topologyNode ? buildEntityNeighborhood(topologyDocument, topologyNode.key) : null;

  if (!local) return <main className="page"><p>正在加载草稿…</p></main>;

  const editDocument = (document: JsonObject) => {
    const next = cloneWorkingDocument(document);
    workingDocumentRef.current = next;
    setWorkingDocument(next);
    setSaveState(deriveWorkingCopySaveState(serverDraftRef.current, next));
    setValidation(null);
    setSandbox(null);
    setFactDeleteDialog(null);
  };
  const saveWorkingCopy = () => {
    const draft = serverDraftRef.current;
    const document = workingDocumentRef.current;
    if (!draft || !document || save.isPending) return;
    if (!workingCopyIsDirty(draft, document)) {
      setSaveState("UNCHANGED");
      return;
    }
    save.mutate({ revision: draft.revision, document: cloneWorkingDocument(document) });
  };
  const discardWorkingCopy = (): boolean => {
    const draft = serverDraftRef.current;
    if (!draft) return false;
    const document = cloneWorkingDocument(draft.definition_document);
    workingDocumentRef.current = document;
    setWorkingDocument(document);
    setSaveState("UNCHANGED");
    setValidation(null);
    setSandbox(null);
    setMessage("");
    return true;
  };
  const guardedNavigate = (to: string) => {
    if (!hasUnsavedChanges) navigate(to);
    else setDiscardRequest(to);
  };
  const requestDiscard = () => {
    if (!hasUnsavedChanges) return;
    setDiscardRequest("");
  };
  const confirmDiscardRequest = () => {
    const target = discardRequest;
    setDiscardRequest(null);
    if (!discardWorkingCopy()) return;
    if (target) navigate(target);
  };
  const reloadServerDraft = async () => {
    const result = await draftQuery.refetch();
    if (result.data) hydrateDraft(result.data);
  };
  const changeName = (name: string) => {
    if (!selected) return;
    if (typeof selected.value.name === "string") editDocument(updateObjectName(local.definition_document, section, selected.key, name, selected.kind));
  };
  const createObject = (kind: EntityKind) => {
    const added = addObject(local.definition_document, kind);
    editDocument(added.document);
    navigate(editorLocatorHref({ owner: "entity", section, kind, objectKey: added.key, fieldPath: null }, scenarioId));
  };
  const createAndLinkEntity = (kind: EntityKind, parentKind: EntityKind, parentKey: string, field: string) => {
    const document = workingDocumentRef.current;
    if (!document) return;
    const added = addObject(document, kind);
    const parent = objectByKindAndKey(added.document, parentKind, parentKey);
    if (!parent) {
      setMessage("当前工作副本中的关联对象已变化，请重试。");
      return;
    }
    const next = replaceObject(added.document, sectionForKind(parentKind), parentKey, { ...parent.value, [field]: added.key }, parentKind);
    editDocument(next);
    navigate(editorLocatorHref({ owner: "entity", section: sectionForKind(parentKind), kind: parentKind, objectKey: parentKey, fieldPath: field }, scenarioId));
  };
  const createGuidedDependency = (item: CompletenessItem) => {
    const document = workingDocumentRef.current;
    if (!document) return;
    const actorRoleMatch = item.key.match(/^actor:([^:]+):role$/);
    if (actorRoleMatch) { createAndLinkEntity("role", "actor", actorRoleMatch[1], "role_key"); return; }
    const actionInteractionMatch = item.key.match(/^action:([^:]+):interaction$/);
    if (actionInteractionMatch) { createAndLinkEntity("interaction", "action", actionInteractionMatch[1], "required_interaction_key"); return; }
    const resolveRuleMatch = item.key.match(/^action:([^:]+):resolve-rule$/);
    if (resolveRuleMatch) {
      const actionKey = resolveRuleMatch[1];
      const added = addObject(document, "rule");
      const action = objectByKindAndKey(added.document, "action", actionKey);
      const rule = objectByKindAndKey(added.document, "rule", added.key);
      if (!action || !rule) { setMessage("当前工作副本中的行动已变化，请重新计算配置状态。"); return; }
      const outcomes = Array.isArray(action.value.expected_outcomes) ? action.value.expected_outcomes : [];
      const firstOutcome = outcomes.find((outcome) => outcome && typeof outcome === "object" && !Array.isArray(outcome) && typeof (outcome as JsonObject).code === "string") as JsonObject | undefined;
      const outcomeCode = typeof firstOutcome?.code === "string" && firstOutcome.code ? firstOutcome.code : "Success";
      const next = replaceObject(added.document, "rules", added.key, { ...rule.value, phase: "RESOLVE", trigger: "ACTION", action_key: actionKey, effects: [{ kind: "EMIT_OUTCOME", outcome_code: outcomeCode, retryable: false }] }, "rule");
      editDocument(next);
      navigate(editorLocatorHref({ owner: "entity", section: "actions", kind: "action", objectKey: actionKey, fieldPath: "rules" }, scenarioId));
      return;
    }
    const sourceHintMatch = item.key.match(/^resource:([^:]+):public-source$/);
    if (sourceHintMatch) {
      const resourceKey = sourceHintMatch[1];
      const knowledge = document.public_knowledge && typeof document.public_knowledge === "object" && !Array.isArray(document.public_knowledge) ? document.public_knowledge as JsonObject : {};
      const hints = Array.isArray(knowledge.resource_source_hints) ? knowledge.resource_source_hints as JsonObject[] : [];
      if (hints.some((hint) => hint.resource_key === resourceKey)) return;
      const next = { ...document, public_knowledge: { ...knowledge, resource_source_hints: [...hints, { resource_key: resourceKey, primary_region_key: null, candidate_region_keys: [] }] } };
      editDocument(next);
      navigate(`/scenarios/${scenarioId}/edit/public-knowledge?owner=collection&collection=resource_source_hints&item=${encodeURIComponent(JSON.stringify([resourceKey]))}`);
      return;
    }
    const poolMatch = item.key.match(/^resource:([^:]+):pool$/);
    if (poolMatch) {
      const resourceKey = poolMatch[1];
      const initialization = document.initialization && typeof document.initialization === "object" && !Array.isArray(document.initialization) ? document.initialization as JsonObject : {};
      const pools = Array.isArray(initialization.resource_pools) ? initialization.resource_pools as JsonObject[] : [];
      if (pools.some((pool) => pool.resource_key === resourceKey)) return;
      const existingKeys = new Set(pools.flatMap((pool) => typeof pool.pool_key === "string" ? [pool.pool_key] : []));
      const baseKey = `pool_${resourceKey}`;
      let poolKey = baseKey;
      for (let index = 2; existingKeys.has(poolKey); index += 1) poolKey = `${baseKey}_${index}`;
      const next = { ...document, initialization: { ...initialization, resource_pools: [...pools, { pool_key: poolKey, resource_key: resourceKey, region_key: null, facility_key: null, quantity: 0, reserved_value: 0, visibility: "VISIBLE", availability: "AVAILABLE", survey_discoverable: false }] } };
      editDocument(next);
      navigate(`/scenarios/${scenarioId}/edit/initialization?domain=resources&group=resource-pools&item=${encodeURIComponent(`pool:${poolKey}`)}`);
      return;
    }
    const recoveryMatch = item.key.match(/^recovery-hint:(.+)$/);
    if (recoveryMatch) {
      const failureCode = recoveryMatch[1];
      const planning = document.planning && typeof document.planning === "object" && !Array.isArray(document.planning) ? document.planning as JsonObject : {};
      const hints = Array.isArray(planning.recovery_hints) ? planning.recovery_hints as JsonObject[] : [];
      if (hints.some((hint) => hint.failure_code === failureCode)) return;
      const next = { ...document, planning: { ...planning, recovery_hints: [...hints, { failure_code: failureCode, hint: "请填写失败恢复策略。" }] } };
      editDocument(next);
      navigate(`/scenarios/${scenarioId}/edit/planning-recovery?owner=collection&collection=recovery_hints&item=${encodeURIComponent(JSON.stringify([failureCode]))}`);
      return;
    }
    setMessage("该依赖不能安全推断业务值，请使用精确前往链接完成配置。");
  };
  const updateSelectedEntity = (value: JsonObject) => {
    if (!selected) return;
    const nextIdentity = objectIdentity(selected.kind, value);
    if (!nextIdentity) { setMessage("请填写完整的对象身份字段。"); return; }
    if (objects.some((item) => item !== selected && item.kind === selected.kind && item.key === nextIdentity)) {
      setMessage("同类型对象已使用该身份。");
      return;
    }
    setMessage("");
    editDocument(replaceObject(local.definition_document, section, selected.key, value, selected.kind));
    if (nextIdentity !== selected.key) {
      navigate(editorLocatorHref({ owner: "entity", section, kind: selected.kind, objectKey: nextIdentity, fieldPath: null }, scenarioId), { replace: true });
    }
  };
  const updateCollectionItem = (selection: RootCollectionSelection, item: JsonObject) => {
    const root = structuredClone(sectionValue) as JsonObject;
    const identityChanged = rootCollectionIdentity(selection.collection, item) !== selection.identity;
    if (identityChanged) {
      setMessage("集合项稳定身份是只读的；请删除后按正确身份重新创建，避免产生悬空引用。");
      return;
    }
    const result = replaceRootCollectionItem(root, selection, item);
    if (!result.ok) { setMessage(result.reason); return; }
    setMessage("");
    editDocument(updateSectionRoot(local.definition_document, section, result.root));
    setCollectionSelection(result.selection, true);
  };
  const moveCollectionItem = (selection: RootCollectionSelection, direction: "up" | "down") => {
    if (!(selection.collection === "resource_source_hints" || selection.collection === "recovery_hints")) {
      setMessage("当前集合按身份展示，不能通过排序改变其语义。");
      return;
    }
    const root = structuredClone(sectionValue) as JsonObject;
    const result = moveRootCollectionItem(root, selection, direction);
    if (!result.ok) { setMessage(result.reason); return; }
    setMessage("");
    editDocument(updateSectionRoot(local.definition_document, section, result.root));
    setCollectionSelection(result.selection, true);
  };
  const preflightRootCollectionDelete = async (selection: RootCollectionSelection, subject: string) => {
    const document = workingDocumentRef.current;
    const draft = serverDraftRef.current;
    if (!document || !draft) return;
    const snapshot = cloneWorkingDocument(document);
    let operationIdentity = selection.identity;
    try {
      const parsed = JSON.parse(selection.identity) as unknown;
      if (Array.isArray(parsed)) operationIdentity = parsed.map((part) => part == null ? "" : String(part)).join(":");
    } catch {
      // Keep the canonical string when a legacy selection was already raw.
    }
    setAuthoringDialog(null);
    try {
      const transformed = await api.transformWorkingCopy(scenarioId, draft.revision, snapshot, {
        kind: "DELETE_ROOT_COLLECTION_ITEM",
        object_kind: section === "planning-recovery" ? "planning" : section === "public-knowledge" ? "public_knowledge" : "initialization",
        collection: selection.collection,
        identity: operationIdentity,
      });
      if (!workingDocumentsEqual(workingDocumentRef.current, snapshot)) {
        setMessage("工作副本在删除预检期间发生了新修改，请重试该操作。");
        return;
      }
      const transformedDocument = cloneWorkingDocument(transformed.definition_document);
      const currentIndex = collectionItems.find((item) => item.collection === selection.collection && item.identity === selection.identity)?.index ?? 0;
      const remainingItems = section === "public-knowledge"
        ? rootCollectionItems(section, sectionRoot(transformedDocument, section), transformedDocument).filter((item) => item.collection === selection.collection)
        : [];
      const fallback = remainingItems.length > 0 ? remainingItems[Math.min(currentIndex, remainingItems.length - 1)] : null;
      setAuthoringDialog({ kind: "confirm", title: `删除「${subject}」？`, subject, detail: "删除后将从当前工作副本中移除。", document: snapshot, transformedDocument, onApplied: () => setCollectionSelection(fallback ? { owner: "collection", collection: fallback.collection, identity: fallback.identity } : null) });
    } catch (error) {
      if (!workingDocumentsEqual(workingDocumentRef.current, snapshot)) {
        setMessage("工作副本在删除预检期间发生了新修改，请重试该操作。");
        return;
      }
      if (error instanceof ApiError && error.code === "SCENARIO_ROOT_COLLECTION_ITEM_REFERENCED") {
        setAuthoringDialog({ kind: "blocked", title: `无法删除「${subject}」`, subject, references: authoringReferencesFromError(error, snapshot, scenarioId) });
        return;
      }
      const preflightError = factDeletePreflightError(error);
      setAuthoringDialog({ kind: "error", subject, ...preflightError });
    }
  };
  const removeCollectionItem = (selection: RootCollectionSelection) => {
    const selectedItem = collectionItems.find((item) => item.collection === selection.collection && item.identity === selection.identity)?.value;
    const selectedPresentation = collectionItems.find((item) => item.collection === selection.collection && item.identity === selection.identity);
    const subject = section === "public-knowledge" && selectedPresentation
      ? selectedPresentation.title
      : selectedItem ? rootCollectionIdentityLabel(selection.collection, selectedItem) : rootCollectionLabel(selection.collection);
    void preflightRootCollectionDelete(selection, subject);
  };
  const createCollectionItem = (collectionKey: RootCollectionKey) => {
    const root = structuredClone(sectionValue) as JsonObject;
    const result = appendRootCollectionItem(root, collectionKey);
    if (!result.ok || !result.selection) { setMessage(result.ok ? "无法选择新集合项。" : result.reason); return; }
    setMessage("");
    editDocument(updateSectionRoot(local.definition_document, section, result.root));
    setCollectionSelection(result.selection);
  };
  const createResourceSourceHint = (item: { resource_key: string; primary_region_key: string | null; candidate_region_keys: string[] }) => {
    const document = workingDocumentRef.current;
    if (!document) return;
    const resources = sectionObjects(document, "resources").filter((resource) => resource.kind === "resource");
    if (!resources.some((resource) => resource.key === item.resource_key)) {
      setMessage("所选资源已不存在，请重新选择。");
      return;
    }
    const knowledge = document.public_knowledge && typeof document.public_knowledge === "object" && !Array.isArray(document.public_knowledge) ? document.public_knowledge as JsonObject : {};
    const hints = Array.isArray(knowledge.resource_source_hints) ? knowledge.resource_source_hints.filter((hint): hint is JsonObject => Boolean(hint) && typeof hint === "object" && !Array.isArray(hint)) : [];
    if (hints.some((hint) => hint.resource_key === item.resource_key)) {
      setMessage("该资源已经有来源提示。");
      return;
    }
    const next = { ...document, public_knowledge: { ...knowledge, resource_source_hints: [...hints, structuredClone(item)] } };
    setMessage("");
    setResourceSourceCreateOpen(false);
    editDocument(next);
    setCollectionSelection({ owner: "collection", collection: "resource_source_hints", identity: JSON.stringify([item.resource_key]) });
  };
  const createPlanningInstruction = () => {
    const root = structuredClone(sectionValue) as JsonObject;
    const instructions = Array.isArray(root.instructions) ? root.instructions.filter((item): item is string => typeof item === "string") : [];
    const index = instructions.length;
    editDocument(updateSectionRoot(local.definition_document, section, { ...root, instructions: [...instructions, ""] }));
    setCollectionSelection({ owner: "instruction", index });
  };
  const updatePlanningInstruction = (index: number, content: string) => {
    const root = structuredClone(sectionValue) as JsonObject;
    const instructions = Array.isArray(root.instructions) ? root.instructions.filter((item): item is string => typeof item === "string") : [];
    editDocument(updateSectionRoot(local.definition_document, section, { ...root, instructions: instructions.map((item, oldIndex) => oldIndex === index ? content : item) }));
  };
  const movePlanningInstruction = (index: number, direction: "up" | "down") => {
    const target = direction === "up" ? index - 1 : index + 1;
    if (target < 0 || target >= planningInstructions.length) return;
    const instructions = [...planningInstructions];
    [instructions[index], instructions[target]] = [instructions[target], instructions[index]];
    editDocument(updateSectionRoot(local.definition_document, section, { ...(sectionValue as JsonObject), instructions }));
    setCollectionSelection({ owner: "instruction", index: target }, true);
  };
  const removePlanningInstruction = (index: number) => {
    const snapshot = cloneWorkingDocument(local.definition_document);
    const instructions = planningInstructions.filter((_, oldIndex) => oldIndex !== index);
    const transformedDocument = updateSectionRoot(snapshot, section, { ...(sectionValue as JsonObject), instructions });
    setAuthoringDialog({ kind: "confirm", title: `删除「规划指引 ${index + 1}」？`, subject: `规划指引 ${index + 1}`, detail: "只会删除当前规划指引。", document: snapshot, transformedDocument, onApplied: () => setCollectionSelection(instructions.length > 0 ? { owner: "instruction", index: Math.min(index, instructions.length - 1) } : null) });
  };
  const deleteInitializationResourcePool = (poolKey: string) => {
    const initialization = local.definition_document.initialization;
    if (!initialization || typeof initialization !== "object" || Array.isArray(initialization)) return;
    const pools = Array.isArray((initialization as JsonObject).resource_pools) ? (initialization as JsonObject).resource_pools as JsonObject[] : [];
    const pool = pools.find((item) => item.pool_key === poolKey);
    if (!pool) return;
    void preflightRootCollectionDelete({ owner: "collection", collection: "resource_pools", identity: JSON.stringify([poolKey]) }, objectDisplayValue(pool, poolKey));
  };
  const deleteRootCollectionItemFromInitialization = (collection: RootCollectionKey, identity: string, subject: string) => {
    void preflightRootCollectionDelete({ owner: "collection", collection, identity: JSON.stringify([identity]) }, subject);
  };
  const addRegionResourceKnowledge = () => {
    const initialization = local.definition_document.initialization && typeof local.definition_document.initialization === "object" && !Array.isArray(local.definition_document.initialization)
      ? local.definition_document.initialization as JsonObject
      : {};
    const world = local.definition_document.world && typeof local.definition_document.world === "object" && !Array.isArray(local.definition_document.world)
      ? local.definition_document.world as JsonObject
      : {};
    const metadata = local.definition_document.metadata && typeof local.definition_document.metadata === "object" && !Array.isArray(local.definition_document.metadata)
      ? local.definition_document.metadata as JsonObject
      : {};
    const locality = metadata.locality && typeof metadata.locality === "object" && !Array.isArray(metadata.locality) ? metadata.locality as JsonObject : {};
    const nodes = Array.isArray(world.nodes) ? world.nodes as JsonObject[] : [];
    const regionType = typeof locality.region_node_type_key === "string" ? locality.region_node_type_key : null;
    const region = nodes.find((node) => !regionType || node.node_type_key === regionType) ?? nodes[0];
    const regionKey = typeof region?.key === "string" ? region.key : "";
    if (!regionKey) {
      setMessage("请先创建至少一个区域节点，再添加区域资源知识。");
      return;
    }
    const states = Array.isArray(initialization.region_resource_knowledge) ? initialization.region_resource_knowledge as JsonObject[] : [];
    if (states.some((state) => state.region_key === regionKey)) {
      setMessage("该区域已经存在区域资源知识。");
      setSearchParams({ domain: "resources", group: "region-resource-knowledge", item: `region-knowledge:${regionKey}` }, { replace: true });
      return;
    }
    const nextDocument = updateSectionRoot(local.definition_document, "initialization", { ...initialization, region_resource_knowledge: [...states, { region_key: regionKey, resource_inventory_visibility: "VISIBLE", resource_survey_completed: false }] });
    editDocument(nextDocument);
    setSearchParams({ domain: "resources", group: "region-resource-knowledge", item: `region-knowledge:${regionKey}` }, { replace: true });
  };

  const requestRename = () => {
    if (!selected) return;
    setRenameRequest({ subject: objectDisplayValue(selected.value, selected.key), currentKey: selected.key });
  };
  const rename = async (newKey: string) => {
    if (!selected || !newKey || newKey === selected.key) {
      setRenameRequest(null);
      return;
    }
    const document = workingDocumentRef.current;
    const draft = serverDraftRef.current;
    if (!document || !draft) return;
    setRenameRequest(null);
    try {
      const transformed = await api.transformWorkingCopy(scenarioId, draft.revision, document, { kind: "RENAME_KEY", object_kind: selected.kind, old_key: selected.key, new_key: newKey });
      if (!workingDocumentsEqual(workingDocumentRef.current, document)) {
        setMessage("工作副本在重命名期间发生了新修改，请重试该操作。");
        return;
      }
      editDocument(transformed.definition_document);
      navigate(editorLocatorHref({ owner: "entity", section, kind: selected.kind, objectKey: newKey, fieldPath: null }, scenarioId), { replace: true });
    } catch (error) { setSaveState(error instanceof ApiError && error.code === "SCENARIO_DRAFT_CONFLICT" ? "CONFLICT" : "ERROR"); setMessage(errorText(error, "稳定键重命名失败。")); }
  };

  const remove = async () => {
    if (!selected) return;
    const document = workingDocumentRef.current;
    const draft = serverDraftRef.current;
    if (!document || !draft) return;
    const snapshot = cloneWorkingDocument(document);
    const subject = objectDisplayValue(selected.value, selected.key);
    const selectedKind = selected.kind;
    const selectedKey = selected.key;
    try {
      const transformed = await api.transformWorkingCopy(scenarioId, draft.revision, snapshot, { kind: "DELETE_OBJECT", object_kind: selectedKind, object_key: selectedKey });
      if (!workingDocumentsEqual(workingDocumentRef.current, snapshot)) {
        setMessage("工作副本在删除期间发生了新修改，请重试该操作。");
        return;
      }
      setAuthoringDialog({ kind: "confirm", title: `删除「${subject}」？`, subject, detail: "删除后将从当前工作副本中移除。", document: snapshot, transformedDocument: cloneWorkingDocument(transformed.definition_document), onApplied: () => navigate(`/scenarios/${scenarioId}/edit/${section}`) });
    } catch (error) {
      if (!workingDocumentsEqual(workingDocumentRef.current, snapshot)) {
        setMessage("工作副本在删除预检期间发生了新修改，请重试该操作。");
        return;
      }
      if (error instanceof ApiError && (error.code === "SCENARIO_OBJECT_REFERENCED" || error.code === "SCENARIO_NESTED_OBJECT_REFERENCED")) {
        setAuthoringDialog({ kind: "blocked", title: `无法删除「${subject}」`, subject, references: authoringReferencesFromError(error, snapshot, scenarioId) });
        return;
      }
      const preflightError = factDeletePreflightError(error);
      setAuthoringDialog({ kind: "error", subject, ...preflightError });
    }
  };
  const removeNested = async (request: { parentKind: string; parentKey: string; collection: string; nestedKey: string; subject: string }) => {
    const document = workingDocumentRef.current;
    const draft = serverDraftRef.current;
    if (!document || !draft) return;
    const snapshot = cloneWorkingDocument(document);
    try {
      const transformed = await api.transformWorkingCopy(scenarioId, draft.revision, snapshot, {
        kind: "DELETE_NESTED",
        object_kind: request.parentKind,
        parent_kind: request.parentKind,
        parent_key: request.parentKey,
        collection: request.collection,
        nested_key: request.nestedKey,
      });
      if (!workingDocumentsEqual(workingDocumentRef.current, snapshot)) {
        setMessage("工作副本在删除预检期间发生了新修改，请重试该操作。");
        return;
      }
      setAuthoringDialog({ kind: "confirm", title: `删除「${request.subject}」？`, subject: request.subject, detail: "删除后将从当前工作副本中移除。", document: snapshot, transformedDocument: cloneWorkingDocument(transformed.definition_document) });
    } catch (error) {
      if (!workingDocumentsEqual(workingDocumentRef.current, snapshot)) {
        setMessage("工作副本在删除预检期间发生了新修改，请重试该操作。");
        return;
      }
      if (error instanceof ApiError && error.code === "SCENARIO_NESTED_OBJECT_REFERENCED") {
        setAuthoringDialog({ kind: "blocked", title: `无法删除「${request.subject}」`, subject: request.subject, references: authoringReferencesFromError(error, snapshot, scenarioId) });
        return;
      }
      setAuthoringDialog({ kind: "error", subject: request.subject, ...factDeletePreflightError(error) });
    }
  };

  const deleteFact = async (nodeKey: string, factKey: string) => {
    const document = workingDocumentRef.current;
    const draft = serverDraftRef.current;
    if (!document || !draft) return;
    const factName = authoredFactName(document, nodeKey, factKey);
    const snapshot = cloneWorkingDocument(document);
    setFactDeleteDialog(null);
    try {
      const transformed = await api.transformWorkingCopy(scenarioId, draft.revision, snapshot, { kind: "DELETE_FACT", object_kind: "node", node_key: nodeKey, fact_key: factKey });
      if (!workingDocumentsEqual(workingDocumentRef.current, snapshot)) {
        setMessage("工作副本在删除预检期间发生了新修改，请重试该操作。");
        return;
      }
      setMessage("");
      setFactDeleteDialog({ kind: "confirm", factName, nodeKey, factKey, document: snapshot, transformedDocument: cloneWorkingDocument(transformed.definition_document) });
    } catch (error) {
      if (error instanceof ApiError && error.code === "SCENARIO_FACT_REFERENCED") {
        if (!workingDocumentsEqual(workingDocumentRef.current, snapshot)) {
          setMessage("工作副本在删除预检期间发生了新修改，请重试该操作。");
          return;
        }
        const details = error.details && typeof error.details === "object" && !Array.isArray(error.details) ? error.details as { references?: unknown } : {};
        const references = Array.isArray(details.references) ? details.references : [];
        const referenceItems = references.flatMap((item) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) return [];
          const source = (item as { source?: unknown }).source;
          if (!source || typeof source !== "object" || Array.isArray(source)) return [];
          const locator = source as { object_kind?: unknown; object_key?: unknown; field_path?: unknown };
          const authoredLocator = {
            object_kind: typeof locator.object_kind === "string" ? locator.object_kind : "unknown",
            object_key: typeof locator.object_key === "string" ? locator.object_key : null,
            field_path: typeof locator.field_path === "string" ? locator.field_path : null,
          };
          return [{ ...authoredReferenceDetails(snapshot, authoredLocator), href: authoredReferenceHref(scenarioId, authoredLocator) }];
        });
        setMessage("");
        setFactDeleteDialog({ kind: "blocked", factName, nodeKey, factKey, references: referenceItems });
        return;
      }
      if (error instanceof ApiError && error.code === "SCENARIO_DRAFT_CONFLICT") setSaveState("CONFLICT");
      if (!workingDocumentsEqual(workingDocumentRef.current, snapshot)) {
        setMessage("工作副本在删除预检期间发生了新修改，请重试该操作。");
        return;
      }
      const preflightError = factDeletePreflightError(error);
      setMessage("");
      setFactDeleteDialog({ kind: "error", factName, nodeKey, factKey, ...preflightError });
    }
  };
  const confirmFactDelete = () => {
    if (!factDeleteDialog || factDeleteDialog.kind !== "confirm") return;
    if (!workingDocumentsEqual(workingDocumentRef.current, factDeleteDialog.document)) {
      setFactDeleteDialog(null);
      setMessage("工作副本在确认删除前发生了新修改，请重试该操作。");
      return;
    }
    setFactDeleteDialog(null);
    editDocument(factDeleteDialog.transformedDocument as JsonObject);
  };
  const confirmAuthoringOperation = () => {
    if (!authoringDialog || authoringDialog.kind !== "confirm") return;
    if (!workingDocumentsEqual(workingDocumentRef.current, authoringDialog.document)) {
      setAuthoringDialog(null);
      setMessage("工作副本在确认删除前发生了新修改，请重试该操作。");
      return;
    }
    const onApplied = authoringDialog.onApplied;
    setAuthoringDialog(null);
    editDocument(authoringDialog.transformedDocument as JsonObject);
    onApplied?.();
  };
  const validate = async () => {
    const draft = serverDraftRef.current;
    if (!draft || saveState !== "UNCHANGED") { setMessage("请先保存当前工作副本后再验证。"); return; }
    try { setValidation(await api.validateDraft(scenarioId, draft.revision)); setMessage(""); }
    catch (error) { setMessage(errorText(error, "草稿验证失败。")); }
  };
  const publish = async () => {
    const draft = serverDraftRef.current;
    if (!draft || saveState !== "UNCHANGED" || !validation?.publish_ready || validation.revision !== draft.revision) { setMessage("请保存并重新验证当前工作副本后再发布。"); return; }
    try { await api.publishDraft(scenarioId, draft.revision, validation.content_hash); setMessage("已发布新的不可变场景版本。"); void queryClient.invalidateQueries({ queryKey: ["scenario", scenarioId] }); }
    catch (error) { setMessage(errorText(error, "发布失败。")); }
  };
  const testDraft = async () => {
    const draft = serverDraftRef.current;
    if (!draft || saveState !== "UNCHANGED") { setMessage("请先保存当前工作副本后再测试。"); return; }
    try { setSandbox(await api.testDraft(scenarioId, draft.revision, sandboxGoal.trim() || null)); setMessage(""); }
    catch (error) { setMessage(errorText(error, "草稿沙盒启动失败。")); }
  };
  const focusIssue = (issue: ValidationResult["issues"][number]) => {
    const locator = issue.locator;
    if (!locator) {
      setMessage(`无法自动定位此诊断，请按原始路径查看：${issue.path}`);
      return;
    }
    if (locator.object_kind === "initialization") {
      const match = locator.field_path?.match(/^resource_pools\.(\d+)/);
      const poolIndex = match ? Number(match[1]) : -1;
      const initialization = local.definition_document.initialization;
      const pools = initialization && typeof initialization === "object" && !Array.isArray(initialization) && Array.isArray((initialization as JsonObject).resource_pools) ? (initialization as JsonObject).resource_pools as JsonObject[] : [];
      const poolKey = typeof pools[poolIndex]?.pool_key === "string" ? pools[poolIndex].pool_key as string : null;
      const item = poolKey ? initializationPreviewQuery.data?.projection.domains.flatMap((domain) => domain.groups).flatMap((group) => group.items).find((candidate) => candidate.id.startsWith(`pool:${poolKey}:`)) : null;
      if (item) {
        navigate(`/scenarios/${scenarioId}/edit/initialization?domain=resources&group=resource-pools&item=${encodeURIComponent(item.id)}`);
        return;
      }
    }
    const target = editorLocatorFromValidation(locator, local.definition_document);
    if (!target) {
      setMessage(`无法精确定位此诊断，请按原始路径查看：${issue.path}`);
      return;
    }
    navigate(editorLocatorHref(target, scenarioId));
  };

  const openTopologyEditor = (nodeKey: string) => {
    navigate(`/scenarios/${scenarioId}/edit/world/${encodeURIComponent(nodeKey)}`);
  };
  const showWorldTopology = structure.workspace.renderer === "topology";
  const usesRootCollections = structure.master.source === "root-collections";
  const masterCount = usesRootCollections
    ? section === "planning-instructions" ? planningInstructions.length : section === "planning-recovery" ? collectionItems.length : collectionItems.length + singletonOwners.length
    : structure.master.source === "topology"
      ? nodeSemanticView(local.definition_document, "all").length
      : objects.length;
  const workspaceTitle = showWorldTopology
    ? "世界拓扑"
    : structure.workspace.renderer === "initialization"
      ? "初始化"
    : structure.workspace.renderer === "configuration-check"
      ? "配置检查"
    : selected
      ? objectDisplayValue(selected.value, selected.name)
      : selectedInstruction !== null && collectionSelection?.owner === "instruction"
        ? `规划指引 ${collectionSelection.index + 1}`
      : planningRecoverySelected
        ? String(selectedCollectionItem?.value.failure_code ?? "失败恢复策略")
      : selectedCollectionItem
        ? selectedCollectionItem.title
        : selectedSingletonOwner
          ? selectedSingletonOwner.label
        : sectionLabels[section] ?? section;
  const workspaceSubtitle = showWorldTopology
    ? "浏览范围、实体与关系；编辑请进入对应的世界模型页面"
    : structure.workspace.renderer === "initialization"
      ? "配置当前版本的开局状态"
    : structure.workspace.renderer === "configuration-check"
      ? "按领域检查当前工作副本的完整性，并前往或创建确定的关联配置"
    : selected
      ? `${kindLabels[selected.kind] ?? selected.kind} · 可编辑对象`
      : selectedInstruction !== null
        ? "规划指引 · 可编辑对象"
      : planningRecoverySelected
        ? "失败恢复策略 · 可编辑对象"
      : section === "public-knowledge" && selectedCollectionItem
        ? "资源来源提示 · 可编辑对象"
      : selectedCollectionItem
        ? "编辑当前集合项"
        : selectedSingletonOwner
          ? selectedSingletonOwner.summary
        : structure.mode === "SINGLETON"
          ? "编辑这一份场景配置"
          : structure.mode === "WORKFLOW"
            ? "验证、测试并发布当前草稿"
            : structure.mode === "HYBRID"
              ? "选择配置或集合项后编辑对应内容"
              : "从左侧选择或新建对象";
  const entityGroups = structure.master.source === "entities" && structure.master.grouped
    ? structure.owners.entityKinds.map((kind) => ({
      kind,
      label: kind === "relation" ? "关系实例" : kindLabels[kind] ?? kind,
      items: filteredObjects.filter((item) => item.kind === kind),
      total: objects.filter((item) => item.kind === kind).length,
    }))
    : [];
  const renderObjectItem = (item: (typeof objects)[number]) => (
    <Link
      className={`object-list-item${selected?.kind === item.kind && selected.key === item.key ? " selected" : ""}`}
      key={draftObjectIdentity(item)}
      to={editorLocatorHref({ owner: "entity", section, kind: item.kind, objectKey: item.key, fieldPath: null }, scenarioId)}
      onClick={(event) => {
        if (structure.master.source !== "topology" || item.kind !== "node") return;
        event.preventDefault();
        setTopologyFocusNodeKey(item.key);
        const scopeKey = findScopeForNode(local.definition_document, item.key);
        if (scopeKey) setTopologyContext({ kind: "scope", scopeKey });
        setTopologySelection({ kind: "node", key: item.key });
      }}
    >
      <span>{item.name}</span>
      <code>{kindLabels[item.kind] ?? item.kind} · {item.key}</code>
    </Link>
  );
  const taxonomy = editorSectionTaxonomy[section] ?? { category: "场景编辑器", label: sectionLabels[section] ?? section };
  return <main className="editor-shell">
    <aside className="editor-nav">
      <div className="editor-nav-header"><div className="editor-nav-identity"><p>场景编辑器</p><h2>当前草稿</h2></div></div>
      <nav className="editor-section-nav" aria-label="编辑器导航">{editorTaxonomyGroups.map((group) => <div className="editor-nav-group" key={group.label}><p>{group.label}</p>{group.items.map((item) => <Link className={item === section ? "active" : ""} key={item} to={`/scenarios/${scenarioId}/edit/${item}`}>{sectionLabels[item] ?? item}</Link>)}</div>)}</nav>
    </aside>
    <section className="editor-main">
      <header className="editor-toolbar"><div className="editor-toolbar-context"><div className="editor-breadcrumb" data-testid="editor-taxonomy-heading"><span>{taxonomy.category}</span><span aria-hidden="true">/</span><strong>{taxonomy.label}</strong></div></div><div className="editor-heading-actions"><span className={`save-state ${saveState.toLowerCase()}`}><i aria-hidden="true" />{saveLabels[saveState]}</span>{serverDraft && <VersionStatusBadges draft={serverDraft} published={publishedVersionQuery.data ?? null} saveState={saveState} />}<button type="button" className="editor-button editor-button-primary" disabled={!hasUnsavedChanges || save.isPending} onClick={saveWorkingCopy}>保存</button><button type="button" className="editor-button editor-button-secondary" disabled={!hasUnsavedChanges || save.isPending} onClick={requestDiscard}>放弃修改</button><button type="button" className="editor-button editor-button-danger editor-return-detail" onClick={() => guardedNavigate(`/scenarios/${scenarioId}`)}>返回场景详情</button>{structure.capabilities.inspector && <button type="button" className="editor-button editor-button-ghost" onClick={() => setInspectorOpen((current) => !current)}>{inspectorOpen ? "隐藏检查器" : "显示检查器"}</button>}</div></header>
      {message && <div className="conflict-banner"><p>{message}</p>{saveState === "CONFLICT" && <button type="button" className="editor-button editor-button-secondary" onClick={() => void reloadServerDraft()}>重新加载服务器草稿</button>}</div>}
      {factDeleteDialog && <FactDeleteDialog state={factDeleteDialog} onClose={() => setFactDeleteDialog(null)} onConfirm={confirmFactDelete} />}
      {authoringDialog && <AuthoringOperationDialog state={authoringDialog} onClose={() => setAuthoringDialog(null)} onConfirm={confirmAuthoringOperation} />}
      {resourceSourceCreateOpen && <ResourceSourceHintCreateDialog document={local.definition_document} scenarioId={scenarioId} onCancel={() => setResourceSourceCreateOpen(false)} onCreate={createResourceSourceHint} />}
      {discardRequest !== null && <EditorConfirmDialog title="放弃当前修改？" message="当前工作副本有未保存修改，放弃后将恢复到已保存草稿。" confirmLabel="放弃修改" onCancel={() => setDiscardRequest(null)} onConfirm={confirmDiscardRequest} />}
      {renameRequest && <EditorRenameDialog subject={renameRequest.subject} initialValue={renameRequest.currentKey} onCancel={() => setRenameRequest(null)} onConfirm={(value) => void rename(value)} />}
      <div className={`editor-columns${structure.master.visible ? "" : " master-hidden"}${inspectorOpen ? "" : " inspector-collapsed"}`}>
        {structure.master.visible && <aside className="object-list object-panel">
          <header className="object-panel-header"><div><p className="panel-kicker">{structure.mode === "BROWSER" ? "世界结构" : structure.mode === "HYBRID" ? "配置导航" : "内容导航"}</p><div className="object-panel-title">{sectionLabels[section] ?? section}</div></div><span className="object-count">{masterCount}</span></header>
          {structure.master.source === "topology" && <div className="segmented-control world-filter-tabs" role="tablist" aria-label="世界对象筛选">{(["all", "regions", "facilities", "transports"] as WorldView[]).map((item) => <button type="button" className={worldView === item ? "selected" : ""} aria-pressed={worldView === item} key={item} onClick={() => setWorldView(item)}><span>{worldViewLabels[item]}</span><small>{nodeSemanticView(local.definition_document, item).length}</small></button>)}</div>}
          <div className="object-panel-tools">{structure.master.searchable && <label className="object-search">搜索<input value={objectSearch} placeholder={usesRootCollections ? "名称、代码或标识" : "名称或稳定键"} onChange={(event) => setObjectSearch(event.target.value)} /></label>}{structure.master.source === "entities" && availableKinds.length > 1 && !structure.master.grouped && <label className="object-filter">对象类型<select value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="all">全部类型</option>{availableKinds.map((kind) => <option key={kind} value={kind}>{kindLabels[kind] ?? kind}</option>)}</select></label>}{structure.master.create === "entity" && !structure.master.grouped && <div className="object-list-actions">{(kindsBySection[section] ?? []).map((kind) => <button type="button" className="editor-button editor-button-secondary add-object" key={kind} onClick={() => createObject(kind)}>＋ {kind === "relation_type" ? "新增关系类型" : kind === "relation" ? "新增关系实例" : kindLabels[kind] ?? kind}</button>)}</div>}{section === "public-knowledge" && <div className="object-list-actions"><button type="button" className="editor-button editor-button-secondary add-object" onClick={() => setResourceSourceCreateOpen(true)}>＋ 资源来源提示</button></div>}{section === "planning-instructions" && <div className="object-list-actions"><button type="button" className="editor-button editor-button-secondary add-object" onClick={createPlanningInstruction}>＋ 规划指引</button></div>}{section === "planning-recovery" && <div className="object-list-actions"><button type="button" className="editor-button editor-button-secondary add-object" onClick={() => createCollectionItem("recovery_hints")}>＋ 失败恢复策略</button></div>}</div>
          <div className="object-list-scroll">
            {usesRootCollections ? section === "public-knowledge" ? <>
              {filteredCollectionItems.map((item) => <button type="button" className={`collection-list-item${collectionSelection?.owner === "collection" && collectionSelection.collection === item.collection && collectionSelection.identity === item.identity ? " selected" : ""}`} key={`${item.collection}:${item.identity}`} onClick={() => setCollectionSelection({ owner: "collection", collection: item.collection, identity: item.identity })}><strong>{item.title}</strong><span>{item.summary}</span><code>{item.identityLabel}</code></button>)}
              {collectionItems.length === 0 && <p className="muted collection-list-empty">暂无资源来源提示</p>}
            </> : section === "planning-instructions" ? <>
              {planningInstructions.map((instruction, index) => ({ instruction, index })).filter(({ instruction }) => !objectSearch.trim() || instruction.toLocaleLowerCase().includes(objectSearch.trim().toLocaleLowerCase())).map(({ instruction, index }) => <button type="button" className={`collection-list-item${collectionSelection?.owner === "instruction" && collectionSelection.index === index ? " selected" : ""}`} key={`instruction-${index}`} onClick={() => setCollectionSelection({ owner: "instruction", index })}><strong>规划指引 {index + 1}</strong><span>{instruction || "尚未填写内容"}</span><code>planning.instructions.{index}</code></button>)}
              {planningInstructions.length === 0 && <p className="muted collection-list-empty">暂无规划指引</p>}
            </> : section === "planning-recovery" ? <>
              {filteredCollectionItems.map((item) => <button type="button" className={`collection-list-item${collectionSelection?.owner === "collection" && collectionSelection.collection === item.collection && collectionSelection.identity === item.identity ? " selected" : ""}`} key={`${item.collection}:${item.identity}`} onClick={() => setCollectionSelection({ owner: "collection", collection: item.collection, identity: item.identity })}><strong>{item.title}</strong><span>{item.summary}</span><code>{item.identityLabel}</code></button>)}
              {collectionItems.length === 0 && <p className="muted collection-list-empty">暂无失败恢复策略</p>}
            </> : <div className="collection-list-groups">
              {singletonOwners.length > 0 && <section className="collection-list-group">
              <header><div><h4>初始化</h4><span>{singletonOwners.length} 项</span></div></header>
                {visibleSingletonOwners.map((owner) => <button type="button" key={owner.key} className={`collection-list-item${(collectionSelection?.owner === "singleton" && collectionSelection.key === owner.key) || (owner.key === "planning-recovery" && collectionSelection?.owner === "collection" && collectionSelection.collection === "recovery_hints") ? " selected" : ""}`} onClick={() => setCollectionSelection({ owner: "singleton", key: owner.key })}><strong>{owner.label}</strong><span>{owner.summary}</span><code>{owner.key}</code></button>)}
              </section>}
              {collectionDefinitions.map((definition) => <section className="collection-list-group" key={definition.key}>
                <header><div><h4>{definition.label}</h4><span>{collectionItems.filter((item) => item.collection === definition.key).length} 项</span></div><button type="button" className="editor-button editor-button-secondary" onClick={() => createCollectionItem(definition.key)}>＋ 新增{definition.singularLabel}</button></header>
                {filteredCollectionItems.filter((item) => item.collection === definition.key).map((item) => <button type="button" className={`collection-list-item${collectionSelection?.owner === "collection" && collectionSelection.collection === item.collection && collectionSelection.identity === item.identity ? " selected" : ""}`} key={`${item.collection}:${item.identity}`} onClick={() => setCollectionSelection({ owner: "collection", collection: item.collection, identity: item.identity })}><strong>{item.title}</strong><span>{item.summary}</span><code>{item.identityLabel}</code></button>)}
                {collectionItems.filter((item) => item.collection === definition.key).length === 0 && <p className="muted collection-list-empty">暂无项目</p>}
              </section>)}
            </div> : entityGroups.length > 0 ? <div className="collection-list-groups">
              {entityGroups.map((group) => <section className="collection-list-group" key={group.kind}>
                <header><div><h4>{group.label}</h4><span>{group.total} 项</span></div><button type="button" className="editor-button editor-button-secondary" onClick={() => createObject(group.kind)}>＋ 新增{group.label}</button></header>
                {group.items.map(renderObjectItem)}
                {group.total === 0 && <p className="muted collection-list-empty">暂无{group.label}</p>}
              </section>)}
            </div> : <>
              {objects.length === 0 && <p className="muted object-empty">暂无可编辑项目。</p>}
              {objects.length > 0 && filteredObjects.length === 0 && <p className="muted object-empty">没有匹配的对象。</p>}
              {filteredObjects.map(renderObjectItem)}
            </>}
          </div>
        </aside>}
         <section className={`canvas editor-canvas${showWorldTopology ? " canvas-topology" : ""}${structure.workspace.renderer === "initialization" ? " canvas-initialization" : ""}`}><header className="canvas-header"><div><p className="panel-kicker">{showWorldTopology ? "世界浏览" : structure.workspace.renderer === "initialization" ? "开局状态" : structure.workspace.renderer === "configuration-check" ? "检查与发布" : structure.mode === "WORKFLOW" ? "验证与发布" : "编辑区"}</p><h3>{workspaceTitle}</h3><p className="canvas-subtitle">{workspaceSubtitle}</p></div>{structure.workspace.renderer === "entity" && selected && <button type="button" className="editor-button editor-button-danger" onClick={() => void remove()}>删除{kindLabels[selected.kind] ?? "对象"}</button>}{section === "planning-instructions" && collectionSelection?.owner === "instruction" && <div className="button-row planning-header-actions"><button type="button" className="small" disabled={collectionSelection.index === 0} onClick={() => movePlanningInstruction(collectionSelection.index, "up")}>↑</button><button type="button" className="small" disabled={collectionSelection.index >= planningInstructions.length - 1} onClick={() => movePlanningInstruction(collectionSelection.index, "down")}>↓</button><button type="button" className="small danger" onClick={() => removePlanningInstruction(collectionSelection.index)}>删除</button></div>}{section === "planning-recovery" && collectionSelection?.owner === "collection" && selectedCollectionItem && <div className="button-row planning-header-actions"><button type="button" className="small" disabled={selectedCollectionItem.index === 0} onClick={() => moveCollectionItem(collectionSelection, "up")}>↑</button><button type="button" className="small" disabled={selectedCollectionItem.index >= collectionItems.length - 1} onClick={() => moveCollectionItem(collectionSelection, "down")}>↓</button><button type="button" className="small danger" onClick={() => removeCollectionItem(collectionSelection)}>删除</button></div>}{section === "public-knowledge" && collectionSelection?.owner === "collection" && selectedCollectionItem && <div className="button-row planning-header-actions"><button type="button" className="small" disabled={selectedCollectionItem.index === 0} onClick={() => moveCollectionItem(collectionSelection, "up")}>↑</button><button type="button" className="small" disabled={selectedCollectionItem.index >= collectionItems.length - 1} onClick={() => moveCollectionItem(collectionSelection, "down")}>↓</button><button type="button" className="small danger" onClick={() => removeCollectionItem(collectionSelection)}>删除</button></div>}</header><div className={`canvas-body${showWorldTopology ? " canvas-body-topology" : ""}${structure.workspace.renderer === "initialization" ? " canvas-body-initialization" : ""}`}>
          {section === "overview" && completenessQuery.data && <CompletenessSummary result={completenessQuery.data} scenarioId={scenarioId} />}
          {showWorldTopology && <WorldGraph document={local.definition_document} context={topologyContext} selection={topologySelection} focusNodeKey={topologyFocusNodeKey} onContextChange={setTopologyContext} onSelectionChange={setTopologySelection} onOpenEditor={openTopologyEditor} onFocusNodeConsumed={() => setTopologyFocusNodeKey(null)} />}
          {structure.workspace.renderer === "initialization" && <InitializationWorkspace document={local.definition_document} preview={initializationPreviewQuery.data ?? null} loading={initializationPreviewQuery.isPending} error={initializationPreviewQuery.error ? errorText(initializationPreviewQuery.error) : null} scenarioId={scenarioId} onChange={editDocument} onDeleteResourcePool={deleteInitializationResourcePool} onDeleteRootCollectionItem={deleteRootCollectionItemFromInitialization} onAddRegionResourceKnowledge={addRegionResourceKnowledge} />}
          {structure.workspace.renderer === "configuration-check" && completenessQuery.isPending && <p className="muted">正在检查当前工作副本…</p>}
          {structure.workspace.renderer === "configuration-check" && completenessQuery.error && <div className="conflict-banner"><p>暂时无法完成配置检查：{errorText(completenessQuery.error)}</p></div>}
          {structure.workspace.renderer === "configuration-check" && completenessQuery.data && <CompletenessPanel result={completenessQuery.data} scenarioId={scenarioId} onCreate={createGuidedDependency} />}
          {structure.workspace.renderer === "entity" && selected && <><TypedEntityEditor entity={selected} document={local.definition_document} focusPath={editorFocusPath} initializationHref={`/scenarios/${scenarioId}/edit/initialization`} onDeleteFact={deleteFact} onDeleteNested={(request) => void removeNested(request)} scenarioId={scenarioId} onChange={updateSelectedEntity} />{completenessQuery.data && <ContextualCompleteness result={completenessQuery.data} scenarioId={scenarioId} kind={selected.kind} objectKey={selected.key} onCreate={createGuidedDependency} />}</>}
          {["root", "root-collection", "hybrid"].includes(structure.workspace.renderer) && sectionValue !== null && <TypedEditor section={section} value={sectionValue} document={local.definition_document} focusPath={editorFocusPath} collectionSelection={collectionSelection} onDeleteNested={(request) => void removeNested(request)} onChange={(value) => editDocument(updateSectionRoot(local.definition_document, section, value))} onCollectionChange={(value) => { if (collectionSelection?.owner === "collection") updateCollectionItem(collectionSelection, value); }} onCollectionRemove={() => { if (collectionSelection?.owner === "collection") removeCollectionItem(collectionSelection); }} onCollectionRemoveSelection={removeCollectionItem} onCollectionMove={(direction) => { if (collectionSelection?.owner === "collection") moveCollectionItem(collectionSelection, direction); }} onInstructionChange={updatePlanningInstruction} onInstructionRemove={removePlanningInstruction} onInstructionMove={movePlanningInstruction} />}
          {structure.workspace.renderer === "workflow" && <ValidationPanel draft={serverDraft} published={publishedVersionQuery.data ?? null} validation={validation} initializationPreview={initializationPreviewQuery.data ?? null} sandboxGoal={sandboxGoal} sandbox={sandbox} setSandboxGoal={setSandboxGoal} onValidate={() => void validate()} onPublish={() => void publish()} onTest={() => void testDraft()} onIssue={focusIssue} />}
          {structure.workspace.renderer === "entity" && !selected && <div className="canvas-empty"><strong>{objects.length === 0 ? `暂无${sectionLabels[section] ?? "对象"}` : "从左侧选择一个对象"}</strong><p>{objects.length === 0 ? "使用左侧新增操作创建第一个项目。" : "选择或新建对象后，在这里编辑它的结构化字段。"}</p></div>}
        </div></section>
        {structure.capabilities.inspector && <aside className={`inspector inspector-new${inspectorOpen ? " is-open" : " is-collapsed"}`}><div className="inspector-heading"><div><p className="panel-kicker">详情</p><h3>{showWorldTopology ? "拓扑检查器" : "检查器"}</h3></div><button type="button" className="editor-button editor-button-ghost" onClick={() => setInspectorOpen(false)}>收起</button></div><div className="inspector-scroll">
          {showWorldTopology ? <>
            {!topologySelection && <div className="inspector-empty"><strong>未选择拓扑对象</strong><p className="muted">单击范围或实体查看摘要，双击进入下一层。</p></div>}
            {topologyScope && <><section className="inspector-section"><p className="inspector-section-title">范围摘要</p><h4 className="topology-inspector-title">{topologyScope.name}</h4><p className="muted">{topologyScope.description || "当前场景范围"}</p><div className="topology-metrics"><span><strong>{topologyScope.internalNodeCount}</strong>内部实体</span><span><strong>{topologyScope.externalConnectionCount}</strong>外部连接</span><span><strong>{topologyScope.factCount}</strong>事实</span></div></section><section className="inspector-section"><p className="inspector-section-title">相邻范围</p>{topologyScope.neighborScopeKeys.length === 0 ? <p className="muted">没有外部连接。</p> : topologyScope.neighborScopeKeys.map((key) => <button type="button" className="topology-inspector-link" key={key} onClick={() => { setTopologyContext({ kind: "scope", scopeKey: key }); setTopologySelection({ kind: "scope", key }); }}>{topologyOverview.scopes.find((scope) => scope.key === key)?.name ?? key}</button>)}</section><section className="inspector-section inspector-actions"><button type="button" className="editor-button editor-button-primary" onClick={() => setTopologyContext({ kind: "scope", scopeKey: topologyScope.key })}>进入范围</button><button type="button" className="editor-button editor-button-secondary" onClick={() => openTopologyEditor(topologyScope.key)}>在编辑器中打开</button></section></>}
            {topologyNode && <><section className="inspector-section"><p className="inspector-section-title">实体摘要</p><h4 className="topology-inspector-title">{topologyNode.name}</h4><p className="muted machine-key">{topologyNode.key}</p><div className="topology-detail-list"><span>类型 <strong>{topologyNode.nodeTypeName}</strong></span><span>所在范围 <strong>{topologyNode.scopeKeys.join("、") || "未归属"}</strong></span><span>直接关系 <strong>{topologyNode.relationCount}</strong></span><span>事实 <strong>{topologyNode.factCount}</strong></span></div></section><section className="inspector-section"><p className="inspector-section-title">邻域操作</p><button type="button" className="editor-button editor-button-primary" onClick={() => setTopologyContext({ kind: "entity", entityKey: topologyNode.key, scopeKey: topologyNode.scopeKeys[0] ?? findScopeForNode(local.definition_document, topologyNode.key) })}>聚焦关系</button><button type="button" className="editor-button editor-button-secondary" onClick={() => openTopologyEditor(topologyNode.key)}>在编辑器中打开</button></section>{topologyNeighborhood && <section className="inspector-section"><p className="inspector-section-title">一跳关系</p>{topologyNeighborhood.relations.length === 0 ? <p className="muted">没有直接关系。</p> : topologyNeighborhood.relations.map((relation) => <p className="topology-relation-summary" key={relation.key}>{relation.sourceNodeName} <span>{relation.relationTypeKey}</span> {relation.targetNodeName}</p>)}</section>}</>}
            {topologyPortal && <section className="inspector-section"><p className="inspector-section-title">边界出口</p><h4 className="topology-inspector-title">→ {topologyPortal.neighborScopeName}</h4><p className="muted">{topologyPortal.transportNodeNames.join("、") || topologyPortal.relationSummaries.join("、") || "边界连接"}</p><div className="inspector-actions"><button type="button" className="editor-button editor-button-primary" onClick={() => { setTopologyContext({ kind: "scope", scopeKey: topologyPortal.neighborScopeKey }); setTopologySelection({ kind: "scope", key: topologyPortal.neighborScopeKey }); }}>前往 {topologyPortal.neighborScopeName}</button>{topologyPortal.transportNodeKeys.length === 1 && <button type="button" className="editor-button editor-button-secondary" onClick={() => openTopologyEditor(topologyPortal.transportNodeKeys[0])}>打开连接对象</button>}</div></section>}
            {topologyRelation && <section className="inspector-section"><p className="inspector-section-title">关系摘要</p><h4 className="topology-inspector-title">{topologyRelation.relationTypeKey}</h4><p>{topologyRelation.sourceNodeName} → {topologyRelation.targetNodeName}</p><code className="machine-key">{topologyRelation.key}</code></section>}
          </> : !selected ? <div className="inspector-empty"><strong>未选择对象</strong><p className="muted">选择一个对象后查看身份、引用和危险操作。</p></div> : <><section className="inspector-section"><p className="inspector-section-title">对象身份</p>{typeof selected.value.name === "string" && <label>显示名称<input value={objectDisplayValue(selected.value, selected.key)} onChange={(event) => changeName(event.target.value)} /></label>}<label>{selected.kind === "public_reference" ? "语义身份" : selected.kind === "relation" && !selected.value.key ? "复合身份" : "稳定键"}<input readOnly value={selected.key} /></label></section><section className="inspector-section"><p className="inspector-section-title">引用关系</p>{usedBy.length === 0 ? <p className="muted">没有对象引用。</p> : usedBy.map((edge, index) => edge.source.object_key ? <Link key={index} to={editorLocatorHref({ owner: "entity", section: sectionForKind(edge.source.object_kind), kind: edge.source.object_kind as EntityKind, objectKey: edge.source.object_key, fieldPath: edge.source.field_path }, scenarioId)}>{kindLabels[edge.source.object_kind] ?? edge.source.object_kind} · {edge.source.object_key}</Link> : <p className="muted" key={index}>{kindLabels[edge.source.object_kind] ?? edge.source.object_kind} · {edge.source.field_path}</p>)}</section><section className="inspector-section inspector-danger"><p className="inspector-section-title">危险操作</p><div className="button-row">{structure.capabilities.rename && (selected.kind !== "relation" || typeof selected.value.key === "string") && <button type="button" className="editor-button editor-button-secondary" onClick={requestRename}>重命名稳定键</button>}<button type="button" className="editor-button editor-button-danger" onClick={() => void remove()}>删除</button></div></section></>}
        </div></aside>}
      </div>
    </section>
  </main>;
}

function VersionDetailPanel({ draft, published, validation }: { draft: Draft; published: ScenarioVersionDetail | null; validation: ValidationResult | null }) {
  const differenceCount = published ? documentDifferenceCount(draft.definition_document, published.definition_document) : null;
  const aheadLabel = !published ? "暂无已发布版本" : differenceCount !== null && differenceCount > 0 ? "包含未发布修改" : "与最新已发布版本一致";
  return <section className="validation-section validation-version-detail"><h4>版本详情</h4><div className="version-detail-grid"><span>当前草稿 <strong>r{draft.revision}</strong></span><span>验证状态 <strong>{validationStatusLabel(draft.validation_status)}</strong></span><span>最新发布 <strong>{published ? `v${published.version_number}` : "暂无"}</strong></span><span>草稿状态 <strong>{aheadLabel}</strong></span><span>当前发布准备度 <strong>{validation?.publish_ready ? "可发布" : "需先通过验证"}</strong></span></div><p className="muted">保存只更新场景草稿；验证不会发布。发布会创建不可变场景版本。</p><p className="muted">新游戏必须明确选择已发布的场景版本；已有游戏继续固定使用创建时的场景版本。</p><details className="version-technical-details"><summary>技术详情</summary><dl><div><dt>基础已发布版本</dt><dd><code>{draft.base_scenario_version_id ?? "无"}</code></dd></div><div><dt>草稿内容哈希</dt><dd><code>{draft.content_hash ?? "无"}</code></dd></div>{published && <div><dt>当前发布内容哈希</dt><dd><code>{published.content_hash}</code></dd></div>}</dl></details></section>;
}

function ValidationPanel({ draft, published, validation, initializationPreview, sandboxGoal, sandbox, setSandboxGoal, onValidate, onPublish, onTest, onIssue }: { draft: Draft | null; published: ScenarioVersionDetail | null; validation: ValidationResult | null; initializationPreview: InitializationPreview | null; sandboxGoal: string; sandbox: DraftSandboxResult | null; setSandboxGoal: (value: string) => void; onValidate: () => void; onPublish: () => void; onTest: () => void; onIssue: (issue: ValidationResult["issues"][number]) => void }) {
  return <div className="validation-panel">{draft && <VersionDetailPanel draft={draft} published={published} validation={validation} />}<section className="validation-section validation-actions"><h4>草稿检查与发布</h4><p className="muted">先验证当前草稿；只有通过验证的已保存版本可以发布。</p><div className="button-row"><button onClick={onValidate}>验证当前草稿</button><button disabled={!validation?.publish_ready} onClick={onPublish}>发布不可变版本</button></div></section><section className="validation-section validation-bootstrap"><h4>开局准备度 · 当前草稿与已发布版本</h4>{initializationPreview ? <><p>初始化警告 {initializationPreview.projection.summary.warnings} 项</p><p>发布后开局变化 {initializationPreview.parity.initialization_changes.length} 项 · 设计变化 {initializationPreview.parity.design_changes.length} 组</p><Link to="../initialization" className="editor-button editor-button-secondary">打开初始化配置</Link></> : <p className="muted">正在生成开局完整度和版本差异。</p>}</section><section className="validation-section validation-readiness"><h4>运行准备度</h4>{validation ? validation.readiness.map((item) => <div className={`readiness ${item.passed ? "pass" : "fail"}`} key={item.level}>{item.passed ? "✓" : "×"} {uiLabel(item.level)}</div>) : <p className="muted">验证后将在这里显示各级运行准备度。</p>}</section><section className="validation-section validation-issues"><h4>问题</h4>{!validation ? <p className="muted">尚未验证当前草稿。</p> : validation.issues.length === 0 ? <p>没有发现问题。</p> : validation.issues.map((issue) => <article className={`issue ${issue.severity.toLowerCase()}`} role="button" tabIndex={0} onClick={() => onIssue(issue)} key={`${issue.code}:${issue.path}`}><strong>{uiLabel(issue.severity)} · {issue.code}</strong><p>{diagnosticMessage(issue.code, issue.message)}</p><code>{issue.path}</code>{issue.locator && <small>点击定位到字段</small>}</article>)}</section>
    <section className="sandbox-panel"><h4>预览/测试当前草稿</h4><p className="muted">在一次性隔离沙盒中运行，不会创建正式游戏。</p><label htmlFor="sandbox-goal">可选目标<input id="sandbox-goal" value={sandboxGoal} onChange={(event) => setSandboxGoal(event.target.value)} placeholder="输入精确版本中定义的目标别名" /></label><button onClick={onTest}>启动隔离测试</button>{sandbox && <div className={sandbox.sandbox_started ? "sandbox-result pass" : "sandbox-result fail"}><strong>{sandbox.sandbox_started ? "沙盒已启动" : "草稿无效，未启动沙盒"}</strong>{sandbox.goal_status && <p>目标解析：{uiLabel(sandbox.goal_status)}</p>}{sandbox.task && <p>任务状态：{uiLabel(sandbox.task.status)}</p>}{sandbox.issues.map((issue) => <p key={`${issue.code}:${issue.path}`}>{uiLabel(issue.severity)} · {diagnosticMessage(issue.code, issue.message)}</p>)}</div>}</section>
  </div>;
}
