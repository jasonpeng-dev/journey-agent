import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";

export function NewGamePage() {
  const navigate = useNavigate();
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios });
  const [scenarioId, setScenarioId] = useState("");
  const [versionId, setVersionId] = useState("");
  const versions = useQuery({ queryKey: ["versions", scenarioId], queryFn: () => api.versions(scenarioId), enabled: Boolean(scenarioId) });
  const create = useMutation({ mutationFn: () => api.createGame(versionId, crypto.randomUUID()), onSuccess: (game) => navigate(`/games/${game.id}`) });
  return <main className="page"><p className="eyebrow">Exact-version runtime</p><h1>New Game</h1>
    <div className="form-card">
      <label>Scenario<select value={scenarioId} onChange={(event) => { setScenarioId(event.target.value); setVersionId(""); }}><option value="">Select…</option>{scenarios.data?.filter((item) => item.status === "PUBLISHED").map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>Published version<select value={versionId} onChange={(event) => setVersionId(event.target.value)}><option value="">Select…</option>{versions.data?.map((item) => <option key={item.id} value={item.id}>Version {item.version_number} · {item.content_hash.slice(0, 10)}</option>)}</select></label>
      <button className="primary-button" disabled={!versionId || create.isPending} onClick={() => create.mutate()}>Create Game</button>
      {create.error && <p className="error">{create.error.message}</p>}
    </div>
  </main>;
}
