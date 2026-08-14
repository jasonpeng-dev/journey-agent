import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api";
import type { PublicPlanStep, PublicTask } from "../types";

const stepMark: Record<PublicPlanStep["status"], string> = {
  COMPLETED: "✓",
  CURRENT: "●",
  PENDING: "○",
  FAILED: "×",
  BLOCKED: "!",
};

const taskTone: Record<string, string> = {
  COMPLETED: "success",
  ACTIVE: "warning",
  NEEDS_PLAYER_INPUT: "warning",
  BLOCKED_BY_PLAYER_DECISION: "danger",
  UNREACHABLE_IN_CURRENT_STATE: "danger",
  ABORTED: "neutral",
};

function shortId(value: string) {
  return value.slice(0, 8);
}

function mutationMessage(errors: Array<Error | null>): string | null {
  return errors.find((error): error is Error => error !== null)?.message ?? null;
}

function MissionTimeline({ task }: { task: PublicTask | null }) {
  if (!task) {
    return (
      <div className="console-welcome">
        <span>策</span>
        <div>
          <strong>Command chamber ready</strong>
          <p>Issue one high-level Goal. The Agent will plan, delegate, execute and replan.</p>
        </div>
      </div>
    );
  }
  const steps = task.plan?.steps ?? [];
  return (
    <div className="mission-timeline" aria-live="polite">
      <article className="timeline-entry goal-received">
        <span className="timeline-mark">令</span>
        <div><small>GOAL ACCEPTED</small><strong>{task.goal}</strong></div>
      </article>
      {steps.map((step) => (
        <article className={`timeline-entry ${step.status.toLowerCase()}`} key={step.id}>
          <span className="timeline-mark">{stepMark[step.status]}</span>
          <div>
            <small>{step.assigned_actor_name} · STEP {step.sequence}</small>
            <strong>{step.description}</strong>
            {step.result_summary && <p>{step.result_summary}</p>}
          </div>
        </article>
      ))}
      <article className={`timeline-entry task-outcome ${taskTone[task.status] ?? "neutral"}`}>
        <span className="timeline-mark">{task.status === "COMPLETED" ? "✓" : "·"}</span>
        <div><small>TASK STATUS</small><strong>{task.status.replaceAll("_", " ")}</strong></div>
      </article>
    </div>
  );
}

export function GamePage() {
  const { gameId = "" } = useParams();
  const queryClient = useQueryClient();
  const [goal, setGoal] = useState("");
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [developerToken, setDeveloperToken] = useState("");
  const play = useQuery({ queryKey: ["play", gameId], queryFn: () => api.playState(gameId) });
  const scenario = useQuery({
    queryKey: ["scenario", play.data?.game.scenario_id],
    queryFn: () => api.scenario(play.data!.game.scenario_id),
    enabled: Boolean(play.data?.game.scenario_id),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["play", gameId] });
  const submit = useMutation({
    mutationFn: () => api.submitGoal(gameId, goal, crypto.randomUUID()),
    onSuccess: (result) => {
      if (result.status === "ACCEPTED") setGoal("");
      void refresh();
    },
  });
  const abandon = useMutation({ mutationFn: (taskId: string) => api.abandonTask(gameId, taskId), onSuccess: () => { void refresh(); } });
  const archive = useMutation({ mutationFn: () => api.archiveGame(gameId), onSuccess: () => { void refresh(); } });
  const decision = useMutation({ mutationFn: ({ approve, decisionId, taskVersion }: { approve: boolean; decisionId: string; taskVersion: number }) => api.decideApproval(gameId, decisionId, approve, taskVersion), onSuccess: () => { void refresh(); } });
  const developer = useQuery({ queryKey: ["developer", gameId, developerToken], queryFn: () => api.developerSnapshot(gameId, developerToken), enabled: developerOpen && Boolean(developerToken) });

  if (play.isLoading || !play.data) return <main className="page"><p>Loading command state…</p></main>;
  const { game, current_task: task } = play.data;
  const busy = submit.isPending || abandon.isPending || archive.isPending || decision.isPending;
  const error = mutationMessage([
    submit.error instanceof Error ? submit.error : null,
    abandon.error instanceof Error ? abandon.error : null,
    archive.error instanceof Error ? archive.error : null,
    decision.error instanceof Error ? decision.error : null,
  ]);
  const resolutionMessage = submit.data && submit.data.status !== "ACCEPTED"
    ? submit.data.clarification_prompt ?? submit.data.explanation ?? "Goal must map to an Objective in this exact ScenarioVersion."
    : null;

  return (
    <main className="game-console">
      {(error || resolutionMessage) && <div className="console-error"><strong>Command could not proceed</strong><span>{error ?? resolutionMessage}</span></div>}

      <section className="scenario-strip">
        <div><span>SCENARIO</span><strong>{scenario.data?.name ?? "Loading…"}</strong></div>
        <div><span>INSTANCE</span><strong>{shortId(game.id)}</strong></div>
        <div><span>EXACT VERSION</span><strong>Version {game.scenario_version_number} · {game.scenario_content_hash.slice(0, 10)}</strong></div>
        <div><span>RUNTIME</span><strong className={`console-pill ${game.status === "ACTIVE" ? "success" : "neutral"}`}>{game.status}</strong></div>
      </section>

      <section className="command-grid">
        <aside className="command-panel world-panel">
          <header className="command-panel-heading"><div><p>01 · WORLD</p><h1>Known Domain</h1></div><span className="console-pill success">PLAYER VISIBLE</span></header>
          <div className="console-resource-grid">
            {play.data.resources.map((resource) => <article key={resource.key}><span>{resource.name.slice(0, 1)}</span><div><small>{resource.name}</small><strong>{resource.value}</strong>{resource.reserved_value > 0 && <em>{resource.reserved_value} reserved</em>}</div></article>)}
          </div>
          <div className="console-subheading"><h2>Known World</h2><span>Visibility and access</span></div>
          <div className="console-fact-list">
            {play.data.visible_nodes.map((node) => <div key={node.key}><div><strong>{node.name}</strong><small>{node.key}</small></div><span className={`console-pill ${node.accessible ? "success" : "neutral"}`}>{node.accessible ? "Accessible" : "Locked"}</span></div>)}
          </div>
          <div className="console-subheading"><h2>Known Facts</h2><span>Hidden Truth excluded</span></div>
          <div className="console-fact-list">
            {play.data.known_facts.map((fact) => <div key={`${fact.node_key}.${fact.fact_key}`}><div><strong>{fact.name}</strong><small>{fact.node_key}</small></div><span className="fact-chip">{String(fact.value)}</span></div>)}
          </div>
        </aside>

        <section className="command-panel conversation-panel-v2">
          <header className="command-panel-heading"><div><p>02 · COMMAND</p><h1>Mission Report</h1></div><span className="console-pill neutral">AUTOMATIC EXECUTION</span></header>
          <div className="timeline-scroll"><MissionTimeline task={task} /></div>

          {play.data.pending_approval_id && task && <div className="console-approval"><div><small>PLAYER DECISION REQUIRED</small><strong>The proposed action exceeds autonomous authority.</strong></div><div><button disabled={busy} onClick={() => decision.mutate({ approve: true, decisionId: play.data.pending_approval_id!, taskVersion: task.version })}>Approve &amp; Continue</button><button className="danger-button" disabled={busy} onClick={() => decision.mutate({ approve: false, decisionId: play.data.pending_approval_id!, taskVersion: task.version })}>Reject &amp; Replan</button></div></div>}

          {game.status === "ACTIVE" && !game.active_task_id && <form className="command-composer-v2" onSubmit={(event) => { event.preventDefault(); if (goal.trim()) submit.mutate(); }}><label htmlFor="goal">Issue a high-level Goal</label><div><textarea id="goal" rows={3} value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="For example: open the northern trade route"/><button disabled={!goal.trim() || busy} type="submit">{submit.isPending ? "Planning…" : "Start Goal"}</button></div><p>The Agent selects only Objectives and Actions defined by this exact Version.</p></form>}
          {game.status === "ACTIVE" && game.active_task_id && <p className="command-running">The Agent is advancing this Task until completion or a durable decision point.</p>}
          {game.status !== "ACTIVE" && <p className="archived-notice">Archived games are read-only.</p>}
        </section>

        <aside className="command-panel execution-panel-v2">
          <header className="command-panel-heading"><div><p>03 · EXECUTION</p><h1>Current Plan</h1></div>{task?.plan?.updated && <span className="console-pill warning">PLAN UPDATED</span>}</header>
          {!task && <p className="console-empty">Waiting for the first Goal.</p>}
          {task && <div className="task-brief"><small>CURRENT GOAL</small><strong>{task.goal}</strong><span className={`console-pill ${taskTone[task.status] ?? "neutral"}`}>{task.status}</span><p>{task.objective_names.join(" · ")} · {task.status}</p>{task.explanation && <code>{task.explanation}</code>}</div>}
          {task?.plan && <ol className="execution-plan">{task.plan.steps.map((step) => <li className={step.status.toLowerCase()} key={step.id}><b>{stepMark[step.status]}</b><div><strong>{step.description}</strong><small>{step.assigned_actor_name}</small>{step.result_summary && <details><summary>Result</summary><p>{step.result_summary}</p></details>}</div></li>)}</ol>}
          {game.status === "ACTIVE" && game.active_task_id && task && <button className="console-button danger-button full" disabled={busy} onClick={() => abandon.mutate(task.id)}>Abandon Current Goal</button>}
        </aside>
      </section>

      <section className="developer-bar-v2"><div><p>DEVELOPER CONTROL</p><span>Internal state is fetched only after credentialed access.</span></div><div><button onClick={() => setDeveloperOpen((value) => !value)}>Developer View</button>{game.status === "ACTIVE" && <button className="danger-button" disabled={busy} onClick={() => archive.mutate()}>End Game</button>}</div></section>
      {developerOpen && <section className="developer-panel-v2"><label>Developer credential<input type="password" value={developerToken} onChange={(event) => setDeveloperToken(event.target.value)} /></label>{developer.error && <p className="developer-error">Developer access denied.</p>}{developer.data && <><h2>Internal Runtime Snapshot</h2><pre>{JSON.stringify(developer.data, null, 2)}</pre></>}</section>}
    </main>
  );
}
