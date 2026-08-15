import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { StructuredEditor } from "../components/StructuredEditor";
import { WorldGraph } from "../components/WorldGraph";
import { replaceObject, sectionObjects, sectionRoot, sections, updateObjectName, updateSectionRoot } from "../editor";
import { addObject, kindsBySection } from "../templates";
import type { Draft, DraftSandboxResult, ValidationResult } from "../types";
import { diagnosticMessage, errorText, kindLabels, sectionLabels, uiLabel } from "../ui";

type SaveState = "Saved" | "Editing" | "Saving" | "Conflict" | "Error";
const saveLabels: Record<SaveState, string> = { Saved: "已保存", Editing: "编辑中", Saving: "保存中", Conflict: "版本冲突", Error: "保存失败" };

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
  const [sandboxGoal, setSandboxGoal] = useState("");
  const [sandbox, setSandbox] = useState<DraftSandboxResult | null>(null);

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
      const conflict = error instanceof ApiError && error.code === "SCENARIO_DRAFT_CONFLICT";
      setSaveState(conflict ? "Conflict" : "Error");
      setMessage(conflict ? "服务器上的草稿已被更新，请重新加载后继续编辑。" : errorText(error, "草稿保存失败。"));
    },
  });

  useEffect(() => {
    if (!local || saveState !== "Editing") return;
    const timer = window.setTimeout(() => save.mutate(local), 700);
    return () => window.clearTimeout(timer);
  }, [local, save, saveState]);

  const objects = useMemo(() => local ? sectionObjects(local.definition_document, section) : [], [local, section]);
  const selected = objects.find((item) => item.key === objectKey) ?? null;
  const usedBy = refsQuery.data?.references.filter((edge) => edge.target.object_kind === selected?.kind && edge.target.object_key === selected?.key) ?? [];
  if (!local) return <main className="page"><p>正在加载草稿……</p></main>;

  const editDocument = (document: Record<string, unknown>) => { setLocal({ ...local, definition_document: document }); setSaveState("Editing"); };
  const changeName = (name: string) => { if (selected) editDocument(updateObjectName(local.definition_document, section, selected.key, name)); };
  const createObject = (kind: string) => { const added = addObject(local.definition_document, kind); editDocument(added.document); navigate(`/scenarios/${scenarioId}/edit/${section}/${added.key}`); };

  const rename = async () => {
    if (!selected) return;
    const newKey = window.prompt("请输入新的稳定键", selected.key)?.trim();
    if (!newKey || newKey === selected.key) return;
    try {
      const saved = await api.renameKey(scenarioId, local.revision, selected.kind, selected.key, newKey);
      setLocal(saved); setSaveState("Saved");
      await queryClient.invalidateQueries({ queryKey: ["references", scenarioId] });
      navigate(`/scenarios/${scenarioId}/edit/${section}/${newKey}`);
    } catch (error) { setSaveState("Error"); setMessage(errorText(error, "稳定键重命名失败。")); }
  };

  const remove = async () => {
    if (!selected || !window.confirm(`确定删除“${selected.name}”吗？被其他对象引用时系统会阻止删除。`)) return;
    try {
      const saved = await api.deleteObject(scenarioId, local.revision, selected.kind, selected.key);
      setLocal(saved); setSaveState("Saved"); navigate(`/scenarios/${scenarioId}/edit/${section}`);
    } catch (error) { setSaveState("Error"); setMessage(errorText(error, "删除失败，该对象可能仍被引用。")); }
  };

  const validate = async () => {
    if (saveState !== "Saved") { setMessage("请等待草稿保存完成后再验证。"); return; }
    try { setValidation(await api.validateDraft(scenarioId, local.revision)); setMessage(""); }
    catch (error) { setMessage(errorText(error, "草稿验证失败。")); }
  };
  const publish = async () => {
    if (!validation?.publish_ready) return;
    try { await api.publishDraft(scenarioId, local.revision, validation.content_hash); setMessage("已发布新的不可变场景版本。"); void queryClient.invalidateQueries({ queryKey: ["scenario", scenarioId] }); }
    catch (error) { setMessage(errorText(error, "发布失败。")); }
  };
  const testDraft = async () => {
    if (saveState !== "Saved") { setMessage("请等待草稿保存完成后再测试。"); return; }
    try { setSandbox(await api.testDraft(scenarioId, local.revision, sandboxGoal.trim() || null)); setMessage(""); }
    catch (error) { setMessage(errorText(error, "草稿沙箱启动失败。")); }
  };

  return <main className="editor-shell">
    <aside className="editor-nav"><Link to={`/scenarios/${scenarioId}`}>← 返回场景</Link><h2>当前草稿</h2>{sections.map((item) => <Link className={item === section ? "active" : ""} key={item} to={`/scenarios/${scenarioId}/edit/${item}`}>{sectionLabels[item]}</Link>)}</aside>
    <section className="editor-main">
      <header className="editor-heading"><div><p className="eyebrow">{sectionLabels[section]}</p><h1>{selected?.name ?? "草稿工作区"}</h1></div><span className={`save-state ${saveState.toLowerCase()}`}>{saveLabels[saveState]}</span></header>
      {message && <div className="conflict-banner"><p>{message}</p>{saveState === "Conflict" && <button onClick={() => { setLocal(null); setSaveState("Saved"); void draftQuery.refetch(); }}>重新加载服务器草稿</button>}</div>}
      <div className="editor-columns">
        <div className="object-list"><h3>对象</h3>{(kindsBySection[section] ?? []).map((kind) => <button className="small add-object" key={kind} onClick={() => createObject(kind)}>＋ {kindLabels[kind] ?? kind}</button>)}{objects.length === 0 && <p className="muted">此部分还没有带稳定键的对象。</p>}{objects.map((item) => <Link className={item.key === objectKey ? "selected" : ""} key={`${item.kind}:${item.key}`} to={`/scenarios/${scenarioId}/edit/${section}/${item.key}`}><span>{item.name}</span><code>{kindLabels[item.kind] ?? item.kind} · {item.key}</code></Link>)}</div>
        <div className="canvas"><h3>编辑工作区</h3>
          {section === "world" && !selected && <WorldGraph document={local.definition_document} />}
          {selected && <StructuredEditor value={selected.value} onChange={(value) => { if (value && typeof value === "object" && !Array.isArray(value)) editDocument(replaceObject(local.definition_document, section, selected.key, value as Record<string, unknown>)); }} />}
          {!selected && sectionRoot(local.definition_document, section) !== null && <StructuredEditor value={sectionRoot(local.definition_document, section)} onChange={(value) => editDocument(updateSectionRoot(local.definition_document, section, value))} />}
          {!selected && section === "validation" && <div className="validation-panel"><div className="button-row"><button onClick={() => void validate()}>验证当前草稿</button><button disabled={!validation?.publish_ready} onClick={() => void publish()}>发布不可变版本</button></div>{validation && <><h4>运行准备度</h4>{validation.readiness.map((item) => <div className={`readiness ${item.passed ? "pass" : "fail"}`} key={item.level}>{item.passed ? "✓" : "×"} {uiLabel(item.level)}</div>)}<h4>问题</h4>{validation.issues.length === 0 ? <p>没有发现问题。</p> : validation.issues.map((issue) => <article className={`issue ${issue.severity.toLowerCase()}`} key={`${issue.code}:${issue.path}`}><strong>{uiLabel(issue.severity)} · {issue.code}</strong><p>{diagnosticMessage(issue.code, issue.message)}</p><code>{issue.path}</code></article>)}</>}
            <section className="sandbox-panel"><h4>预览／测试当前草稿</h4><p className="muted">在一次性隔离沙箱中试运行，不会创建正式游戏。</p><label htmlFor="sandbox-goal">可选目标<input id="sandbox-goal" value={sandboxGoal} onChange={(event) => setSandboxGoal(event.target.value)} placeholder="请输入精确版本中定义的目标别名" /></label><button onClick={() => void testDraft()}>启动隔离测试</button>{sandbox && <div className={sandbox.sandbox_started ? "sandbox-result pass" : "sandbox-result fail"}><strong>{sandbox.sandbox_started ? "沙箱已启动" : "草稿无效，未启动沙箱"}</strong>{sandbox.goal_status && <p>目标解析：{uiLabel(sandbox.goal_status)}</p>}{sandbox.task && <p>任务状态：{uiLabel(sandbox.task.status)}</p>}{sandbox.issues.map((issue) => <p key={`${issue.code}:${issue.path}`}>{uiLabel(issue.severity)} · {diagnosticMessage(issue.code, issue.message)}</p>)}</div>}</section>
          </div>}
          {!selected && section !== "world" && section !== "validation" && sectionRoot(local.definition_document, section) === null && <p>请选择或新建对象，以编辑其结构化字段。</p>}
        </div>
        <aside className="inspector"><h3>检查器</h3>{!selected ? <p className="muted">尚未选择对象。</p> : <><label>显示名称<input value={selected.name} onChange={(event) => changeName(event.target.value)} /></label><label>稳定键<input readOnly value={selected.key} /></label><div className="button-row"><button onClick={() => void rename()}>重命名稳定键</button><button className="danger" onClick={() => void remove()}>删除</button></div><h4>被以下对象引用</h4>{usedBy.length === 0 ? <p className="muted">没有引用。</p> : usedBy.map((edge, index) => <Link key={index} to={`/scenarios/${scenarioId}/edit/${sectionForKind(edge.source.object_kind)}/${edge.source.object_key ?? ""}`}>{kindLabels[edge.source.object_kind] ?? edge.source.object_kind} · {edge.source.object_key ?? edge.source.field_path}</Link>)}</>}</aside>
      </div>
    </section>
  </main>;
}

function sectionForKind(kind: string): string {
  if (["node", "node_type", "resource", "interaction"].includes(kind)) return "world";
  if (["role", "actor"].includes(kind)) return "actors";
  if (kind === "action") return "actions";
  if (kind === "rule") return "rules";
  if (kind === "objective") return "objectives";
  return "overview";
}
