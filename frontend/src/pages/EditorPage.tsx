import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { StructuredEditor } from "../components/StructuredEditor";
import { WorldGraph } from "../components/WorldGraph";
import { replaceObject, sectionObjects, sectionRoot, sections, updateObjectName, updateSectionRoot } from "../editor";
import { addObject, kindsBySection } from "../templates";
import type { Draft } from "../types";
import type { ValidationResult } from "../types";

type SaveState = "Saved" | "Editing" | "Saving" | "Conflict" | "Error";

export function EditorPage() {
  const { scenarioId = "", section = "overview", objectKey } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const draftQuery = useQuery({ queryKey: ["draft", scenarioId], queryFn: () => api.draft(scenarioId) });
  const refsQuery = useQuery({ queryKey: ["references", scenarioId], queryFn: () => api.references(scenarioId) });
  const [local, setLocal] = useState<Draft | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("Saved");
  const [message, setMessage] = useState("");
  const [validation, setValidation] = useState<ValidationResult | null>(null);

  useEffect(() => { if (draftQuery.data && !local) setLocal(draftQuery.data); }, [draftQuery.data, local]);

  const save = useMutation({
    mutationFn: (value: Draft) => api.saveDraft(scenarioId, value.revision, value.definition_document),
    onMutate: () => setSaveState("Saving"),
    onSuccess: (saved) => {
      setLocal(saved); setSaveState("Saved"); setMessage("");
      queryClient.setQueryData(["draft", scenarioId], saved);
      void queryClient.invalidateQueries({ queryKey: ["references", scenarioId] });
    },
    onError: (error) => {
      setSaveState(error instanceof ApiError && error.code === "SCENARIO_DRAFT_CONFLICT" ? "Conflict" : "Error");
      setMessage(error instanceof Error ? error.message : "Draft save failed");
    },
  });

  useEffect(() => {
    if (!local || saveState !== "Editing") return;
    const timer = window.setTimeout(() => save.mutate(local), 700);
    return () => window.clearTimeout(timer);
  }, [local, save, saveState]);

  const objects = useMemo(
    () => local ? sectionObjects(local.definition_document, section) : [],
    [local, section],
  );
  const selected = objects.find((item) => item.key === objectKey) ?? null;
  const usedBy = refsQuery.data?.references.filter(
    (edge) => edge.target.object_kind === selected?.kind && edge.target.object_key === selected?.key,
  ) ?? [];

  if (!local) return <main className="page"><p>Loading Draft…</p></main>;

  const changeName = (name: string) => {
    if (!selected) return;
    setLocal({ ...local, definition_document: updateObjectName(local.definition_document, section, selected.key, name) });
    setSaveState("Editing");
  };

  const editDocument = (document: Record<string, unknown>) => {
    setLocal({ ...local, definition_document: document });
    setSaveState("Editing");
  };

  const createObject = (kind: string) => {
    const added = addObject(local.definition_document, kind);
    editDocument(added.document);
    navigate(`/scenarios/${scenarioId}/edit/${section}/${added.key}`);
  };

  const rename = async () => {
    if (!selected) return;
    const newKey = window.prompt("New stable key", selected.key)?.trim();
    if (!newKey || newKey === selected.key) return;
    try {
      const saved = await api.renameKey(scenarioId, local.revision, selected.kind, selected.key, newKey);
      setLocal(saved); setSaveState("Saved");
      await queryClient.invalidateQueries({ queryKey: ["references", scenarioId] });
      navigate(`/scenarios/${scenarioId}/edit/${section}/${newKey}`);
    } catch (error) { setSaveState("Error"); setMessage(error instanceof Error ? error.message : "Rename failed"); }
  };

  const remove = async () => {
    if (!selected || !window.confirm(`Delete ${selected.name}?`)) return;
    try {
      const saved = await api.deleteObject(scenarioId, local.revision, selected.kind, selected.key);
      setLocal(saved); setSaveState("Saved"); navigate(`/scenarios/${scenarioId}/edit/${section}`);
    } catch (error) { setSaveState("Error"); setMessage(error instanceof Error ? error.message : "Delete failed"); }
  };

  const validate = async () => {
    if (saveState !== "Saved") { setMessage("Wait for the Draft to finish saving before validation."); return; }
    try { setValidation(await api.validateDraft(scenarioId, local.revision)); setMessage(""); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Validation failed"); }
  };

  const publish = async () => {
    if (!validation?.publish_ready) return;
    try { await api.publishDraft(scenarioId, local.revision, validation.content_hash); setMessage("Published an immutable ScenarioVersion."); void queryClient.invalidateQueries({ queryKey: ["scenario", scenarioId] }); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Publish failed"); }
  };

  return (
    <main className="editor-shell">
      <aside className="editor-nav">
        <Link to={`/scenarios/${scenarioId}`}>← Scenario</Link><h2>Current Draft</h2>
        {sections.map((item) => <Link className={item === section ? "active" : ""} key={item} to={`/scenarios/${scenarioId}/edit/${item}`}>{item.replace("-", " ")}</Link>)}
      </aside>
      <section className="editor-main">
        <header className="editor-heading"><div><p className="eyebrow">{section}</p><h1>{selected?.name ?? "Draft workspace"}</h1></div><span className={`save-state ${saveState.toLowerCase()}`}>{saveState}</span></header>
        {message && <div className="conflict-banner"><p>{message}</p>{saveState === "Conflict" && <button onClick={() => { setLocal(null); setSaveState("Saved"); void draftQuery.refetch(); }}>Reload server Draft</button>}</div>}
        <div className="editor-columns">
          <div className="object-list">
            <h3>Objects</h3>
            {(kindsBySection[section] ?? []).map((kind) => <button className="small add-object" key={kind} onClick={() => createObject(kind)}>+ {kind.replaceAll("_", " ")}</button>)}
            {objects.length === 0 && <p className="muted">This section has no keyed objects yet.</p>}
            {objects.map((item) => <Link className={item.key === objectKey ? "selected" : ""} key={`${item.kind}:${item.key}`} to={`/scenarios/${scenarioId}/edit/${section}/${item.key}`}><span>{item.name}</span><code>{item.kind} · {item.key}</code></Link>)}
          </div>
          <div className="canvas"><h3>Workspace</h3>
            {section === "world" && !selected && <WorldGraph document={local.definition_document} />}
            {selected && <StructuredEditor value={selected.value} onChange={(value) => { if (value && typeof value === "object" && !Array.isArray(value)) editDocument(replaceObject(local.definition_document, section, selected.key, value as Record<string, unknown>)); }} />}
            {!selected && sectionRoot(local.definition_document, section) !== null && <StructuredEditor value={sectionRoot(local.definition_document, section)} onChange={(value) => editDocument(updateSectionRoot(local.definition_document, section, value))} />}
            {!selected && section === "validation" && <div className="validation-panel"><div className="button-row"><button onClick={() => void validate()}>Validate Current Draft</button><button disabled={!validation?.publish_ready} onClick={() => void publish()}>Publish immutable Version</button></div>{validation && <><h4>Runtime Readiness</h4>{validation.readiness.map((item) => <div className={`readiness ${item.passed ? "pass" : "fail"}`} key={item.level}>{item.passed ? "✓" : "×"} {item.level.replaceAll("_", " ")}</div>)}<h4>Issues</h4>{validation.issues.length === 0 ? <p>No issues.</p> : validation.issues.map((issue) => <article className={`issue ${issue.severity.toLowerCase()}`} key={`${issue.code}:${issue.path}`}><strong>{issue.severity} · {issue.code}</strong><p>{issue.message}</p><code>{issue.path}</code></article>)}</>}</div>}
            {!selected && section !== "world" && section !== "validation" && sectionRoot(local.definition_document, section) === null && <p>Choose or create an object to edit its structured fields.</p>}
          </div>
          <aside className="inspector">
            <h3>Inspector</h3>
            {!selected ? <p className="muted">No object selected.</p> : <>
              <label>Display name<input value={selected.name} onChange={(event) => changeName(event.target.value)} /></label>
              <label>Stable key<input readOnly value={selected.key} /></label>
              <div className="button-row"><button onClick={() => void rename()}>Rename key</button><button className="danger" onClick={() => void remove()}>Delete</button></div>
              <h4>Used By</h4>{usedBy.length === 0 ? <p className="muted">No references.</p> : usedBy.map((edge, index) => <Link key={index} to={`/scenarios/${scenarioId}/edit/${sectionForKind(edge.source.object_kind)}/${edge.source.object_key ?? ""}`}>{edge.source.object_kind} · {edge.source.object_key ?? edge.source.field_path}</Link>)}
            </>}
          </aside>
        </div>
      </section>
    </main>
  );
}

function sectionForKind(kind: string): string {
  if (["node", "node_type", "resource", "interaction"].includes(kind)) return "world";
  if (["role", "actor"].includes(kind)) return "actors";
  if (kind === "action") return "actions";
  if (kind === "rule") return "rules";
  if (kind === "objective") return "objectives";
  return "overview";
}
