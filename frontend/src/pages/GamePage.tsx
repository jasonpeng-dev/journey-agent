import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api";
import type { PublicPlanStep } from "../types";

const stepMark: Record<PublicPlanStep["status"], string> = { COMPLETED: "✓", CURRENT: "●", PENDING: "○", FAILED: "×", BLOCKED: "!" };

export function GamePage() {
  const { gameId = "" } = useParams();
  const queryClient = useQueryClient();
  const [goal, setGoal] = useState("");
  const play = useQuery({ queryKey: ["play", gameId], queryFn: () => api.playState(gameId) });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["play", gameId] });
  const submit = useMutation({ mutationFn: () => api.submitGoal(gameId, goal, crypto.randomUUID()), onSuccess: () => { setGoal(""); void refresh(); } });
  const abandon = useMutation({ mutationFn: (taskId: string) => api.abandonTask(gameId, taskId), onSuccess: () => { void refresh(); } });
  const archive = useMutation({ mutationFn: () => api.archiveGame(gameId), onSuccess: () => { void refresh(); } });
  if (!play.data) return <main className="page"><p>Loading game…</p></main>;
  const { game, current_task: task } = play.data;
  return <main className="page play-page">
    <div className="page-heading"><div><p className="eyebrow">Player view · Version {game.scenario_version_number}</p><h1>Game {game.id.slice(0, 8)}</h1></div><span className={`status ${game.status.toLowerCase()}`}>{game.status}</span></div>
    {game.status === "ACTIVE" && !game.active_task_id && <section className="goal-box"><label htmlFor="goal">What do you want to achieve?</label><div><input id="goal" value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="Enter a natural-language goal"/><button className="primary-button" disabled={!goal.trim() || submit.isPending} onClick={() => submit.mutate()}>Start Goal</button></div>{submit.data?.status !== "ACCEPTED" && submit.data && <p className="error">{submit.data.clarification_prompt ?? submit.data.explanation}</p>}</section>}
    {task && <section className="detail-card"><p className="eyebrow">Current Goal</p><h2>{task.goal}</h2><p>{task.objective_names.join(" · ")} · {task.status}</p>
      {task.plan && <><div className="plan-heading"><h3>Current Plan</h3>{task.plan.updated && <span>Plan Updated</span>}</div><ol className="plan-list">{task.plan.steps.map((step) => <li className={step.status.toLowerCase()} key={step.id}><b>{stepMark[step.status]}</b><div><span>{step.description}</span>{step.status === "CURRENT" && <small>{step.assigned_actor_name} is executing…</small>}{step.result_summary && <details><summary>Result</summary>{step.result_summary}</details>}</div></li>)}</ol></>}
      {game.status === "ACTIVE" && game.active_task_id && <button className="danger small" onClick={() => abandon.mutate(task.id)}>Abandon Current Goal</button>}
    </section>}
    <div className="play-columns"><section className="detail-card"><h2>Known World</h2>{play.data.visible_nodes.map((node) => <p key={node.key}>{node.name} · {node.accessible ? "Accessible" : "Locked"}</p>)}</section><section className="detail-card"><h2>Known Facts</h2>{play.data.known_facts.map((fact) => <p key={`${fact.node_key}.${fact.fact_key}`}>{fact.name}: <strong>{String(fact.value)}</strong></p>)}</section><section className="detail-card"><h2>Resources</h2>{play.data.resources.map((resource) => <p key={resource.key}>{resource.name}: <strong>{resource.value}</strong></p>)}</section></div>
    {game.status === "ACTIVE" ? <button className="danger small" onClick={() => archive.mutate()}>End Game</button> : <p className="muted">Archived games are read-only.</p>}
  </main>;
}
