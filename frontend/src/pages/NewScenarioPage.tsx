import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";

export function NewScenarioPage() {
  const navigate = useNavigate();
  const examples = useQuery({ queryKey: ["examples"], queryFn: api.examples });
  const [mode, setMode] = useState("BLANK"); const [key, setKey] = useState(""); const [name, setName] = useState(""); const [exampleKey, setExampleKey] = useState("medical_emergency");
  const create = useMutation({ mutationFn: () => api.createScenario({ mode, key, name, ...(mode === "EXAMPLE" ? { example_key: exampleKey } : {}) }), onSuccess: (scenario) => navigate(`/scenarios/${scenario.id}/edit/overview`) });
  return <main className="page"><p className="eyebrow">Authoring</p><h1>New Scenario</h1><div className="form-card">
    <label>Starting point<select value={mode} onChange={(event) => setMode(event.target.value)}><option>BLANK</option><option>EXAMPLE</option></select></label>
    {mode === "EXAMPLE" && <label>Example<select value={exampleKey} onChange={(event) => setExampleKey(event.target.value)}>{examples.data?.map((item) => <option key={item.key} value={item.key}>{item.name} · {item.maturity}</option>)}</select></label>}
    <label>Stable key<input value={key} onChange={(event) => setKey(event.target.value)} /></label><label>Name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
    <button className="primary-button" disabled={!key || !name || create.isPending} onClick={() => create.mutate()}>Create Current Draft</button>{create.error && <p className="error">{create.error.message}</p>}
  </div></main>;
}
