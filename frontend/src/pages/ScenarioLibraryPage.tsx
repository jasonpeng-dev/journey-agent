import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api";
import { uiLabel } from "../ui";

export function ScenarioLibraryPage() {
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios });
  return (
    <main className="page">
      <div className="page-heading"><div><p className="eyebrow">人工测试</p><h1>完整测试模板</h1><p className="muted">选择一个已发布模板，创建独立游戏后即可开始测试。</p></div><Link className="primary-button" to="/scenarios/new">新建场景</Link></div>
      {scenarios.isLoading && <p>正在加载场景……</p>}
      {scenarios.error && <p className="error">无法加载场景。</p>}
      <div className="scenario-list" data-testid="scenario-library-list">
        {scenarios.data?.map((scenario) => (
          <Link className="scenario-row scenario-row-link" data-testid={`scenario-row-${scenario.id}`} key={scenario.id} to={`/scenarios/${scenario.id}`}>
            <div className="scenario-row-info">
              <div className="scenario-row-heading">
                <h2>{scenario.name}</h2>
                <span className={`status ${scenario.status.toLowerCase()}`}>{uiLabel(scenario.status)}</span>
              </div>
              <div className="scenario-row-meta">
                <code>{scenario.key}</code>
                {scenario.current_published_version_number !== null && <span>已发布版本 {scenario.current_published_version_number}</span>}
                <span>当前草稿修订号：{scenario.draft_revision}</span>
              </div>
            </div>
            <span aria-hidden="true" className="scenario-row-chevron">›</span>
          </Link>
        ))}
      </div>
    </main>
  );
}
