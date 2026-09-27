import { useState } from "react";

import { api, ApiError } from "../api";
import type { NewScenarioArtifactPreview, ScenarioArtifactImportResult } from "../types";
import { EditorDialog } from "./editor/EditorDialog";

const MAX_ARTIFACT_BYTES = 4 * 1024 * 1024;

type Props = {
  onCancel: () => void;
  onImported?: (result: ScenarioArtifactImportResult) => void;
};

function artifactScenarioKey(value: Record<string, unknown> | null): string {
  const scenario = value?.scenario;
  return scenario && typeof scenario === "object" && !Array.isArray(scenario) && typeof (scenario as Record<string, unknown>).key === "string"
    ? String((scenario as Record<string, unknown>).key)
    : "";
}

function friendlyError(error: unknown, fallback = "导入预览失败，请检查文件后重试。") {
  if (!(error instanceof ApiError)) return fallback;
  const messages: Record<string, string> = {
    INVALID_ARTIFACT: "文件不是有效的 Scenario artifact。",
    UNSUPPORTED_ARTIFACT_TYPE: "文件类型不受支持，请选择 .scenario.json 文件。",
    UNSUPPORTED_ARTIFACT_VERSION: "文件版本不受支持。",
    UNSUPPORTED_SCENARIO_SCHEMA_VERSION: "该文件使用了当前编辑器不支持的 Scenario schema。",
    INVALID_CONTENT_TYPE: "文件内容类型不受支持。",
    ARTIFACT_INTEGRITY_MISMATCH: "文件内容校验失败，文件可能已被修改。",
    ARTIFACT_METADATA_MISMATCH: "文件元数据与场景定义不一致。",
    ARTIFACT_INPUT_LIMIT: "文件过大或结构超出导入限制。",
    INVALID_TARGET_SCENARIO_KEY: "目标场景 key 无效，请使用小写字母、数字和下划线。",
    SCENARIO_KEY_CONFLICT: "该场景 key 已存在。导入始终创建新的场景，请指定一个未使用的 key。",
    RELEASE_NOT_PUBLISHABLE: "该 Release artifact 尚未达到发布条件。",
    NO_PUBLISHED_VERSION: "暂无已发布版本。",
  };
  return messages[error.code] ?? fallback;
}

function technicalDetails(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.details == null) return null;
  try {
    return JSON.stringify(error.details, null, 2);
  } catch {
    return null;
  }
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => reject(reader.error ?? new Error("file_read_failed"));
    reader.readAsText(file);
  });
}

function contentTypeLabel(contentType: "draft" | "release") {
  return contentType === "draft" ? "草稿 artifact" : "已发布 artifact";
}

function previewValidation(preview: NewScenarioArtifactPreview) {
  const validation = preview.validation;
  if (validation.issue_count === 0) return null;
  return <section className="portability-validation-issues" aria-label="导入校验问题">
    <strong>{validation.publish_ready ? "文件可以继续导入" : "文件仍有需要处理的问题"}</strong>
    <ul>{validation.issues.slice(0, 8).map((issue, index) => <li key={`${issue.code}:${issue.path}:${index}`}><span>{issue.message}</span><small>{issue.severity === "ERROR" ? "需要处理" : "提示"}</small></li>)}</ul>
  </section>;
}

export function ScenarioArtifactImportDialog({ onCancel, onImported }: Props) {
  const [artifact, setArtifact] = useState<Record<string, unknown> | null>(null);
  const [fileName, setFileName] = useState("");
  const [targetKey, setTargetKey] = useState("");
  const [preview, setPreview] = useState<NewScenarioArtifactPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [importing, setImporting] = useState(false);

  const runPreview = async (nextArtifact: Record<string, unknown>, nextTargetKey: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.previewNewArtifact(nextArtifact, nextTargetKey.trim() || undefined);
      setPreview(result);
      setTargetKey(result.candidate_target_key);
    } catch (nextError) {
      setPreview(null);
      setError(nextError);
    } finally {
      setLoading(false);
    }
  };

  const readArtifact = async (file: File) => {
    setFileName(file.name);
    setError(null);
    setPreview(null);
    if (file.size > MAX_ARTIFACT_BYTES) {
      setArtifact(null);
      setError(new ApiError("文件过大", 422, "ARTIFACT_INPUT_LIMIT", { max_bytes: MAX_ARTIFACT_BYTES }));
      return;
    }
    if (!file.name.endsWith(".scenario.json") && file.type !== "application/json") {
      setArtifact(null);
      setError(new ApiError("文件类型不受支持", 422, "UNSUPPORTED_ARTIFACT_TYPE", {}));
      return;
    }
    try {
      const text = await readFileAsText(file);
      const parsed: unknown = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("root");
      const nextArtifact = parsed as Record<string, unknown>;
      const key = artifactScenarioKey(nextArtifact);
      setArtifact(nextArtifact);
      setTargetKey(key);
      await runPreview(nextArtifact, key);
    } catch (nextError) {
      setArtifact(null);
      setError(nextError instanceof SyntaxError ? new ApiError("文件不是有效 JSON", 422, "INVALID_ARTIFACT", {}) : nextError);
    }
  };

  const targetMatchesPreview = Boolean(preview && targetKey.trim() === preview.candidate_target_key);
  const canConfirm = Boolean(artifact && preview && targetMatchesPreview && !preview.key_conflict && preview.validation.structurally_valid && (preview.artifact.content_type === "draft" || preview.validation.publish_ready) && !loading && !importing);
  const handleConfirm = async () => {
    if (!artifact || !preview || !canConfirm) return;
    setImporting(true);
    setError(null);
    try {
      const result = await api.importNewArtifact(artifact, targetKey.trim() || undefined);
      onImported?.(result);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setImporting(false);
    }
  };

  const technical = technicalDetails(error);
  return <EditorDialog titleId="scenario-artifact-import-dialog-title" kicker="Scenario portability" title="导入场景" onClose={onCancel} className="scenario-artifact-dialog" footer={<>
    <button type="button" className="editor-button editor-button-secondary" onClick={onCancel}>取消</button>
    <button type="button" className="editor-button editor-button-primary" disabled={!canConfirm} onClick={() => void handleConfirm()}>{importing ? "正在导入…" : "确认导入"}</button>
  </>}>
    <div className="scenario-artifact-import-body">
      <p className="muted">外部 artifact 始终创建新的 Scenario，不会导入到已有草稿或合并任何历史。</p>
      <label className="portability-file-picker"><span>选择 Scenario artifact</span><input type="file" accept=".scenario.json,application/json" onChange={(event) => { const file = event.target.files?.[0]; if (file) void readArtifact(file); event.currentTarget.value = ""; }} /></label>
      {fileName && <p className="muted portability-file-name">已选择：{fileName}</p>}
      <label className="portability-target-key"><span>目标 Scenario key</span><input value={targetKey} onChange={(event) => setTargetKey(event.target.value)} placeholder="new_scenario_key" disabled={!artifact || loading || importing} /><button type="button" className="editor-button editor-button-secondary" disabled={!artifact || !targetKey.trim() || loading || importing} onClick={() => artifact && void runPreview(artifact, targetKey)}>检查目标 key</button></label>
      {Boolean(error) && <div className="portability-error" role="alert"><strong>{friendlyError(error)}</strong>{technical && <details><summary>开发者详情</summary><pre>{technical}</pre></details>}</div>}
      {loading && <p role="status">正在检查导入内容…</p>}
      {preview && <section className="portability-preview" aria-label="导入预览">
        <div className="portability-section-heading"><h3>{preview.artifact.scenario.name}</h3><span>{contentTypeLabel(preview.artifact.content_type)}</span></div>
        <p className="portability-secondary"><strong>目标 key</strong><code>{preview.candidate_target_key}</code></p>
        <p className="portability-secondary"><strong>导入后</strong>{preview.what_import_will_create.records.join("、")}</p>
        {preview.artifact.content_type === "draft" ? <p>将创建新的场景和可编辑草稿。</p> : <p>将创建新的场景、可编辑草稿和 Published Version v1；目标场景从 v1 开始，不恢复源版本号。</p>}
        {preview.key_conflict && <div className="portability-conflict" role="alert"><strong>该场景 key 已存在。导入始终创建新的场景，请指定一个未使用的 key。</strong><p>不会自动覆盖、合并或生成带后缀的 key。请修改上方目标 key 后重新检查。</p></div>}
        {previewValidation(preview)}
      </section>}
    </div>
  </EditorDialog>;
}
