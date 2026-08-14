import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api";

export function NewGamePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios });
  const [scenarioId, setScenarioId] = useState(searchParams.get("scenarioId") ?? "");
  const [versionId, setVersionId] = useState(searchParams.get("versionId") ?? "");
  const versions = useQuery({ queryKey: ["versions", scenarioId], queryFn: () => api.versions(scenarioId), enabled: Boolean(scenarioId) });
  const create = useMutation({ mutationFn: () => api.createGame(versionId, crypto.randomUUID()), onSuccess: (game) => navigate(`/games/${game.id}`) });
  return <main className="page"><p className="eyebrow">精确版本运行时</p><h1>新游戏</h1>
    <div className="form-card">
      <label>场景<select value={scenarioId} onChange={(event) => { setScenarioId(event.target.value); setVersionId(""); }}><option value="">请选择场景……</option>{scenarios.data?.filter((item) => item.status === "PUBLISHED").map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>已发布版本<select value={versionId} onChange={(event) => setVersionId(event.target.value)}><option value="">请选择版本……</option>{versions.data?.map((item) => <option key={item.id} value={item.id}>版本 {item.version_number} · {item.content_hash.slice(0, 10)}</option>)}</select></label>
      <button className="primary-button" disabled={!versionId || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "正在创建……" : "创建游戏"}</button>
      {create.error && <p className="error">创建游戏失败，请确认选择的是已发布版本。</p>}
    </div>
  </main>;
}
