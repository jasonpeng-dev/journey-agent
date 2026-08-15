import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PlanHistory, Timeline } from "./pages/GamePage";
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
  timeline: [{ id: "goal", kind: "TASK_STARTED", title: "测试目标", detail: null, actor_name: null, result_summary: null, success: null, knowledge_changes: [], occurred_at: null }],
  briefing: null,
  debrief: null,
  explanation: null,
};

describe("Formal Play player projections", () => {
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
    expect(screen.queryByText(/WAIT|settle|operation/i)).not.toBeInTheDocument();
  });
});
