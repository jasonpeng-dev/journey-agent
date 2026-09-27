import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { EditorDialog } from "../components/editor/EditorDialog";
import type { RestorePreview, ScenarioDeletionImpact, ScenarioVersion } from "../types";
import { uiLabel } from "../ui";

function diffSummary(preview: RestorePreview) {
  const diff = preview.semantic_diff;
  if (preview.unchanged || diff.is_equal === true) return <p className="restore-preview-unchanged">该版本与当前草稿内容一致。</p>;
  if (!diff.comparable || diff.is_equal === null) return <p className="muted">当前草稿尚未形成完整语义，恢复候选仍会在编辑器中以工作副本展示。</p>;
  return <section className="portability-semantic-diff" aria-label="恢复语义变化摘要">
    <div className="portability-section-heading"><h3>语义变化</h3><span>{diff.total_changed_items} 项变化</span></div>
    <div className="portability-diff-level-one">{diff.section_summaries.map((section) => <div key={section.section}><strong>{section.section}</strong><span>{section.count}</span></div>)}</div>
    <details><summary>查看变化</summary><div className="portability-diff-level-two">{diff.section_summaries.flatMap((section) => section.subsections.map((subsection) => <p key={`${section.section}:${subsection.subsection}`}>{subsection.subsection}<strong>{subsection.count}</strong></p>))}</div></details>
  </section>;
}

function restoreErrorText(error: unknown) {
  if (!(error instanceof ApiError)) return "恢复预览失败，请稍后重试。";
  if (error.code === "SCENARIO_DRAFT_CONFLICT") return "当前草稿已在其他窗口更新，请重新打开恢复预览。";
  if (error.code === "SCENARIO_RESTORE_UNSUPPORTED") return "该历史版本无法安全转换为当前可编辑草稿格式。";
  return "恢复预览失败，请稍后重试。";
}

function deletionErrorText(error: unknown) {
  if (!(error instanceof ApiError)) return "删除场景失败，请稍后重试。";
  if (error.code === "SCENARIO_HAS_GAME_DEPENDENCIES") return "该场景仍被游戏实例使用。请先手动删除这些游戏实例。";
  return error.message || "删除场景失败，请稍后重试。";
}

function RestoreDialog({ scenarioName, draftRevision, version, preview, loading, error, onCancel, onPreview, onConfirm }: {
  scenarioName: string;
  draftRevision: number;
  version: ScenarioVersion;
  preview: RestorePreview | null;
  loading: boolean;
  error: unknown;
  onCancel: () => void;
  onPreview: () => void;
  onConfirm: () => void;
}) {
  return <EditorDialog titleId="restore-version-dialog-title" kicker="版本恢复预览" title={`恢复版本 v${version.version_number}`} onClose={onCancel} className="restore-version-dialog" footer={<>
    <button type="button" className="editor-button editor-button-secondary" onClick={onCancel}>取消</button>
    <button type="button" className="editor-button editor-button-primary" disabled={!preview || loading || !preview.restore_supported} onClick={onConfirm}>{preview?.unchanged ? "返回编辑器" : "载入当前草稿工作副本"}</button>
  </>}>
    <div className="restore-version-body">
      <p><strong>场景</strong> {scenarioName}</p>
      <p><strong>目标版本</strong> v{version.version_number} · {version.content_hash.slice(0, 12)}</p>
      <p><strong>当前 Draft revision</strong> r{draftRevision}</p>
      <p>将版本 v{version.version_number} 的内容载入当前草稿工作副本。</p>
      <p className="muted">此操作不会立即保存草稿，也不会创建新的已发布版本。</p>
      {loading && <p role="status">正在生成恢复预览…</p>}
      {Boolean(error) && <div className="portability-error" role="alert"><strong>{restoreErrorText(error)}</strong></div>}
      {!preview && !loading && !error && <button type="button" className="editor-button editor-button-secondary" onClick={onPreview}>生成恢复预览</button>}
      {preview && <div className="restore-preview-content">{diffSummary(preview)}<p className="muted">确认后会进入编辑器并标记为“有未保存修改”，需要手动保存。</p></div>}
    </div>
  </EditorDialog>;
}

function DeleteDialog({ scenarioName, scenarioKey, impact, loading, error, onCancel, onConfirm, onRefresh }: {
  scenarioName: string;
  scenarioKey: string;
  impact: ScenarioDeletionImpact | null;
  loading: boolean;
  error: unknown;
  onCancel: () => void;
  onConfirm: () => void;
  onRefresh: () => void;
}) {
  const games = impact?.dependent_games ?? [];
  return <EditorDialog titleId="delete-scenario-dialog-title" kicker="场景生命周期" title={`删除「${scenarioName}」？`} onClose={onCancel} className="delete-scenario-dialog" footer={<>
    <button type="button" className="editor-button editor-button-secondary" onClick={onCancel}>取消</button>
    {impact && impact.can_delete && <button type="button" className="editor-button editor-button-danger" disabled={loading} onClick={onConfirm}>永久删除当前场景</button>}
  </>}>
    <div className="delete-scenario-body">
      <p><strong>Scenario key</strong> <code>{scenarioKey}</code></p>
      {loading && <p role="status">正在检查场景依赖…</p>}
      {Boolean(error) && <div className="portability-error" role="alert"><strong>{deletionErrorText(error)}</strong><button type="button" className="editor-button editor-button-secondary" onClick={onRefresh}>重新检查依赖</button></div>}
      {impact && games.length > 0 && <><p className="delete-scenario-blocker">该场景仍被游戏实例使用。请先手动删除这些游戏实例。</p><ul className="scenario-dependent-games">{games.map((game) => <li key={game.game_id}><span><strong>{game.identifier}</strong><small>{uiLabel(game.status)} · 场景版本 v{game.scenario_version_number} · {new Date(game.created_at).toLocaleString("zh-CN")}</small></span><Link className="editor-button editor-button-secondary" to={`/games?highlight=${encodeURIComponent(game.game_id)}`} onClick={onCancel}>前往游戏</Link></li>)}</ul></>}
      {impact && impact.can_delete && <><p>此操作将永久删除整个场景及全部版本。</p><p>草稿修订号：r{impact.draft_revision} · Published Version：{impact.published_version_count} 个</p><p className="delete-scenario-warning">此操作不可撤销。</p></>}
    </div>
  </EditorDialog>;
}

export function ScenarioDetailPage() {
  const { scenarioId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const scenario = useQuery({ queryKey: ["scenario", scenarioId], queryFn: () => api.scenario(scenarioId) });
  const draft = useQuery({ queryKey: ["draft", scenarioId], queryFn: () => api.draft(scenarioId) });
  const versions = useQuery({ queryKey: ["versions", scenarioId], queryFn: () => api.versions(scenarioId) });
  const [restoreVersion, setRestoreVersion] = useState<ScenarioVersion | null>(null);
  const [restorePreview, setRestorePreview] = useState<RestorePreview | null>(null);
  const restore = useMutation({ mutationFn: (versionId: string) => api.restorePreview(scenarioId, versionId, draft.data!.revision), onSuccess: setRestorePreview });
  const [deleteOpen, setDeleteOpen] = useState(false);
  const deletion = useMutation({ mutationFn: () => api.deletionImpact(scenarioId) });
  const remove = useMutation({ mutationFn: () => api.deleteScenario(scenarioId), onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["scenarios"] }); navigate("/scenarios"); } });
  if (!scenario.data) return <main className="page"><p>正在加载场景……</p></main>;
  const latestVersion = versions.data?.find((version) => version.id === scenario.data.current_published_version_id) ?? null;
  const latestExportable = Boolean(latestVersion && scenario.data.current_published_version_id && latestVersion.schema_version === 3);
  const openRestore = (version: ScenarioVersion) => {
    setRestoreVersion(version);
    setRestorePreview(null);
    restore.reset();
  };
  const confirmRestore = () => {
    if (!restorePreview) return;
    navigate(`/scenarios/${scenarioId}/edit/overview`, { state: { restorePreview } });
    setRestoreVersion(null);
    setRestorePreview(null);
  };
  const openDelete = () => { setDeleteOpen(true); deletion.mutate(); };
  return <main className="page scenario-detail-page">
    <div className="scenario-detail-topline"><div><p className="eyebrow">场景</p><h1>{scenario.data.name}</h1></div><Link className="editor-button editor-button-secondary secondary-button navigation-away-button scenario-detail-action-button action-button-centered" to="/scenarios">返回场景库</Link></div>
    <div className="detail-card scenario-summary-card"><code>{scenario.data.key}</code><p>状态：{uiLabel(scenario.data.status)}</p><p>当前草稿修订号：{scenario.data.draft_revision}</p></div>
    <div className="scenario-detail-actions"><div className="scenario-detail-primary-actions"><Link className="editor-button editor-button-primary primary-button scenario-detail-action-button action-button-centered" to={`/scenarios/${scenarioId}/edit/overview`}>编辑当前草稿</Link><Link className="editor-button editor-button-primary primary-button scenario-detail-action-button action-button-centered" data-testid="presentation-settings-link" to={`/scenarios/${scenarioId}/presentation`}>界面设置</Link>{latestExportable ? <a className="editor-button editor-button-primary primary-button scenario-detail-action-button action-button-centered" data-testid="latest-artifact-export" href={`/api/v1/scenarios/${scenarioId}/latest/artifact`} download>导出最新版本</a> : <button type="button" className="editor-button editor-button-secondary secondary-button scenario-detail-action-button action-button-centered" disabled title={latestVersion ? "此版本使用旧 Scenario schema，当前 .scenario.json 格式不支持导出。" : "暂无已发布版本"}>导出最新版本</button>}</div><button type="button" className="editor-button editor-button-danger danger-button scenario-detail-action-button action-button-centered" data-testid="delete-scenario" onClick={openDelete}>删除当前场景</button></div>
    <h2 className="section-title">版本历史</h2>
    <div className="version-list">{versions.data?.map((version) => <article className="version-card" key={version.id}><div className="version-card-meta"><strong>版本 {version.version_number}</strong><code>{version.content_hash.slice(0, 12)}</code><time>{new Date(version.published_at).toLocaleString("zh-CN")}</time><span>schema v{version.schema_version}</span></div><div className="version-card-actions"><button type="button" className="editor-button editor-button-primary" disabled={restore.isPending} onClick={() => openRestore(version)}>恢复到当前草稿</button>{version.schema_version === 3 ? <a className="editor-button editor-button-primary" data-testid={`version-artifact-export-${version.version_number}`} href={`/api/v1/scenarios/${scenarioId}/versions/${version.id}/artifact`} download>导出该版本</a> : <button className="editor-button editor-button-secondary" type="button" disabled title="此版本使用旧 Scenario schema，当前 .scenario.json 格式不支持导出。">不支持当前格式导出</button>}</div></article>)}</div>
    {restoreVersion && <RestoreDialog scenarioName={scenario.data.name} draftRevision={draft.data?.revision ?? scenario.data.draft_revision} version={restoreVersion} preview={restorePreview} loading={restore.isPending} error={restore.error} onCancel={() => { setRestoreVersion(null); setRestorePreview(null); restore.reset(); }} onPreview={() => { if (draft.data) restore.mutate(restoreVersion.id); }} onConfirm={confirmRestore} />}
    {deleteOpen && <DeleteDialog scenarioName={scenario.data.name} scenarioKey={scenario.data.key} impact={deletion.data ?? null} loading={deletion.isPending || remove.isPending} error={deletion.error ?? remove.error} onCancel={() => { setDeleteOpen(false); deletion.reset(); remove.reset(); }} onRefresh={() => deletion.mutate()} onConfirm={() => remove.mutate()} />}
  </main>;
}
