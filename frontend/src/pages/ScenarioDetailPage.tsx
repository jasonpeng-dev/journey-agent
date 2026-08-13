import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";

export function ScenarioDetailPage() {
  const { scenarioId = "" } = useParams();
  const queryClient = useQueryClient();
  const scenario = useQuery({ queryKey: ["scenario", scenarioId], queryFn: () => api.scenario(scenarioId) });
  const draft = useQuery({ queryKey: ["draft", scenarioId], queryFn: () => api.draft(scenarioId) });
  const versions = useQuery({ queryKey: ["versions", scenarioId], queryFn: () => api.versions(scenarioId) });
  const restore = useMutation({ mutationFn: (versionId: string) => api.restoreVersion(scenarioId, draft.data!.revision, versionId), onSuccess: (saved) => { queryClient.setQueryData(["draft", scenarioId], saved); void queryClient.invalidateQueries({ queryKey: ["scenario", scenarioId] }); } });
  if (!scenario.data) return <main className="page"><p>Loading scenario…</p></main>;
  return (
    <main className="page">
      <p className="eyebrow">Scenario</p><h1>{scenario.data.name}</h1>
      <div className="detail-card"><code>{scenario.data.key}</code><p>Status: {scenario.data.status}</p><p>Draft revision {scenario.data.draft_revision}</p></div>
      <Link className="primary-button" to={`/scenarios/${scenarioId}/edit/overview`}>Edit Current Draft</Link>
      <h2 className="section-title">Version History</h2><div className="version-list">{versions.data?.map((version) => <article key={version.id}><div><strong>Version {version.version_number}</strong><code>{version.content_hash.slice(0, 12)}</code><time>{new Date(version.published_at).toLocaleString()}</time></div><div><button onClick={() => restore.mutate(version.id)}>Restore to Current Draft</button><button disabled title="Games arrive in D5">Play</button></div></article>)}</div>
    </main>
  );
}
