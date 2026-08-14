import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { uiLabel } from "../ui";

export function ScenarioLibraryPage() {
  const navigate = useNavigate();
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios });
  const startTest = useMutation({
    mutationFn: (versionId: string) => api.createGame(versionId, crypto.randomUUID()),
    onSuccess: (game) => navigate(`/games/${game.id}`),
  });
  return (
    <main className="page">
      <div className="page-heading"><div><p className="eyebrow">人工测试</p><h1>完整测试模板</h1><p className="muted">选择一个已发布模板，创建独立游戏后即可开始测试。</p></div></div>
      {scenarios.isLoading && <p>正在加载场景……</p>}
      {scenarios.error && <p className="error">无法加载场景。</p>}
      <div className="card-grid">
        {scenarios.data?.map((scenario) => (
          <article className="scenario-card" key={scenario.id}>
            <span className={`status ${scenario.status.toLowerCase()}`}>{uiLabel(scenario.status)}</span>
            <h2><Link to={`/scenarios/${scenario.id}`}>{scenario.name}</Link></h2><code>{scenario.key}</code>
            <p>已发布完整模板 · 当前草稿修订号：{scenario.draft_revision}</p>
            <div className="game-card-actions">
              <Link className="secondary-button" to={`/scenarios/${scenario.id}`}>查看场景</Link>
              {scenario.current_published_version_id && <button className="primary-button" disabled={startTest.isPending} onClick={() => startTest.mutate(scenario.current_published_version_id!)}>{startTest.isPending ? "正在创建…" : "直接开始测试"}</button>}
            </div>
          </article>
        ))}
      </div>
      {startTest.error && <p className="error">无法创建测试游戏，请确认 backend 正在运行。</p>}
    </main>
  );
}
