import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { uiLabel } from "../ui";

export function NewScenarioPage() {
  const navigate = useNavigate();
  const examples = useQuery({ queryKey: ["examples"], queryFn: api.examples });
  const [mode, setMode] = useState("BLANK");
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [exampleKey, setExampleKey] = useState("");
  const selectedExampleKey = examples.data?.some((item) => item.key === exampleKey)
    ? exampleKey
    : examples.data?.[0]?.key ?? "";
  const create = useMutation({
    mutationFn: () => api.createScenario({
      mode,
      key,
      name,
      ...(mode === "EXAMPLE" ? { example_key: selectedExampleKey } : {}),
    }),
    onSuccess: (scenario) => navigate(`/scenarios/${scenario.id}/edit/overview`),
  });
  return <main className="page"><p className="eyebrow">场景创作</p><h1>新建场景</h1><div className="form-card">
    <label>创建方式<select value={mode} onChange={(event) => setMode(event.target.value)}><option value="BLANK">空白场景</option><option value="EXAMPLE">使用示例</option></select></label>
    {mode === "EXAMPLE" && <label>示例<select value={selectedExampleKey} onChange={(event) => setExampleKey(event.target.value)}>{examples.data?.map((item) => <option key={item.key} value={item.key}>{item.name} · {uiLabel(item.maturity)}</option>)}</select></label>}
    <label>稳定键（用于引用）<input value={key} onChange={(event) => setKey(event.target.value)} /></label>
    <label>场景名称<input value={name} onChange={(event) => setName(event.target.value)} /></label>
    <button className="primary-button" disabled={!key || !name || create.isPending || (mode === "EXAMPLE" && !selectedExampleKey)} onClick={() => create.mutate()}>{create.isPending ? "正在创建……" : "创建当前草稿"}</button>
    {create.error && <p className="error">创建失败，请检查稳定键格式或是否重复。</p>}
  </div></main>;
}
