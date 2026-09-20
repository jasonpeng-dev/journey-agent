import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { TypedEditor, TypedEntityEditor } from "../components/TypedEditor";
import { InitializationWorkspace } from "../components/InitializationWorkspace";
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
import { appendRootCollectionItem, removeRootCollectionItem, replaceRootCollectionItem, rootCollectionDefinitions, rootCollectionIdentity, rootCollectionItems, rootCollectionReferencePath, rootSingletonOwner, type RootCollectionKey, type RootCollectionSelection, type RootOwnerSelection } from "../editor-collections";
import { sectionStructure } from "../editor-structure";
import { editorLocatorFromValidation, editorLocatorHref } from "../editor-locator";
import { cloneWorkingDocument, deriveWorkingCopySaveState, workingCopyIsDirty, workingDocumentsEqual, type WorkingCopySaveState } from "../editor-working-copy";
import { buildEntityNeighborhood, buildScopeOverview, buildScopeTopology, findScopeForNode, nodeByTopologyKey, relationByTopologyKey } from "../topology-projection";
import type { Draft, DraftSandboxResult, InitializationPreview, ValidationResult } from "../types";
import { diagnosticMessage, editorSectionTaxonomy, editorTaxonomyGroups, errorText, kindLabels, sectionLabels, uiLabel } from "../ui";

type SaveState = WorkingCopySaveState;
const saveLabels: Record<SaveState, string> = { UNCHANGED: "未修改", DIRTY: "有未保存修改", SAVING: "保存中", CONFLICT: "版本冲突", ERROR: "保存失败" };
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

function rootEditorKey(section: EditorSection): string | null {
  if (section === "overview") return "metadata";
  if (section === "goal-resolution") return "goal_resolution";
  if (section === "public-knowledge") return "public_knowledge";
  if (section === "initialization" || section === "planning") return section;
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
  const section: EditorSection = requestedSection === "world" && objectKey ? "world-entities" : requestedSection;
  const structure = sectionStructure(section);
  const singletonOwner = useMemo(() => rootSingletonOwner(section), [section]);
  const navigateRouter = useNavigate();
  const navigate = (to: string, options?: { replace?: boolean }) => navigateRouter(normalizeEditorNavigation(to, scenarioId), options);
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedKind = searchParams.get("kind");
  const routeKind = structure.owners.entityKinds.includes(requestedKind as EntityKind) ? requestedKind as EntityKind : null;
  const queryClient = useQueryClient();
  const draftQuery = useQuery({ queryKey: ["draft", scenarioId], queryFn: () => api.draft(scenarioId) });
  const [serverDraft, setServerDraft] = useState<Draft | null>(null);
  const [workingDocument, setWorkingDocument] = useState<Record<string, unknown> | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("UNCHANGED");
  const [message, setMessage] = useState("");
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
  const initializationPreviewQuery = useQuery({
    queryKey: ["initialization-preview", scenarioId, serverDraft?.revision, workingDocument],
    queryFn: () => api.initializationPreview(scenarioId, serverDraft!.revision, workingDocument!),
    enabled: Boolean(serverDraft && workingDocument && saveState !== "CONFLICT" && (section === "initialization" || section === "validation")),
    retry: false,
    placeholderData: (previous) => previous,
  });
  const local = useMemo(() => serverDraft && workingDocument ? { ...serverDraft, definition_document: workingDocument } : null, [serverDraft, workingDocument]);
  const hasUnsavedChanges = workingCopyIsDirty(serverDraft, workingDocument);
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
  const collectionDefinitions = useMemo(() => rootCollectionDefinitions(section), [section]);
  const collectionItems = useMemo(() => rootCollectionItems(section, sectionValue, local?.definition_document), [local?.definition_document, section, sectionValue]);
  const collectionSelection = collectionSelectionState?.section === section ? collectionSelectionState.selection : null;
  useEffect(() => {
    const owner = searchParams.get("owner");
    const collection = searchParams.get("collection");
    const identity = searchParams.get("item");
    const collectionMatch = collectionDefinitions.find((definition) => definition.key === collection);
    if (owner === "collection" && collectionMatch && identity && collectionItems.some((item) => item.collection === collectionMatch.key && item.identity === identity)) {
      setCollectionSelectionState({ section, selection: { owner: "collection", collection: collectionMatch.key, identity } });
      return;
    }
    if (singletonOwner && owner === singletonOwner.key) {
      setCollectionSelectionState({ section, selection: { owner: "singleton", key: singletonOwner.key } });
      return;
    }
    setCollectionSelectionState(singletonOwner ? { section, selection: { owner: "singleton", key: singletonOwner.key } } : null);
  }, [collectionDefinitions, collectionItems, searchParams, section, singletonOwner]);
  const filteredCollectionItems = useMemo(() => {
    const query = objectSearch.trim().toLocaleLowerCase();
    return collectionItems.filter((item) => !query || [item.title, item.summary, item.identityLabel, item.collection].some((value) => value.toLocaleLowerCase().includes(query)));
  }, [collectionItems, objectSearch]);
  const singletonOwnerVisible = singletonOwner
    ? !objectSearch.trim() || [singletonOwner.label, singletonOwner.summary].some((value) => value.toLocaleLowerCase().includes(objectSearch.trim().toLocaleLowerCase()))
    : false;
  const selectedCollectionItem = collectionSelection?.owner === "collection"
    ? collectionItems.find((item) => item.collection === collectionSelection.collection && item.identity === collectionSelection.identity) ?? null
    : null;
  const selectedSingletonOwner = collectionSelection?.owner === "singleton" ? singletonOwner : null;
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

  useEffect(() => {
    if (!hasUnsavedChanges) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [hasUnsavedChanges]);

  if (!local) return <main className="page"><p>正在加载草稿…</p></main>;

  const editDocument = (document: JsonObject) => {
    const next = cloneWorkingDocument(document);
    workingDocumentRef.current = next;
    setWorkingDocument(next);
    setSaveState(deriveWorkingCopySaveState(serverDraftRef.current, next));
    setValidation(null);
    setSandbox(null);
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
  const discardWorkingCopy = (confirmDiscard = true): boolean => {
    if (confirmDiscard && hasUnsavedChanges && !window.confirm("当前有未保存修改，确定放弃并离开吗？")) return false;
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
    if (!hasUnsavedChanges || discardWorkingCopy()) navigate(to);
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
    const selectedItem = selectedCollectionItem?.value ?? null;
    const referencePath = selectedItem ? rootCollectionReferencePath(selection.collection, selectedItem) : null;
    const identityChanged = rootCollectionIdentity(selection.collection, item) !== selection.identity;
    const identityReferences = referencePath ? refsQuery.data?.references.filter((edge) => edge.target.object_kind === "initialization" && edge.target.field_path === referencePath) ?? [] : [];
    if (identityChanged && identityReferences.length > 0) {
      setMessage("该身份仍被其他配置引用，不能直接修改。请先移除相关引用。");
      return;
    }
    const result = replaceRootCollectionItem(root, selection, item);
    if (!result.ok) { setMessage(result.reason); return; }
    setMessage("");
    editDocument(updateSectionRoot(local.definition_document, section, result.root));
    setCollectionSelection(result.selection, true);
  };
  const removeCollectionItem = (selection: RootCollectionSelection) => {
    const root = structuredClone(sectionValue) as JsonObject;
    const selectedItem = selectedCollectionItem?.value ?? null;
    const referencePath = selectedItem ? rootCollectionReferencePath(selection.collection, selectedItem) : null;
    const references = referencePath ? refsQuery.data?.references.filter((edge) => edge.target.object_kind === "initialization" && edge.target.field_path === referencePath) ?? [] : [];
    if (references.length > 0) {
      setMessage("该集合项仍被其他配置引用，不能删除。请先移除相关引用。");
      return;
    }
    if (!window.confirm("确定删除当前集合项吗？")) return;
    const result = removeRootCollectionItem(root, selection);
    if (!result.ok) { setMessage(result.reason); return; }
    setMessage("");
    editDocument(updateSectionRoot(local.definition_document, section, result.root));
    setCollectionSelection(null);
  };
  const createCollectionItem = (collectionKey: RootCollectionKey) => {
    const root = structuredClone(sectionValue) as JsonObject;
    const result = appendRootCollectionItem(root, collectionKey);
    if (!result.ok || !result.selection) { setMessage(result.ok ? "无法选择新集合项。" : result.reason); return; }
    setMessage("");
    editDocument(updateSectionRoot(local.definition_document, section, result.root));
    setCollectionSelection(result.selection);
  };
  const deleteInitializationResourcePool = (poolKey: string) => {
    const initialization = local.definition_document.initialization;
    if (!initialization || typeof initialization !== "object" || Array.isArray(initialization)) return;
    const pools = Array.isArray((initialization as JsonObject).resource_pools) ? (initialization as JsonObject).resource_pools as JsonObject[] : [];
    const pool = pools.find((item) => item.pool_key === poolKey);
    if (!pool) return;
    const referencePath = rootCollectionReferencePath("resource_pools", pool);
    const references = refsQuery.data?.references.filter((edge) => edge.target.object_kind === "initialization" && edge.target.field_path === referencePath) ?? [];
    if (references.length > 0) {
      setMessage("该集合项仍被其他配置引用，不能删除。请先移除相关引用。");
      return;
    }
    if (!window.confirm("确定删除当前资源池吗？")) return;
    setMessage("");
    editDocument(updateSectionRoot(local.definition_document, "initialization", { ...(initialization as JsonObject), resource_pools: pools.filter((item) => item !== pool) }));
  };

  const rename = async () => {
    if (!selected) return;
    const newKey = window.prompt("请输入新的稳定键", selected.key)?.trim();
    if (!newKey || newKey === selected.key) return;
    const document = workingDocumentRef.current;
    const draft = serverDraftRef.current;
    if (!document || !draft) return;
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
    if (!selected || !window.confirm(`确定删除“${objectDisplayValue(selected.value, selected.key)}”吗？被其他对象引用时系统会阻止删除。`)) return;
    const document = workingDocumentRef.current;
    const draft = serverDraftRef.current;
    if (!document || !draft) return;
    try {
      const transformed = await api.transformWorkingCopy(scenarioId, draft.revision, document, { kind: "DELETE_OBJECT", object_kind: selected.kind, object_key: selected.key });
      if (!workingDocumentsEqual(workingDocumentRef.current, document)) {
        setMessage("工作副本在删除期间发生了新修改，请重试该操作。");
        return;
      }
      editDocument(transformed.definition_document);
      navigate(`/scenarios/${scenarioId}/edit/${section}`);
    } catch (error) { setSaveState(error instanceof ApiError && error.code === "SCENARIO_DRAFT_CONFLICT" ? "CONFLICT" : "ERROR"); setMessage(errorText(error, "删除失败，该对象可能仍被引用。")); }
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
    ? collectionItems.length + (singletonOwner ? 1 : 0)
    : structure.master.source === "topology"
      ? nodeSemanticView(local.definition_document, "all").length
      : objects.length;
  const workspaceTitle = showWorldTopology
    ? "世界拓扑"
    : structure.workspace.renderer === "initialization"
      ? "初始化"
    : selected
      ? objectDisplayValue(selected.value, selected.name)
      : selectedCollectionItem
        ? selectedCollectionItem.title
        : selectedSingletonOwner
          ? selectedSingletonOwner.label
        : sectionLabels[section] ?? section;
  const workspaceSubtitle = showWorldTopology
    ? "浏览范围、实体与关系；编辑请进入对应的世界模型页面"
    : structure.workspace.renderer === "initialization"
      ? "配置当前版本的开局状态"
    : selected
      ? `${kindLabels[selected.kind] ?? selected.kind} · 可编辑对象`
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
      <nav className="editor-section-nav" aria-label="编辑器导航">{editorTaxonomyGroups.map((group) => <div className="editor-nav-group" key={group.label}><p>{group.label}</p>{group.items.map((item) => <Link className={item === section ? "active" : ""} key={item} to={`/scenarios/${scenarioId}/edit/${item}`} onClick={(event) => { if (hasUnsavedChanges) { event.preventDefault(); guardedNavigate(`/scenarios/${scenarioId}/edit/${item}`); } }}>{sectionLabels[item] ?? item}</Link>)}</div>)}</nav>
    </aside>
    <section className="editor-main">
      <header className="editor-toolbar"><div className="editor-toolbar-context"><div className="editor-breadcrumb" data-testid="editor-taxonomy-heading"><span>{taxonomy.category}</span><span aria-hidden="true">/</span><strong>{taxonomy.label}</strong></div></div><div className="editor-heading-actions"><span className={`save-state ${saveState.toLowerCase()}`}><i aria-hidden="true" />{saveLabels[saveState]}</span><button type="button" className="editor-button editor-button-primary" disabled={!hasUnsavedChanges || save.isPending} onClick={saveWorkingCopy}>保存</button><button type="button" className="editor-button editor-button-secondary" disabled={!hasUnsavedChanges || save.isPending} onClick={() => discardWorkingCopy()}>放弃修改</button><button type="button" className="editor-button editor-button-danger editor-return-detail" onClick={() => guardedNavigate(`/scenarios/${scenarioId}`)}>返回场景详情</button>{structure.capabilities.inspector && <button type="button" className="editor-button editor-button-ghost" onClick={() => setInspectorOpen((current) => !current)}>{inspectorOpen ? "隐藏检查器" : "显示检查器"}</button>}</div></header>
      {message && <div className="conflict-banner"><p>{message}</p>{saveState === "CONFLICT" && <button type="button" className="editor-button editor-button-secondary" onClick={() => void reloadServerDraft()}>重新加载服务器草稿</button>}</div>}
      <div className={`editor-columns${structure.master.visible ? "" : " master-hidden"}${inspectorOpen ? "" : " inspector-collapsed"}`}>
        {structure.master.visible && <aside className="object-list object-panel">
          <header className="object-panel-header"><div><p className="panel-kicker">{structure.mode === "BROWSER" ? "世界结构" : structure.mode === "HYBRID" ? "配置导航" : "内容导航"}</p><div className="object-panel-title">{sectionLabels[section] ?? section}</div></div><span className="object-count">{masterCount}</span></header>
          {structure.master.source === "topology" && <div className="segmented-control world-filter-tabs" role="tablist" aria-label="世界对象筛选">{(["all", "regions", "facilities", "transports"] as WorldView[]).map((item) => <button type="button" className={worldView === item ? "selected" : ""} aria-pressed={worldView === item} key={item} onClick={() => setWorldView(item)}><span>{worldViewLabels[item]}</span><small>{nodeSemanticView(local.definition_document, item).length}</small></button>)}</div>}
          <div className="object-panel-tools">{structure.master.searchable && <label className="object-search">搜索<input value={objectSearch} placeholder={usesRootCollections ? "名称、代码或标识" : "名称或稳定键"} onChange={(event) => setObjectSearch(event.target.value)} /></label>}{structure.master.source === "entities" && availableKinds.length > 1 && !structure.master.grouped && <label className="object-filter">对象类型<select value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="all">全部类型</option>{availableKinds.map((kind) => <option key={kind} value={kind}>{kindLabels[kind] ?? kind}</option>)}</select></label>}{structure.master.create === "entity" && !structure.master.grouped && <div className="object-list-actions">{(kindsBySection[section] ?? []).map((kind) => <button type="button" className="editor-button editor-button-secondary add-object" key={kind} onClick={() => createObject(kind)}>＋ {kind === "relation_type" ? "新增关系类型" : kind === "relation" ? "新增关系实例" : kindLabels[kind] ?? kind}</button>)}</div>}</div>
          <div className="object-list-scroll">
            {usesRootCollections ? <div className="collection-list-groups">
              {singletonOwner && <section className="collection-list-group">
                <header><div><h4>基础配置</h4><span>1 项</span></div></header>
                {singletonOwnerVisible && <button type="button" className={`collection-list-item${collectionSelection?.owner === "singleton" ? " selected" : ""}`} onClick={() => setCollectionSelection({ owner: "singleton", key: singletonOwner.key })}><strong>{singletonOwner.label}</strong><span>{singletonOwner.summary}</span><code>{singletonOwner.key}</code></button>}
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
         <section className={`canvas editor-canvas${showWorldTopology ? " canvas-topology" : ""}${structure.workspace.renderer === "initialization" ? " canvas-initialization" : ""}`}><header className="canvas-header"><div><p className="panel-kicker">{structure.mode === "WORKFLOW" ? "工作流程" : structure.mode === "BROWSER" ? "浏览器" : "编辑区"}</p><h3>{workspaceTitle}</h3><p className="canvas-subtitle">{workspaceSubtitle}</p></div></header><div className={`canvas-body${showWorldTopology ? " canvas-body-topology" : ""}${structure.workspace.renderer === "initialization" ? " canvas-body-initialization" : ""}`}>{showWorldTopology && <WorldGraph document={local.definition_document} context={topologyContext} selection={topologySelection} focusNodeKey={topologyFocusNodeKey} onContextChange={setTopologyContext} onSelectionChange={setTopologySelection} onOpenEditor={openTopologyEditor} onFocusNodeConsumed={() => setTopologyFocusNodeKey(null)} />}{structure.workspace.renderer === "initialization" && <InitializationWorkspace document={local.definition_document} preview={initializationPreviewQuery.data ?? null} loading={initializationPreviewQuery.isPending} error={initializationPreviewQuery.error ? errorText(initializationPreviewQuery.error) : null} scenarioId={scenarioId} onChange={editDocument} onDeleteResourcePool={deleteInitializationResourcePool} />}{structure.workspace.renderer === "entity" && selected && <TypedEntityEditor entity={selected} document={local.definition_document} focusPath={editorFocusPath} initializationHref={`/scenarios/${scenarioId}/edit/initialization`} onChange={updateSelectedEntity} />}{["root", "root-collection", "hybrid"].includes(structure.workspace.renderer) && sectionValue !== null && <TypedEditor section={section} value={sectionValue} document={local.definition_document} focusPath={editorFocusPath} collectionSelection={collectionSelection} onChange={(value) => editDocument(updateSectionRoot(local.definition_document, section, value))} onCollectionChange={(value) => { if (collectionSelection?.owner === "collection") updateCollectionItem(collectionSelection, value); }} onCollectionRemove={() => { if (collectionSelection?.owner === "collection") removeCollectionItem(collectionSelection); }} />}{structure.workspace.renderer === "workflow" && <ValidationPanel validation={validation} initializationPreview={initializationPreviewQuery.data ?? null} sandboxGoal={sandboxGoal} sandbox={sandbox} setSandboxGoal={setSandboxGoal} onValidate={() => void validate()} onPublish={() => void publish()} onTest={() => void testDraft()} onIssue={focusIssue} />}{structure.workspace.renderer === "entity" && !selected && <div className="canvas-empty"><strong>{objects.length === 0 ? `暂无${sectionLabels[section] ?? "对象"}` : "从左侧选择一个对象"}</strong><p>{objects.length === 0 ? "使用左侧新增操作创建第一个项目。" : "选择或新建对象后，在这里编辑它的结构化字段。"}</p></div>}</div></section>
        {structure.capabilities.inspector && <aside className={`inspector inspector-new${inspectorOpen ? " is-open" : " is-collapsed"}`}><div className="inspector-heading"><div><p className="panel-kicker">详情</p><h3>{showWorldTopology ? "拓扑检查器" : "检查器"}</h3></div><button type="button" className="editor-button editor-button-ghost" onClick={() => setInspectorOpen(false)}>收起</button></div><div className="inspector-scroll">
          {showWorldTopology ? <>
            {!topologySelection && <div className="inspector-empty"><strong>未选择拓扑对象</strong><p className="muted">单击范围或实体查看摘要，双击进入下一层。</p></div>}
            {topologyScope && <><section className="inspector-section"><p className="inspector-section-title">范围摘要</p><h4 className="topology-inspector-title">{topologyScope.name}</h4><p className="muted">{topologyScope.description || "当前场景范围"}</p><div className="topology-metrics"><span><strong>{topologyScope.internalNodeCount}</strong>内部实体</span><span><strong>{topologyScope.externalConnectionCount}</strong>外部连接</span><span><strong>{topologyScope.factCount}</strong>事实</span></div></section><section className="inspector-section"><p className="inspector-section-title">相邻范围</p>{topologyScope.neighborScopeKeys.length === 0 ? <p className="muted">没有外部连接。</p> : topologyScope.neighborScopeKeys.map((key) => <button type="button" className="topology-inspector-link" key={key} onClick={() => { setTopologyContext({ kind: "scope", scopeKey: key }); setTopologySelection({ kind: "scope", key }); }}>{topologyOverview.scopes.find((scope) => scope.key === key)?.name ?? key}</button>)}</section><section className="inspector-section inspector-actions"><button type="button" className="editor-button editor-button-primary" onClick={() => setTopologyContext({ kind: "scope", scopeKey: topologyScope.key })}>进入范围</button><button type="button" className="editor-button editor-button-secondary" onClick={() => openTopologyEditor(topologyScope.key)}>在编辑器中打开</button></section></>}
            {topologyNode && <><section className="inspector-section"><p className="inspector-section-title">实体摘要</p><h4 className="topology-inspector-title">{topologyNode.name}</h4><p className="muted machine-key">{topologyNode.key}</p><div className="topology-detail-list"><span>类型 <strong>{topologyNode.nodeTypeName}</strong></span><span>所在范围 <strong>{topologyNode.scopeKeys.join("、") || "未归属"}</strong></span><span>直接关系 <strong>{topologyNode.relationCount}</strong></span><span>事实 <strong>{topologyNode.factCount}</strong></span></div></section><section className="inspector-section"><p className="inspector-section-title">邻域操作</p><button type="button" className="editor-button editor-button-primary" onClick={() => setTopologyContext({ kind: "entity", entityKey: topologyNode.key, scopeKey: topologyNode.scopeKeys[0] ?? findScopeForNode(local.definition_document, topologyNode.key) })}>聚焦关系</button><button type="button" className="editor-button editor-button-secondary" onClick={() => openTopologyEditor(topologyNode.key)}>在编辑器中打开</button></section>{topologyNeighborhood && <section className="inspector-section"><p className="inspector-section-title">一跳关系</p>{topologyNeighborhood.relations.length === 0 ? <p className="muted">没有直接关系。</p> : topologyNeighborhood.relations.map((relation) => <p className="topology-relation-summary" key={relation.key}>{relation.sourceNodeName} <span>{relation.relationTypeKey}</span> {relation.targetNodeName}</p>)}</section>}</>}
            {topologyPortal && <section className="inspector-section"><p className="inspector-section-title">边界出口</p><h4 className="topology-inspector-title">→ {topologyPortal.neighborScopeName}</h4><p className="muted">{topologyPortal.transportNodeNames.join("、") || topologyPortal.relationSummaries.join("、") || "边界连接"}</p><div className="inspector-actions"><button type="button" className="editor-button editor-button-primary" onClick={() => { setTopologyContext({ kind: "scope", scopeKey: topologyPortal.neighborScopeKey }); setTopologySelection({ kind: "scope", key: topologyPortal.neighborScopeKey }); }}>前往 {topologyPortal.neighborScopeName}</button>{topologyPortal.transportNodeKeys.length === 1 && <button type="button" className="editor-button editor-button-secondary" onClick={() => openTopologyEditor(topologyPortal.transportNodeKeys[0])}>打开连接对象</button>}</div></section>}
            {topologyRelation && <section className="inspector-section"><p className="inspector-section-title">关系摘要</p><h4 className="topology-inspector-title">{topologyRelation.relationTypeKey}</h4><p>{topologyRelation.sourceNodeName} → {topologyRelation.targetNodeName}</p><code className="machine-key">{topologyRelation.key}</code></section>}
          </> : !selected ? <div className="inspector-empty"><strong>未选择对象</strong><p className="muted">选择一个对象后查看身份、引用和危险操作。</p></div> : <><section className="inspector-section"><p className="inspector-section-title">对象身份</p>{typeof selected.value.name === "string" && <label>显示名称<input value={objectDisplayValue(selected.value, selected.key)} onChange={(event) => changeName(event.target.value)} /></label>}<label>{selected.kind === "public_reference" ? "语义身份" : selected.kind === "relation" && !selected.value.key ? "复合身份" : "稳定键"}<input readOnly value={selected.key} /></label></section><section className="inspector-section"><p className="inspector-section-title">引用关系</p>{usedBy.length === 0 ? <p className="muted">没有对象引用。</p> : usedBy.map((edge, index) => edge.source.object_key ? <Link key={index} to={editorLocatorHref({ owner: "entity", section: sectionForKind(edge.source.object_kind), kind: edge.source.object_kind as EntityKind, objectKey: edge.source.object_key, fieldPath: edge.source.field_path }, scenarioId)}>{kindLabels[edge.source.object_kind] ?? edge.source.object_kind} · {edge.source.object_key}</Link> : <p className="muted" key={index}>{kindLabels[edge.source.object_kind] ?? edge.source.object_kind} · {edge.source.field_path}</p>)}</section><section className="inspector-section inspector-danger"><p className="inspector-section-title">危险操作</p><div className="button-row">{structure.capabilities.rename && (selected.kind !== "relation" || typeof selected.value.key === "string") && <button type="button" className="editor-button editor-button-secondary" onClick={() => void rename()}>重命名稳定键</button>}<button type="button" className="editor-button editor-button-danger" onClick={() => void remove()}>删除</button></div></section></>}
        </div></aside>}
      </div>
    </section>
  </main>;
}

function ValidationPanel({ validation, initializationPreview, sandboxGoal, sandbox, setSandboxGoal, onValidate, onPublish, onTest, onIssue }: { validation: ValidationResult | null; initializationPreview: InitializationPreview | null; sandboxGoal: string; sandbox: DraftSandboxResult | null; setSandboxGoal: (value: string) => void; onValidate: () => void; onPublish: () => void; onTest: () => void; onIssue: (issue: ValidationResult["issues"][number]) => void }) {
  return <div className="validation-panel"><section className="validation-section validation-actions"><h4>草稿检查与发布</h4><p className="muted">先验证当前草稿；只有通过验证的已保存版本可以发布。</p><div className="button-row"><button onClick={onValidate}>验证当前草稿</button><button disabled={!validation?.publish_ready} onClick={onPublish}>发布不可变版本</button></div></section><section className="validation-section validation-bootstrap"><h4>开局准备度 · 当前草稿与已发布版本</h4>{initializationPreview ? <><p>初始化警告 {initializationPreview.projection.summary.warnings} 项</p><p>发布后开局变化 {initializationPreview.parity.initialization_changes.length} 项 · 设计变化 {initializationPreview.parity.design_changes.length} 组</p><Link to="../initialization" className="editor-button editor-button-secondary">打开初始化配置</Link></> : <p className="muted">正在生成开局完整度和版本差异。</p>}</section><section className="validation-section validation-readiness"><h4>运行准备度</h4>{validation ? validation.readiness.map((item) => <div className={`readiness ${item.passed ? "pass" : "fail"}`} key={item.level}>{item.passed ? "✓" : "×"} {uiLabel(item.level)}</div>) : <p className="muted">验证后将在这里显示各级运行准备度。</p>}</section><section className="validation-section validation-issues"><h4>问题</h4>{!validation ? <p className="muted">尚未验证当前草稿。</p> : validation.issues.length === 0 ? <p>没有发现问题。</p> : validation.issues.map((issue) => <article className={`issue ${issue.severity.toLowerCase()}`} role="button" tabIndex={0} onClick={() => onIssue(issue)} key={`${issue.code}:${issue.path}`}><strong>{uiLabel(issue.severity)} · {issue.code}</strong><p>{diagnosticMessage(issue.code, issue.message)}</p><code>{issue.path}</code>{issue.locator && <small>点击定位到字段</small>}</article>)}</section>
    <section className="sandbox-panel"><h4>预览/测试当前草稿</h4><p className="muted">在一次性隔离沙盒中运行，不会创建正式游戏。</p><label htmlFor="sandbox-goal">可选目标<input id="sandbox-goal" value={sandboxGoal} onChange={(event) => setSandboxGoal(event.target.value)} placeholder="输入精确版本中定义的目标别名" /></label><button onClick={onTest}>启动隔离测试</button>{sandbox && <div className={sandbox.sandbox_started ? "sandbox-result pass" : "sandbox-result fail"}><strong>{sandbox.sandbox_started ? "沙盒已启动" : "草稿无效，未启动沙盒"}</strong>{sandbox.goal_status && <p>目标解析：{uiLabel(sandbox.goal_status)}</p>}{sandbox.task && <p>任务状态：{uiLabel(sandbox.task.status)}</p>}{sandbox.issues.map((issue) => <p key={`${issue.code}:${issue.path}`}>{uiLabel(issue.severity)} · {diagnosticMessage(issue.code, issue.message)}</p>)}</div>}</section>
  </div>;
}
