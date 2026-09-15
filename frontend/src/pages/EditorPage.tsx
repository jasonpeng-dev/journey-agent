import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { TypedEditor, TypedEntityEditor } from "../components/TypedEditor";
import { WorldGraph } from "../components/WorldGraph";
import {
  addObject,
  filterDraftObjects,
  nodeSemanticView,
  replaceObject,
  sectionForKind,
  sectionObjects,
  sectionRoot,
  sections,
  updateObjectName,
  updateSectionRoot,
  type EditorSection,
  type EntityKind,
  type JsonObject,
} from "../editor";
import { kindsBySection } from "../templates";
import { rootCollectionDefinitions, rootCollectionDefault, rootCollectionItems, type RootCollectionSelection } from "../editor-collections";
import { cloneWorkingDocument, deriveWorkingCopySaveState, workingCopyIsDirty, workingDocumentsEqual, type WorkingCopySaveState } from "../editor-working-copy";
import type { Draft, DraftSandboxResult, ValidationResult } from "../types";
import { diagnosticMessage, errorText, kindLabels, sectionLabels, uiLabel } from "../ui";

type SaveState = WorkingCopySaveState;
const saveLabels: Record<SaveState, string> = { UNCHANGED: "未修改", DIRTY: "有未保存修改", SAVING: "保存中", CONFLICT: "版本冲突", ERROR: "保存失败" };
type WorldView = "all" | "regions" | "facilities" | "transports";
const worldViewLabels: Record<WorldView, string> = { all: "全部节点", regions: "区域", facilities: "设施", transports: "交通" };
type WorldMode = "edit" | "topology";

const sectionGroups: Array<{ label: string; items: EditorSection[] }> = [
  { label: "场景", items: ["overview", "world"] },
  { label: "实体", items: ["actors", "interactions", "actions"] },
  { label: "逻辑", items: ["rules", "objectives", "derived-states"] },
  { label: "配置", items: ["initialization", "goal-resolution", "planning"] },
  { label: "公开信息", items: ["public-knowledge", "public-references"] },
  { label: "发布", items: ["validation"] },
];

function isEditorSection(value: string): value is EditorSection {
  return (sections as readonly string[]).includes(value);
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

function sectionForLocator(kind: string): EditorSection {
  if (kind === "metadata") return "overview";
  if (kind === "goal_resolution") return "goal-resolution";
  if (kind === "public_knowledge") return "public-knowledge";
  if (kind === "initialization") return "initialization";
  if (kind === "planning") return "planning";
  return sectionForKind(kind);
}

export function EditorPage() {
  const { scenarioId = "", section: routeSection = "overview", objectKey } = useParams();
  const section: EditorSection = isEditorSection(routeSection) ? routeSection : "overview";
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
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
  const [worldMode, setWorldMode] = useState<WorldMode>("topology");
  const [objectSearch, setObjectSearch] = useState("");
  const [kindFilter, setKindFilter] = useState("all");
  const [collectionSelection, setCollectionSelection] = useState<RootCollectionSelection | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
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
    setCollectionSelection(null);
    if (section === "world") setWorldMode(objectKey ? "edit" : "topology");
  }, [section, objectKey]);

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
  const local = useMemo(() => serverDraft && workingDocument ? { ...serverDraft, definition_document: workingDocument } : null, [serverDraft, workingDocument]);
  const hasUnsavedChanges = workingCopyIsDirty(serverDraft, workingDocument);

  const objects = useMemo(() => {
    if (!local) return [];
    return section === "world" ? nodeSemanticView(local.definition_document, worldView) : sectionObjects(local.definition_document, section);
  }, [local, section, worldView]);
  const availableKinds = useMemo(() => Array.from(new Set(objects.map((item) => item.kind))), [objects]);
  const filteredObjects = useMemo(() => filterDraftObjects(objects, objectSearch, kindFilter), [objects, objectSearch, kindFilter]);
  const sectionValue = local ? sectionRoot(local.definition_document, section) : null;
  const collectionDefinitions = useMemo(() => rootCollectionDefinitions(section), [section]);
  const collectionItems = useMemo(() => rootCollectionItems(section, sectionValue), [section, sectionValue]);
  const filteredCollectionItems = useMemo(() => {
    const query = objectSearch.trim().toLocaleLowerCase();
    return collectionItems.filter((item) => !query || [item.title, item.summary, item.collection].some((value) => value.toLocaleLowerCase().includes(query)));
  }, [collectionItems, objectSearch]);
  const selectedCollectionItem = collectionSelection ? collectionItems.find((item) => item.collection === collectionSelection.collection && item.index === collectionSelection.index) ?? null : null;
  const selected = objects.find((item) => item.key === objectKey) ?? null;
  const focusPath = searchParams.get("focus_path");
  const editorFocusPath = focusPath ? `${selected ? `${selected.kind}.${selected.key}` : rootEditorKey(section) ?? ""}.${focusPath}` : null;
  const usedBy = refsQuery.data?.references.filter((edge) => edge.target.object_kind === selected?.kind && edge.target.object_key === selected?.key) ?? [];

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
    if (typeof selected.value.name === "string") editDocument(updateObjectName(local.definition_document, section, selected.key, name));
    else if (typeof selected.value.term === "string") editDocument(replaceObject(local.definition_document, section, selected.key, { ...selected.value, term: name }));
  };
  const createObject = (kind: EntityKind) => { const added = addObject(local.definition_document, kind); editDocument(added.document); navigate(`/scenarios/${scenarioId}/edit/${section}/${encodeURIComponent(added.key)}`); };
  const updateCollectionItem = (selection: RootCollectionSelection, item: JsonObject) => {
    const root = structuredClone(sectionValue) as JsonObject;
    const collection = Array.isArray(root[selection.collection]) ? [...root[selection.collection] as unknown[]] : [];
    collection[selection.index] = structuredClone(item);
    editDocument(updateSectionRoot(local.definition_document, section, { ...root, [selection.collection]: collection }));
  };
  const removeCollectionItem = (selection: RootCollectionSelection) => {
    const root = structuredClone(sectionValue) as JsonObject;
    const collection = Array.isArray(root[selection.collection]) ? [...root[selection.collection] as unknown[]] : [];
    collection.splice(selection.index, 1);
    editDocument(updateSectionRoot(local.definition_document, section, { ...root, [selection.collection]: collection }));
    setCollectionSelection(null);
  };
  const createCollectionItem = (collectionKey: RootCollectionSelection["collection"]) => {
    const root = structuredClone(sectionValue) as JsonObject;
    const collection = Array.isArray(root[collectionKey]) ? [...root[collectionKey] as unknown[]] : [];
    const index = collection.length;
    collection.push(rootCollectionDefault(collectionKey));
    editDocument(updateSectionRoot(local.definition_document, section, { ...root, [collectionKey]: collection }));
    setCollectionSelection({ collection: collectionKey, index });
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
      navigate(`/scenarios/${scenarioId}/edit/${section}/${encodeURIComponent(newKey)}`);
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
    const targetSection = sectionForLocator(locator.object_kind);
    const target = locator.object_key ? `/${encodeURIComponent(locator.object_key)}` : "";
    const query = locator.field_path ? `?focus_path=${encodeURIComponent(locator.field_path)}` : "";
    navigate(`/scenarios/${scenarioId}/edit/${targetSection}${target}${query}`);
  };

   const showWorldTopology = section === "world" && worldMode === "topology";
   const isCollectionSection = collectionDefinitions.length > 0;
   return <main className="editor-shell">
     <aside className="editor-nav">
       <div className="editor-nav-header"><Link className="editor-nav-back" to={`/scenarios/${scenarioId}`} onClick={(event) => { if (hasUnsavedChanges) { event.preventDefault(); guardedNavigate(`/scenarios/${scenarioId}`); } }}><span aria-hidden="true">←</span> 返回场景</Link><div className="editor-nav-identity"><p>SCENARIO EDITOR</p><h2>当前草稿</h2><span>Working copy</span></div></div>
       <nav className="editor-section-nav" aria-label="编辑器导航">{sectionGroups.map((group) => <div className="editor-nav-group" key={group.label}><p>{group.label}</p>{group.items.map((item) => <Link className={item === section ? "active" : ""} key={item} to={`/scenarios/${scenarioId}/edit/${item}`} onClick={(event) => { if (hasUnsavedChanges) { event.preventDefault(); guardedNavigate(`/scenarios/${scenarioId}/edit/${item}`); } }}>{sectionLabels[item] ?? item}</Link>)}</div>)}</nav>
     </aside>
    <section className="editor-main">
       <header className="editor-toolbar"><div className="editor-toolbar-context"><p className="eyebrow">场景编辑器</p><div className="editor-breadcrumb"><span>场景</span><span aria-hidden="true">/</span><strong>{sectionLabels[section] ?? section}</strong>{selected && <><span aria-hidden="true">/</span><span>{objectDisplayValue(selected.value, selected.name)}</span></>}{selectedCollectionItem && <><span aria-hidden="true">/</span><span>{selectedCollectionItem.title}</span></>}</div><p className="editor-toolbar-subtitle">{selected ? `${kindLabels[selected.kind] ?? selected.kind} · 结构化字段` : selectedCollectionItem ? `${selectedCollectionItem.collection} · 集合项详情` : `编辑 ${sectionLabels[section] ?? section} 配置`}</p></div><div className="editor-heading-actions"><span className={`save-state ${saveState.toLowerCase()}`}><i aria-hidden="true" />{saveLabels[saveState]}</span><button type="button" className="editor-button editor-button-primary" disabled={!hasUnsavedChanges || save.isPending} onClick={saveWorkingCopy}>保存</button><button type="button" className="editor-button editor-button-secondary" disabled={!hasUnsavedChanges || save.isPending} onClick={() => discardWorkingCopy()}>放弃修改</button><button type="button" className="editor-button editor-button-ghost" onClick={() => setInspectorOpen((current) => !current)}>{inspectorOpen ? "隐藏检查器" : "显示检查器"}</button></div></header>
       {message && <div className="conflict-banner"><p>{message}</p>{saveState === "CONFLICT" && <button type="button" className="editor-button editor-button-secondary" onClick={() => void reloadServerDraft()}>重新加载服务器草稿</button>}</div>}
       <div className={`editor-columns${inspectorOpen ? "" : " inspector-collapsed"}`}>
         <aside className="object-list object-panel"><header className="object-panel-header"><div><p className="panel-kicker">{isCollectionSection ? "COLLECTIONS" : section === "world" ? "STRUCTURE" : "OBJECTS"}</p><h3>{section === "world" ? "World 结构" : isCollectionSection && collectionDefinitions.length === 1 ? collectionDefinitions[0].label : sectionLabels[section] ?? "对象"}</h3></div><span className="object-count">{isCollectionSection ? collectionItems.length : section === "world" ? nodeSemanticView(local.definition_document, "all").length : objects.length}</span></header>{section === "world" && <div className="segmented-control world-filter-tabs" role="tablist" aria-label="World 对象筛选">{(["all", "regions", "facilities", "transports"] as WorldView[]).map((item) => <button type="button" className={worldView === item ? "selected" : ""} aria-pressed={worldView === item} key={item} onClick={() => setWorldView(item)}><span>{worldViewLabels[item]}</span><small>{nodeSemanticView(local.definition_document, item).length}</small></button>)}</div>}<div className="object-panel-tools"><label className="object-search">搜索对象<input value={objectSearch} placeholder={isCollectionSection ? "名称、代码或稳定键" : "名称或稳定键"} onChange={(event) => setObjectSearch(event.target.value)} /></label>{!isCollectionSection && section !== "world" && availableKinds.length > 1 && <label className="object-filter">对象类型<select value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="all">全部类型</option>{availableKinds.map((kind) => <option key={kind} value={kind}>{kindLabels[kind] ?? kind}</option>)}</select></label>}{!isCollectionSection && <div className="object-list-actions">{(kindsBySection[section] ?? []).map((kind) => <button type="button" className="editor-button editor-button-secondary add-object" key={kind} onClick={() => createObject(kind)}>＋ {kindLabels[kind] ?? kind}</button>)}</div>}</div><div className="object-list-scroll">{isCollectionSection ? <div className="collection-list-groups">{collectionDefinitions.map((definition) => <section className="collection-list-group" key={definition.key}><header><div><h4>{definition.label}</h4><span>{collectionItems.filter((item) => item.collection === definition.key).length} 项</span></div><button type="button" className="editor-button editor-button-secondary" onClick={() => createCollectionItem(definition.key)}>＋ 新增</button></header>{filteredCollectionItems.filter((item) => item.collection === definition.key).map((item) => <button type="button" className={`collection-list-item${collectionSelection?.collection === item.collection && collectionSelection.index === item.index ? " selected" : ""}`} key={`${item.collection}:${item.index}`} onClick={() => setCollectionSelection({ collection: item.collection, index: item.index })}><strong>{item.title}</strong><span>{item.summary}</span><code>{item.collection}.{item.index}</code></button>)}{collectionItems.filter((item) => item.collection === definition.key).length === 0 && <p className="muted collection-list-empty">暂无项目</p>}</section>)}</div> : <>{objects.length === 0 && <p className="muted object-empty">此部分还没有带稳定键的对象。</p>}{objects.length > 0 && filteredObjects.length === 0 && <p className="muted object-empty">没有匹配的对象。</p>}{filteredObjects.map((item) => <Link className={`object-list-item${item.key === objectKey ? " selected" : ""}`} key={`${item.kind}:${item.key}`} to={`/scenarios/${scenarioId}/edit/${section}/${encodeURIComponent(item.key)}`} onClick={() => { if (section === "world") setWorldMode("edit"); }}><span>{item.name}</span><code>{kindLabels[item.kind] ?? item.kind} · {item.key}</code></Link>)}</>}</div></aside>
         <section className={`canvas editor-canvas${showWorldTopology ? " canvas-topology" : ""}`}><header className="canvas-header"><div><p className="panel-kicker">WORKSPACE</p><h3>{showWorldTopology ? "拓扑概览" : selected ? objectDisplayValue(selected.value, selected.name) : selectedCollectionItem ? selectedCollectionItem.title : isCollectionSection ? sectionLabels[section] ?? section : "Typed 编辑器"}</h3><p className="canvas-subtitle">{showWorldTopology ? "浏览世界节点与关系，不直接编辑画布" : selected ? `${kindLabels[selected.kind] ?? selected.kind} · 可编辑对象` : selectedCollectionItem ? "集合项详情编辑" : isCollectionSection ? "左侧选择集合项；顶层固定配置保留在这里" : "结构化 Scenario authoring"}</p></div>{section === "world" && <div className="world-mode-tabs" role="tablist" aria-label="World 视图模式"><button type="button" className={worldMode === "edit" ? "selected" : ""} aria-pressed={worldMode === "edit"} onClick={() => setWorldMode("edit")}>对象编辑</button><button type="button" className={worldMode === "topology" ? "selected" : ""} aria-pressed={worldMode === "topology"} onClick={() => setWorldMode("topology")}>拓扑概览</button></div>}</header><div className={`canvas-body${showWorldTopology ? " canvas-body-topology" : ""}`}>{showWorldTopology && <WorldGraph document={local.definition_document} />}{!showWorldTopology && selected && <TypedEntityEditor entity={selected} document={local.definition_document} focusPath={editorFocusPath} onChange={(value) => editDocument(replaceObject(local.definition_document, section, selected.key, value))} />}{!showWorldTopology && !selected && sectionValue !== null && <TypedEditor section={section} value={sectionValue} document={local.definition_document} focusPath={editorFocusPath} collectionSelection={collectionSelection} onChange={(value) => editDocument(updateSectionRoot(local.definition_document, section, value))} onCollectionChange={(value) => { if (collectionSelection) updateCollectionItem(collectionSelection, value); }} onCollectionRemove={() => { if (collectionSelection) removeCollectionItem(collectionSelection); }} />}{!showWorldTopology && !selected && section === "validation" && <ValidationPanel validation={validation} sandboxGoal={sandboxGoal} sandbox={sandbox} setSandboxGoal={setSandboxGoal} onValidate={() => void validate()} onPublish={() => void publish()} onTest={() => void testDraft()} onIssue={focusIssue} />}{!showWorldTopology && !selected && section !== "validation" && sectionValue === null && <div className="canvas-empty"><strong>从左侧选择一个对象</strong><p>选择或新建对象后，在这里编辑它的结构化字段。</p></div>}</div></section>
        <aside className={`inspector${inspectorOpen ? " is-open" : " is-collapsed"}`}><div className="inspector-heading"><h3>检查器</h3><button type="button" className="small" onClick={() => setInspectorOpen(false)}>收起</button></div>{!selected ? <p className="muted">尚未选择对象。</p> : <>{(typeof selected.value.name === "string" || typeof selected.value.term === "string") && <label>显示名称<input value={objectDisplayValue(selected.value, selected.key)} onChange={(event) => changeName(event.target.value)} /></label>}<label>稳定键<input readOnly value={selected.key} /></label><div className="button-row"><button type="button" onClick={() => void rename()}>重命名稳定键</button><button type="button" className="danger" onClick={() => void remove()}>删除</button></div><h4>被以下对象引用</h4>{usedBy.length === 0 ? <p className="muted">没有引用。</p> : usedBy.map((edge, index) => <Link key={index} to={`/scenarios/${scenarioId}/edit/${sectionForKind(edge.source.object_kind)}/${edge.source.object_key ? encodeURIComponent(edge.source.object_key) : ""}`}>{kindLabels[edge.source.object_kind] ?? edge.source.object_kind} · {edge.source.object_key ?? edge.source.field_path}</Link>)}</>}</aside>
         <aside className={`inspector inspector-new${inspectorOpen ? " is-open" : " is-collapsed"}`}><div className="inspector-heading"><div><p className="panel-kicker">DETAILS</p><h3>检查器</h3></div><button type="button" className="editor-button editor-button-ghost" onClick={() => setInspectorOpen(false)}>收起</button></div><div className="inspector-scroll">{!selected ? <div className="inspector-empty"><strong>未选择对象</strong><p className="muted">选择一个对象后查看身份、引用和危险操作。</p></div> : <><section className="inspector-section"><p className="inspector-section-title">对象身份</p>{(typeof selected.value.name === "string" || typeof selected.value.term === "string") && <label>显示名称<input value={objectDisplayValue(selected.value, selected.key)} onChange={(event) => changeName(event.target.value)} /></label>}<label>稳定键<input readOnly value={selected.key} /></label></section><section className="inspector-section"><p className="inspector-section-title">引用关系</p>{usedBy.length === 0 ? <p className="muted">没有对象引用。</p> : usedBy.map((edge, index) => <Link key={index} to={`/scenarios/${scenarioId}/edit/${sectionForKind(edge.source.object_kind)}/${edge.source.object_key ? encodeURIComponent(edge.source.object_key) : ""}`}>{kindLabels[edge.source.object_kind] ?? edge.source.object_kind} · {edge.source.object_key ?? edge.source.field_path}</Link>)}</section><section className="inspector-section inspector-danger"><p className="inspector-section-title">危险操作</p><div className="button-row"><button type="button" className="editor-button editor-button-secondary" onClick={() => void rename()}>重命名稳定键</button><button type="button" className="editor-button editor-button-danger" onClick={() => void remove()}>删除</button></div></section></>}</div></aside>
       </div>
    </section>
  </main>;
}

function ValidationPanel({ validation, sandboxGoal, sandbox, setSandboxGoal, onValidate, onPublish, onTest, onIssue }: { validation: ValidationResult | null; sandboxGoal: string; sandbox: DraftSandboxResult | null; setSandboxGoal: (value: string) => void; onValidate: () => void; onPublish: () => void; onTest: () => void; onIssue: (issue: ValidationResult["issues"][number]) => void }) {
  return <div className="validation-panel"><div className="button-row"><button onClick={onValidate}>验证当前草稿</button><button disabled={!validation?.publish_ready} onClick={onPublish}>发布不可变版本</button></div>{validation && <><h4>运行准备度</h4>{validation.readiness.map((item) => <div className={`readiness ${item.passed ? "pass" : "fail"}`} key={item.level}>{item.passed ? "✓" : "×"} {uiLabel(item.level)}</div>)}<h4>问题</h4>{validation.issues.length === 0 ? <p>没有发现问题。</p> : validation.issues.map((issue) => <article className={`issue ${issue.severity.toLowerCase()}`} role="button" tabIndex={0} onClick={() => onIssue(issue)} key={`${issue.code}:${issue.path}`}><strong>{uiLabel(issue.severity)} · {issue.code}</strong><p>{diagnosticMessage(issue.code, issue.message)}</p><code>{issue.path}</code>{issue.locator && <small>点击定位到字段</small>}</article>)}</>}
    <section className="sandbox-panel"><h4>预览/测试当前草稿</h4><p className="muted">在一次性隔离沙盒中运行，不会创建正式游戏。</p><label htmlFor="sandbox-goal">可选目标<input id="sandbox-goal" value={sandboxGoal} onChange={(event) => setSandboxGoal(event.target.value)} placeholder="输入精确版本中定义的目标别名" /></label><button onClick={onTest}>启动隔离测试</button>{sandbox && <div className={sandbox.sandbox_started ? "sandbox-result pass" : "sandbox-result fail"}><strong>{sandbox.sandbox_started ? "沙盒已启动" : "草稿无效，未启动沙盒"}</strong>{sandbox.goal_status && <p>目标解析：{uiLabel(sandbox.goal_status)}</p>}{sandbox.task && <p>任务状态：{uiLabel(sandbox.task.status)}</p>}{sandbox.issues.map((issue) => <p key={`${issue.code}:${issue.path}`}>{uiLabel(issue.severity)} · {diagnosticMessage(issue.code, issue.message)}</p>)}</div>}</section>
  </div>;
}
