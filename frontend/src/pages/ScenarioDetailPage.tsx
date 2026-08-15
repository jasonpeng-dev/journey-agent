import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { uiLabel } from "../ui";

export function ScenarioDetailPage() {
  const { scenarioId = "" } = useParams();
  const queryClient = useQueryClient();
  const scenario = useQuery({ queryKey: ["scenario", scenarioId], queryFn: () => api.scenario(scenarioId) });
  const draft = useQuery({ queryKey: ["draft", scenarioId], queryFn: () => api.draft(scenarioId) });
  const versions = useQuery({ queryKey: ["versions", scenarioId], queryFn: () => api.versions(scenarioId) });
  const restore = useMutation({ mutationFn: (versionId: string) => api.restoreVersion(scenarioId, draft.data!.revision, versionId), onSuccess: (saved) => { queryClient.setQueryData(["draft", scenarioId], saved); void queryClient.invalidateQueries({ queryKey: ["scenario", scenarioId] }); } });
  if (!scenario.data) return <main className="page"><p>正在加载场景……</p></main>;
  return (
    <main className="page">
      <p className="eyebrow">场景</p><h1>{scenario.data.name}</h1>
      <div className="detail-card"><code>{scenario.data.key}</code><p>状态：{uiLabel(scenario.data.status)}</p><p>当前草稿修订号：{scenario.data.draft_revision}</p></div>
      <Link className="primary-button" to={`/scenarios/${scenarioId}/edit/overview`}>编辑当前草稿</Link>
      <h2 className="section-title">版本历史</h2>
      <div className="version-list">{versions.data?.map((version) => <article key={version.id}><div><strong>版本 {version.version_number}</strong><code>{version.content_hash.slice(0, 12)}</code><time>{new Date(version.published_at).toLocaleString("zh-CN")}</time></div><div><button disabled={restore.isPending} onClick={() => restore.mutate(version.id)}>恢复到当前草稿</button><Link className="small" to="/games/new">用此版本开局</Link></div></article>)}</div>
    </main>
  );
}
