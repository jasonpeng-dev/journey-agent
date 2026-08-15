import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api";

export function ScenarioLibraryPage() {
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios });
  return (
    <main className="page">
      <div className="page-heading"><div><p className="eyebrow">Authoring</p><h1>Scenario Library</h1></div><Link className="primary-button" to="/scenarios/new">New Scenario</Link></div>
      {scenarios.isLoading && <p>Loading scenarios…</p>}
      {scenarios.error && <p className="error">Unable to load scenarios.</p>}
      <div className="card-grid">
        {scenarios.data?.map((scenario) => (
          <Link className="scenario-card" key={scenario.id} to={`/scenarios/${scenario.id}`}>
            <span className={`status ${scenario.status.toLowerCase()}`}>{scenario.status}</span>
            <h2>{scenario.name}</h2><code>{scenario.key}</code>
            <p>Draft revision {scenario.draft_revision}</p>
          </Link>
        ))}
      </div>
    </main>
  );
}
