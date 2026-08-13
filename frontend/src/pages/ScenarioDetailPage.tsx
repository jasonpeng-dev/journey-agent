import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";

export function ScenarioDetailPage() {
  const { scenarioId = "" } = useParams();
  const scenario = useQuery({ queryKey: ["scenario", scenarioId], queryFn: () => api.scenario(scenarioId) });
  if (!scenario.data) return <main className="page"><p>Loading scenario…</p></main>;
  return (
    <main className="page">
      <p className="eyebrow">Scenario</p><h1>{scenario.data.name}</h1>
      <div className="detail-card"><code>{scenario.data.key}</code><p>Status: {scenario.data.status}</p><p>Draft revision {scenario.data.draft_revision}</p></div>
      <Link className="primary-button" to={`/scenarios/${scenarioId}/edit/overview`}>Edit Current Draft</Link>
    </main>
  );
}
