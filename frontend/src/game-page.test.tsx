import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  GoalComposer,
  KnownWorldAccordions,
  PlanHistory,
  TaskTabs,
  Timeline,
  WaitingStatus,
} from "./pages/GamePage";
import { formatDuration, operationBelongsToTask } from "./playPresentation";
import type { PublicTask } from "./types";

const task: PublicTask = {
  id: "task",
  version: 1,
  goal: "测试目标",
  status: "ACTIVE",
  execution_phase: "AWAITING_ACTION_ACK",
  pacing_version: 1,
  objective_names: ["测试目标"],
  roadmap: { stages: [] },
  plan: null,
  plan_history: [
    {
      id: "plan-1",
      ordinal: 1,
      status: "ADJUSTED",
      completed_steps: 0,
      total_steps: 2,
      failed_step_name: "旧行动",
      steps: [
        { id: "old-1", sequence: 1, action_name: "旧行动", assigned_actor_name: "甲", status: "FAILED", result_summary: "行动未完成" },
        { id: "old-2", sequence: 2, action_name: "取消行动", assigned_actor_name: "甲", status: "CANCELLED", result_summary: null },
      ],
    },
    {
      id: "plan-2",
      ordinal: 2,
      status: "EXECUTING",
      completed_steps: 0,
      total_steps: 1,
      failed_step_name: null,
      steps: [{ id: "new-1", sequence: 1, action_name: "新行动", assigned_actor_name: "乙", status: "CURRENT", result_summary: null }],
    },
  ],
  timeline: [{ id: "goal", kind: "TASK_STARTED", title: "测试目标", detail: null, actor_name: null, result_summary: null, success: null, knowledge_changes: [], occurred_at: null, duration_ms: 22102 }],
  briefing: null,
  debrief: null,
  explanation: null,
};

describe("Formal Play player projections", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("renders a standalone GameInstance goal composer", () => {
    const onGoalChange = vi.fn();
    const onSubmit = vi.fn();
    render(
      <GoalComposer
        goal="打开北部贸易路线"
        pendingGoal={null}
        resolving={false}
        startedAt={null}
        busy={false}
        onGoalChange={onGoalChange}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByTestId("goal-composer")).toBeVisible();
    expect(screen.getByText("当前 · 下达目标")).toBeVisible();
    expect(screen.getByLabelText("下达高层目标")).toHaveValue("打开北部贸易路线");
    fireEvent.click(screen.getByRole("button", { name: "开始目标" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("keeps the composer and its timer visible while Goal Resolution runs", () => {
    vi.useFakeTimers();
    const startedAt = Date.now();
    render(
      <GoalComposer
        goal="打开北部贸易路线"
        pendingGoal="打开北部贸易路线"
        resolving={true}
        startedAt={startedAt}
        busy={true}
        onGoalChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByTestId("goal-composer")).toBeVisible();
    expect(screen.getByTestId("goal-resolving-status")).toHaveTextContent("Agent 正在接收任务");
    act(() => vi.advanceTimersByTime(1250));
    expect(screen.getByTestId("goal-resolving-status")).toHaveTextContent("1s");
    vi.useRealTimers();
  });

  it("renders Knowledge sections with dynamic counts and controlled defaults", () => {
    render(
      <KnownWorldAccordions
        resources={[
          { key: "food", name: "粮食", value: 100, reserved_value: 0 },
          { key: "gold", name: "金币", value: 80, reserved_value: 10 },
        ]}
        visibleNodes={[
          { key: "capital", name: "首都议事厅", accessible: true },
          { key: "valley", name: "北部山谷", accessible: false },
        ]}
        actors={[{ key: "han", name: "韩烈", role_name: "将军", current_node_name: "首都议事厅" }]}
        knownFacts={[{ node_key: "valley", fact_key: "security", name: "山谷安全", value: "UNSAFE" }]}
      />,
    );

    expect(screen.getByText("资源 · 2")).toBeVisible();
    expect(screen.getByText("已知地点 · 2")).toBeVisible();
    expect(screen.getByText("参与者 · 1")).toBeVisible();
    expect(screen.getByText("已知事实 · 1")).toBeVisible();
    expect(within(screen.getByTestId("knowledge-accordion-resources")).getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(within(screen.getByTestId("knowledge-accordion-locations")).getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByText("粮食")).toBeVisible();
    expect(screen.queryByText("首都议事厅")).not.toBeInTheDocument();
    expect(screen.queryByText("韩烈")).not.toBeInTheDocument();
    expect(screen.queryByText("山谷安全")).not.toBeInTheDocument();

    fireEvent.click(within(screen.getByTestId("knowledge-accordion-locations")).getByRole("button"));
    expect(screen.getByText("首都议事厅")).toBeVisible();
    fireEvent.click(within(screen.getByTestId("knowledge-accordion-resources")).getByRole("button"));
    expect(screen.queryByText("粮食")).not.toBeInTheDocument();
    expect(screen.getByText("可用资源状态")).toBeVisible();
  });

  it("默认展开最新方案、折叠旧方案，并允许查看冻结历史", () => {
    render(<PlanHistory task={task} />);
    expect(screen.getByText("新行动")).toBeVisible();
    expect(screen.queryByText("旧行动", { selector: ".plan-history-steps strong" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /初始方案 · 已调整/ }));
    expect(screen.getByText("旧行动", { selector: ".plan-history-steps strong" })).toBeVisible();
    expect(screen.getByText("取消行动").closest("li")).toHaveClass("cancelled");
  });

  it("任务日志只渲染已经进入历史的安全事件", () => {
    render(<Timeline task={task} />);
    expect(screen.getByText("测试目标")).toBeVisible();
    expect(screen.getByText("· 22s")).toBeVisible();
    expect(screen.queryByText(/WAIT|settle|operation/i)).not.toBeInTheDocument();
  });

  it("计划事件只显示标题和右侧冻结耗时", () => {
    render(
      <Timeline
        task={{
          ...task,
          timeline: [
            {
              ...task.timeline[0],
              id: "plan-created",
              kind: "PLAN_CREATED",
              title: "旧的 provider 标题",
              detail: "不应显示的计划细节",
              result_summary: "不应显示的计划结果",
              duration_ms: 1200,
            },
            {
              ...task.timeline[0],
              id: "plan-updated",
              kind: "PLAN_UPDATED",
              title: "另一个旧标题",
              detail: "不应显示的调整细节",
              result_summary: "不应显示的调整结果",
              duration_ms: 2200,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("Agent 已完成计划")).toBeVisible();
    expect(screen.getByText("Agent 已重新规划")).toBeVisible();
    expect(screen.getAllByText("· 1s")).toHaveLength(1);
    expect(screen.getAllByText("· 2s")).toHaveLength(1);
    expect(screen.queryByText("不应显示的计划细节")).not.toBeInTheDocument();
    expect(screen.queryByText("不应显示的调整细节")).not.toBeInTheDocument();
    expect(screen.queryByText("不应显示的计划结果")).not.toBeInTheDocument();
    expect(screen.queryByText("不应显示的调整结果")).not.toBeInTheDocument();
  });

  it("operation transient state is scoped to its Task", () => {
    const operation = { kind: "planning" as const, taskId: "task-2", startedAt: 100 };
    expect(operationBelongsToTask(operation, "task-2")).toBe(true);
    expect(operationBelongsToTask(operation, "task-1")).toBe(false);
    expect(operationBelongsToTask(null, "task-2")).toBe(false);
    expect(formatDuration(0)).toBe("1s");
    expect(formatDuration(null)).toBeNull();
  });

  it("shows a local planning timer without requiring a backend heartbeat", () => {
    vi.useFakeTimers();
    const startedAt = Date.now();
    render(<WaitingStatus startedAt={startedAt} label="Agent 正在规划" testId="planning-status" />);
    act(() => vi.advanceTimersByTime(1250));
    expect(screen.getByTestId("planning-status")).toHaveTextContent("Agent 正在规划");
    expect(screen.getByTestId("planning-status")).toHaveTextContent("1s");
    vi.useRealTimers();
  });

  it("switches task tabs using the task history read model", () => {
    const onSelect = vi.fn();
    render(<TaskTabs tasks={[
      { id: "task-1", sequence: 1, goal: "第一个目标", status: "COMPLETED", execution_phase: "COMPLETED", created_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:01:00Z" },
      { id: "task-2", sequence: 2, goal: "第二个目标", status: "ACTIVE", execution_phase: "AWAITING_ACTION_ACK", created_at: "2026-01-01T00:02:00Z", completed_at: null },
    ]} selectedTaskId="task-2" onSelect={onSelect} />);
    expect(screen.getByTestId("task-tab-task-2")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByTestId("task-tab-task-1"));
    expect(onSelect).toHaveBeenCalledWith("task-1");
  });
});
