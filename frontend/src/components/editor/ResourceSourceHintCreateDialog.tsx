import { useState } from "react";
import { Link } from "react-router-dom";

import type { JsonObject } from "../../editor";
import { referenceOptions, type ReferenceOption } from "./ReferencePicker";
import { MultiValuePicker, OptionSelect } from "./FormPrimitives";
import { EditorDialog } from "./EditorDialog";

type ResourceSourceHintDraft = {
  resource_key: string;
  primary_region_key: string | null;
  candidate_region_keys: string[];
};

type Props = {
  document: JsonObject;
  scenarioId: string;
  onCancel: () => void;
  onCreate: (value: ResourceSourceHintDraft) => void;
};

function object(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function objects(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function availableResources(document: JsonObject): ReferenceOption[] {
  const knowledge = object(document.public_knowledge);
  const used = new Set(objects(knowledge.resource_source_hints).flatMap((item) => typeof item.resource_key === "string" ? [item.resource_key] : []));
  return referenceOptions(document, "resource").filter((option) => !used.has(option.key));
}

function regionOptions(document: JsonObject): ReferenceOption[] {
  const metadata = object(document.metadata);
  const locality = object(metadata.locality);
  const regionTypeKey = typeof locality.region_node_type_key === "string" ? locality.region_node_type_key : null;
  const world = object(document.world);
  const regionKeys = new Set(objects(world.nodes).flatMap((node) => {
    if (typeof node.key !== "string") return [];
    return !regionTypeKey || node.node_type_key === regionTypeKey ? [node.key] : [];
  }));
  return referenceOptions(document, "node").filter((option) => regionKeys.has(option.key));
}

export function ResourceSourceHintCreateDialog({ document, scenarioId, onCancel, onCreate }: Props) {
  const resources = availableResources(document);
  const allResources = referenceOptions(document, "resource");
  const regions = regionOptions(document);
  const [resourceKey, setResourceKey] = useState("");
  const [primaryRegionKey, setPrimaryRegionKey] = useState("");
  const [candidateRegionKeys, setCandidateRegionKeys] = useState<string[]>([]);
  const formId = "resource-source-hint-create-form";
  const noResources = resources.length === 0;

  return <EditorDialog
    titleId="resource-source-hint-create-title"
    kicker="资源来源提示"
    title="新增资源来源提示"
    onClose={onCancel}
    footer={<><button type="button" className="editor-button editor-button-secondary" onClick={onCancel}>取消</button><button type="submit" form={formId} className="editor-button editor-button-primary" disabled={!resourceKey}>创建</button></>}
  >
    <form id={formId} className="typed-grid" onSubmit={(event) => {
      event.preventDefault();
      if (!resourceKey) return;
      onCreate({ resource_key: resourceKey, primary_region_key: primaryRegionKey || null, candidate_region_keys: candidateRegionKeys });
    }}>
      {noResources ? <div className="collection-empty-state form-field-full"><strong>{allResources.length === 0 ? "暂无可选资源" : "所有资源均已有来源提示"}</strong><p>资源只能在“世界模型 → 资源定义”中创建和维护。</p><Link className="editor-button editor-button-secondary" to={`/scenarios/${scenarioId}/edit/resources`} onClick={onCancel}>前往资源定义</Link></div> : <>
        <OptionSelect value={resourceKey} onChange={setResourceKey} options={resources} path="resource_source_hint.create.resource_key" label="资源 *" placeholder="选择已有资源…" error={!resourceKey ? "请选择资源。" : undefined} />
        <OptionSelect value={primaryRegionKey} onChange={(next) => { setPrimaryRegionKey(next); setCandidateRegionKeys((current) => current.filter((key) => key !== next)); }} options={regions} path="resource_source_hint.create.primary_region_key" label="主要区域" placeholder="未选择（可选）" />
        <div className="form-field-full"><MultiValuePicker value={candidateRegionKeys} onChange={setCandidateRegionKeys} options={regions.filter((option) => option.key !== primaryRegionKey)} path="resource_source_hint.create.candidate_region_keys" label="候选区域" /></div>
      </>}
    </form>
  </EditorDialog>;
}
