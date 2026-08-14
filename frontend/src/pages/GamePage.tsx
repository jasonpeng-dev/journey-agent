import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api";
import type { PublicPlanHistory, PublicPlanHistoryStep, PublicTask, PublicTimelineEvent } from "../types";
import { errorText, resultLabel, stepDescription, uiLabel } from "../ui";

const taskTone: Record<string, string> = {
  COMPLETED: "success",
  ACTIVE: "warning",
  NEEDS_PLAYER_INPUT: "warning",
  BLOCKED_BY_PLAYER_DECISION: "danger",
  UNREACHABLE_IN_CURRENT_STATE: "danger",
  MODEL_PLAN_REJECTED: "danger",
  ABORTED: "neutral",
};
const planStatusLabel: Record<PublicPlanHistory["status"], string> = {
  EXECUTING: "执行中",
  ADJUSTED: "已调整",
  COMPLETED: "已完成",
  BLOCKED: "已阻塞",
};
const planStepMark: Record<PublicPlanHistoryStep["status"], string> = {
  PLANNED: "○",
  CURRENT: "●",
  COMPLETED: "✓",
  FAILED: "✕",
  CANCELLED: "–",
};
const timelinePresentation: Record<PublicTimelineEvent["kind"], { label: string; mark: string; tone: string }> = {
  TASK_STARTED: { label: "目标已接受", mark: "令", tone: "goal-received" },
  ACTION_BRIEFING: { label: "下一步行动", mark: "令", tone: "current" },
  ACTION_RESULT: { label: "行动汇报", mark: "✓", tone: "completed" },
  PLAN_UPDATED: { label: "计划调整", mark: "↻", tone: "plan-event updated" },
  APPROVAL_REQUIRED: { label: "需要玩家决定", mark: "?", tone: "current" },
  APPROVAL_APPROVED: { label: "玩家已批准", mark: "✓", tone: "completed" },
  APPROVAL_REJECTED: { label: "玩家已拒绝", mark: "✕", tone: "failed" },
  TASK_COMPLETED: { label: "目标已完成", mark: "✓", tone: "success" },
  TASK_BLOCKED: { label: "目标暂时无法推进", mark: "!", tone: "danger" },
  TASK_ABORTED: { label: "目标已放弃", mark: "·", tone: "neutral" },
};

export function Timeline({ task }: { task: PublicTask | null }) {
  if (!task) return <div className="console-welcome"><span>令</span><div><strong>任务日志已准备就绪</strong><p>下达一个高层目标后，这里会记录已经发生的玩家可见事件。</p></div></div>;
  return <div className="mission-timeline" aria-live="polite">{task.timeline.map((event) => {
    const base = timelinePresentation[event.kind];
    const presentation = event.kind === "ACTION_RESULT" && event.success === false ? { ...base, mark: "✕", tone: "failed" } : base;
    const isApproval = event.kind.startsWith("APPROVAL_");
    const eventLabel = event.kind.startsWith("PLAN_") ? "执行方案" : event.kind.startsWith("TASK_") && event.kind !== "TASK_STARTED" ? "任务状态" : isApproval ? "玩家决定" : presentation.label;
    const headline = isApproval ? stepDescription(event.title) : event.kind === "TASK_STARTED" ? event.title : event.kind.startsWith("TASK_") ? presentation.label : event.title;
    return <article className={`timeline-entry ${presentation.tone}`} key={event.id}><span className="timeline-mark">{presentation.mark}</span><div><small>{eventLabel}{event.actor_name ? ` · ${event.actor_name}` : ""}</small><strong>{headline}</strong>{event.detail && <p>{event.kind === "PLAN_UPDATED" ? "下一步" : "说明"}：{uiLabel(event.detail)}</p>}{event.result_summary && <p>{resultLabel(event.result_summary)}</p>}{event.knowledge_changes.length > 0 && <ul className="knowledge-gains">{event.knowledge_changes.map((change) => <li key={`${event.id}:${change.key}`}>{change.name}{change.value !== null ? `：${typeof change.value === "string" ? uiLabel(change.value) : String(change.value)}` : ""}</li>)}</ul>}</div></article>;
  })}</div>;
}

export function PlanHistory({ task }: { task: PublicTask }) {
  const latestId = task.plan_history.at(-1)?.id ?? null;
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(latestId ? [latestId] : []));
  useEffect(() => { setExpanded(new Set(latestId ? [latestId] : [])); }, [latestId]);
  if (!task.plan_history.length) return <p className="console-empty">尚未生成执行方案。</p>;
  return <div className="plan-history">{task.plan_history.map((plan) => {
    const open = expanded.has(plan.id);
    const title = plan.ordinal === 1 ? "初始方案" : `调整方案 ${plan.ordinal - 1}`;
    return <section className={`plan-history-card ${plan.status.toLowerCase()}`} key={plan.id}>
      <button className="plan-history-toggle" type="button" aria-expanded={open} onClick={() => setExpanded((current) => { const next = new Set(current); if (next.has(plan.id)) next.delete(plan.id); else next.add(plan.id); return next; })}>
        <span><strong>{title} · {planStatusLabel[plan.status]}</strong><small>{plan.completed_steps}/{plan.total_steps} 完成{plan.failed_step_name ? ` · ${plan.failed_step_name} 失败` : ""}</small></span><b>{open ? "收起" : "展开"}</b>
      </button>
      {open && <ol className="plan-history-steps">{plan.steps.map((step) => <li className={step.status.toLowerCase()} key={step.id}><b>{planStepMark[step.status]}</b><div><strong>{step.action_name}</strong><small>{step.assigned_actor_name}</small>{step.result_summary && <p>{resultLabel(step.result_summary)}</p>}</div></li>)}</ol>}
    </section>;
  })}</div>;
}

export function GamePage() {
  const { gameId = "" } = useParams();
  const queryClient = useQueryClient();
  const [goal, setGoal] = useState("");
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [developerToken, setDeveloperToken] = useState("");
  const play = useQuery({ queryKey: ["play", gameId], queryFn: () => api.playState(gameId) });
  const scenario = useQuery({ queryKey: ["scenario", play.data?.game.scenario_id], queryFn: () => api.scenario(play.data!.game.scenario_id), enabled: Boolean(play.data?.game.scenario_id) });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["play", gameId] });
  const submit = useMutation({ mutationFn: () => api.submitGoal(gameId, goal, crypto.randomUUID()), onSuccess: (result) => { if (result.status === "ACCEPTED") setGoal(""); void refresh(); } });
  const abandon = useMutation({ mutationFn: (taskId: string) => api.abandonTask(gameId, taskId), onSuccess: () => void refresh() });
  const archive = useMutation({ mutationFn: () => api.archiveGame(gameId), onSuccess: () => void refresh() });
  const decision = useMutation({ mutationFn: ({ approve, decisionId, taskVersion }: { approve: boolean; decisionId: string; taskVersion: number }) => api.decideApproval(gameId, decisionId, approve, taskVersion), onSuccess: () => void refresh() });
  const pacing = useMutation({ mutationFn: ({ phase, version }: { phase: "action" | "debrief"; version: number }) => phase === "action" ? api.acknowledgeAction(gameId, version) : api.acknowledgeDebrief(gameId, version), onSuccess: () => void refresh() });
  const developer = useQuery({ queryKey: ["developer", gameId, developerToken], queryFn: () => api.developerSnapshot(gameId, developerToken), enabled: developerOpen && Boolean(developerToken) });

  if (play.isLoading || !play.data) return <main className="page"><p>正在加载游戏状态……</p></main>;
  const { game, current_task: task } = play.data;
  const busy = submit.isPending || abandon.isPending || archive.isPending || decision.isPending || pacing.isPending;
  const mutationError = submit.error ?? abandon.error ?? archive.error ?? decision.error ?? pacing.error;
  const resolutionMessage = submit.data && submit.data.status !== "ACCEPTED" ? submit.data.clarification_prompt ?? "输入的目标无法映射到当前精确场景版本定义的目标。" : null;

  return <main className="game-console">
    {(mutationError || resolutionMessage) && <div className="console-error"><strong>命令无法继续</strong><span>{mutationError ? errorText(mutationError) : resolutionMessage}</span></div>}
    <section className="scenario-strip"><div><span>场景</span><strong>{scenario.data?.name ?? "正在加载……"}</strong></div><div><span>实例</span><strong>{game.id.slice(0, 8)}</strong></div><div><span>精确版本</span><strong>版本 {game.scenario_version_number} · {game.scenario_content_hash.slice(0, 10)}</strong></div><div><span>运行状态</span><strong className={`console-pill ${game.status === "ACTIVE" ? "success" : "neutral"}`}>{uiLabel(game.status)}</strong></div></section>
    <section className="command-grid">
      <aside className="command-panel world-panel"><header className="command-panel-heading"><div><p>01 · 世界</p><h1>已知世界</h1></div><span className="console-pill success">玩家可见</span></header><div className="console-resource-grid">{play.data.resources.map((resource) => <article key={resource.key}><span>{resource.name.slice(0, 1)}</span><div><small>{resource.name}</small><strong>{resource.value}</strong>{resource.reserved_value > 0 && <em>已预留 {resource.reserved_value}</em>}</div></article>)}</div><div className="console-subheading"><h2>已知地点</h2><span>可见性与访问状态</span></div><div className="console-fact-list">{play.data.visible_nodes.map((node) => <div key={node.key}><div><strong>{node.name}</strong><small>{node.key}</small></div><span className={`console-pill ${node.accessible ? "success" : "neutral"}`}>{node.accessible ? "可访问" : "已锁定"}</span></div>)}</div><div className="console-subheading"><h2>参与者</h2><span>当前可见位置</span></div><div className="console-fact-list">{play.data.actors.map((actor) => <div key={actor.key}><div><strong>{actor.name}</strong><small>{actor.role_name}</small></div><span className="fact-chip">{actor.current_node_name}</span></div>)}</div><div className="console-subheading"><h2>已知事实</h2><span>不包含隐藏真相</span></div><div className="console-fact-list">{play.data.known_facts.map((fact) => <div key={`${fact.node_key}.${fact.fact_key}`}><div><strong>{fact.name}</strong><small>{fact.node_key}</small></div><span className="fact-chip">{typeof fact.value === "string" ? uiLabel(fact.value) : String(fact.value)}</span></div>)}</div></aside>
      <div className="conversation-column-v2">
        <section className="command-panel mission-log-panel"><header className="command-panel-heading"><div><p>02 · 历史</p><h1>任务执行记录</h1></div><span className="console-pill neutral">已发生事件</span></header><div className="timeline-scroll"><Timeline task={task} /></div></section>
        <section className="command-panel current-report-panel"><header className="command-panel-heading"><div><p>当前 · 汇报</p><h1>Agent 当前汇报</h1></div><span className="console-pill warning">逐步确认</span></header>
          {task?.execution_phase === "AWAITING_ACTION_ACK" && task.briefing && <section className="player-checkpoint action-briefing"><small>下一步行动</small><h2>{task.briefing.actor_name} 准备执行</h2><p><strong>{task.briefing.action_name}</strong> · 目标：{task.briefing.target_name}</p><p>{task.briefing.purpose}</p><button disabled={busy} onClick={() => pacing.mutate({ phase: "action", version: task.pacing_version })}>{pacing.isPending ? "正在执行……" : "知悉，执行"}</button></section>}
          {task?.execution_phase === "AWAITING_DEBRIEF_ACK" && task.debrief && <section className={`player-checkpoint action-debrief ${task.debrief.success ? "success" : "failed"}`}><small>行动汇报</small><h2>{task.debrief.success ? "✓" : "✕"} {task.debrief.action_name}</h2><p>{task.debrief.result_summary}</p>{task.debrief.knowledge_changes.length > 0 && <><h3>新获知情报</h3><ul>{task.debrief.knowledge_changes.map((change) => <li key={change.key}>{change.name}{change.value !== null ? `：${typeof change.value === "string" ? uiLabel(change.value) : String(change.value)}` : ""}</li>)}</ul></>}{task.debrief.plan_adjusted && <div className="debrief-replan"><strong>计划调整</strong><p>{task.debrief.plan_adjustment_summary ? `下一步准备执行「${task.debrief.plan_adjustment_summary}」。` : "Agent 已根据最新情况重新规划。"}</p></div>}<button disabled={busy} onClick={() => pacing.mutate({ phase: "debrief", version: task.pacing_version })}>收到，继续任务</button></section>}
          {play.data.pending_approval_id && task?.execution_phase === "APPROVAL_REQUIRED" && <div className="console-approval"><small>需要你的决定</small><strong>该行动超出了 Agent 的自主权限，需要你的批准。</strong><div><button disabled={busy} onClick={() => decision.mutate({ approve: true, decisionId: play.data.pending_approval_id!, taskVersion: task.version })}>批准</button><button className="danger-button" disabled={busy} onClick={() => decision.mutate({ approve: false, decisionId: play.data.pending_approval_id!, taskVersion: task.version })}>拒绝并重新规划</button></div></div>}
          {task && ["COMPLETED", "BLOCKED", "ABORTED"].includes(task.execution_phase) && <div className={`current-report-terminal ${taskTone[task.status] ?? "neutral"}`}><strong>{uiLabel(task.status)}</strong><p>{task.explanation ?? "当前任务已经结束。你可以查看完整记录。"}</p></div>}
          {game.status === "ACTIVE" && !game.active_task_id && <form className="command-composer-v2" onSubmit={(event) => { event.preventDefault(); if (goal.trim()) submit.mutate(); }}><label htmlFor="goal">下达高层目标</label><div><textarea id="goal" rows={3} value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="例如：打开北部贸易路线"/><button disabled={!goal.trim() || busy} type="submit">{submit.isPending ? "正在规划……" : "开始目标"}</button></div><p>智能体只会选择当前精确版本中定义的目标和行动；场景作者定义的内容保持其原始语言。</p></form>}
          {game.status !== "ACTIVE" && <p className="archived-notice">已归档游戏为只读状态。</p>}
        </section>
      </div>
      <aside className="command-panel execution-panel-v2"><header className="command-panel-heading"><div><p>03 · 计划</p><h1>计划演进</h1></div></header>{!task && <p className="console-empty">等待下达第一个目标。</p>}{task && <div className="task-brief"><small>当前目标</small><strong>{task.goal}</strong><span className={`console-pill ${taskTone[task.status] ?? "neutral"}`}>{uiLabel(task.status)}</span><p>{task.objective_names.join(" · ")}</p>{task.explanation && <code>{uiLabel(task.explanation)}</code>}</div>}{task && <PlanHistory task={task} />}{game.status === "ACTIVE" && game.active_task_id && task && <button className="console-button danger-button full" disabled={busy} onClick={() => abandon.mutate(task.id)}>放弃当前目标</button>}</aside>
    </section>
    <section className="developer-bar-v2"><div><p>开发者控制</p><span>只有输入服务端配置的凭证后，浏览器才会读取内部状态。</span></div><div><button onClick={() => setDeveloperOpen((value) => !value)}>开发者视图</button>{game.status === "ACTIVE" && <button className="danger-button" disabled={busy} onClick={() => archive.mutate()}>结束并归档游戏</button>}</div></section>
    {developerOpen && <section className="developer-panel-v2"><label>开发者凭证<input type="password" value={developerToken} onChange={(event) => setDeveloperToken(event.target.value)} /></label>{developer.error && <p className="developer-error">开发者访问被拒绝。</p>}{developer.data && <><h2>内部运行时快照</h2><pre>{JSON.stringify(developer.data, null, 2)}</pre></>}</section>}
  </main>;
}
