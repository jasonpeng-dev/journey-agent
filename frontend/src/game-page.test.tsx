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
import { groupActorsByTask } from "./actorPresentation";
import {
  debriefButtonLabel,
  formatDuration,
  operationBelongsToTask,
  segmentCompletionMessage,
  syncPlayStateCaches,
} from "./playPresentation";
import type {
  PlayerGameState,
  PublicPlanStep,
  PublicTask,
  ResourceIntelligence,
} from "./types";

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

const actorFixtures: PlayerGameState["actors"] = [
  { key: "logistics", name: "应急物流一队", role_name: "应急物流队", current_node_name: "中央城区", command_reachability: "ONLINE" },
  { key: "electrical", name: "电力抢修二队", role_name: "电力抢修队", current_node_name: "中央城区", command_reachability: "ONLINE" },
  { key: "municipal", name: "市政抢修一队", role_name: "市政抢修队", current_node_name: "中央城区", command_reachability: "ONLINE" },
];

function actorStep(id: string, actor: string, status: PublicPlanStep["status"]): PublicPlanStep {
  return {
    id,
    sequence: Number(id.replace("step-", "")),
    description: id,
    assigned_actor_name: actor,
    status,
    result_summary: null,
    location: null,
  };
}

function actorBriefing(actor: string): NonNullable<PublicTask["briefing"]> {
  return {
    step_id: "step-1",
    action_name: "执行行动",
    actor_name: actor,
    target_name: "目标地点",
    purpose: "测试当前行动队伍",
    location: null,
  };
}

function actorTask(
  executionPhase: PublicTask["execution_phase"],
  steps: PublicPlanStep[],
  briefing: PublicTask["briefing"] = null,
  status = "ACTIVE",
): PublicTask {
  return {
    ...task,
    status,
    execution_phase: executionPhase,
    plan: { strategy_summary: "测试计划", updated: false, steps },
    briefing,
  };
}

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
    expect(screen.getByLabelText("自定义目标")).toHaveValue("打开北部贸易路线");
    fireEvent.click(screen.getByRole("button", { name: "开始目标" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("synchronously updates the active PLAY cache without overwriting another task history", () => {
    const state = {
      game: { active_task_id: "active-task" },
      current_task: { id: "active-task" },
    } as PlayerGameState;
    const writes: Array<{ key: readonly unknown[]; state: PlayerGameState }> = [];
    const setQueryData = (key: readonly unknown[], nextState: PlayerGameState) => {
      writes.push({ key, state: nextState });
    };

    syncPlayStateCaches(setQueryData, "game", null, state);
    expect(writes.map((item) => item.key)).toEqual([
      ["play", "game", "live"],
      ["play", "game", null],
    ]);

    writes.length = 0;
    syncPlayStateCaches(setQueryData, "game", "active-task", state);
    expect(writes.map((item) => item.key)).toEqual([
      ["play", "game", "live"],
      ["play", "game", "active-task"],
    ]);

    writes.length = 0;
    syncPlayStateCaches(setQueryData, "game", "old-task", state);
    expect(writes.map((item) => item.key)).toEqual([["play", "game", "live"]]);
  });

  it("keeps the segment-complete debrief copy and ordinary/failure labels distinct", () => {
    expect(segmentCompletionMessage(true)).toEqual({
      title: "当前方案已完成，任务目标尚未达成。",
      detail: "可以根据最新信息继续规划。",
    });
    expect(segmentCompletionMessage(false)).toBeNull();
    expect(segmentCompletionMessage(false, true)).toEqual({
      title: "最新信息使当前方案后续步骤不再有效，需要重新规划。",
      detail: "当前步骤已完成，请根据新获知识重新规划。",
    });
    expect(
      debriefButtonLabel({
        failureDebrief: false,
        segmentCompleteDebrief: false,
        planInvalidated: false,
        replanning: false,
      }),
    ).toBe("收到，继续任务");
    expect(
      debriefButtonLabel({
        failureDebrief: true,
        segmentCompleteDebrief: false,
        planInvalidated: false,
        replanning: false,
      }),
    ).toBe("没事，重新规划");
    expect(
      debriefButtonLabel({
        failureDebrief: true,
        segmentCompleteDebrief: true,
        planInvalidated: false,
        replanning: false,
      }),
    ).toBe("收到，继续规划任务");
    expect(
      debriefButtonLabel({
        failureDebrief: true,
        segmentCompleteDebrief: false,
        planInvalidated: true,
        replanning: false,
      }),
    ).toBe("收到，继续规划任务");
  });

  it("uses Scenario Objective names in the preset Goal Composer and keeps custom input separate", () => {
    const onGoalChange = vi.fn();
    const onSubmit = vi.fn();
    render(
      <GoalComposer
        goal="恢复东部应急供电网络"
        pendingGoal={null}
        resolving={false}
        startedAt={null}
        busy={false}
        objectivesLoaded
        objectives={[
          { key: "restore_power", name: "恢复东部应急供电网络" },
        ]}
        onGoalChange={onGoalChange}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByRole("combobox", { name: "选择任务" })).toBeVisible();
    expect(screen.queryByLabelText("自定义目标")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "选择任务" }), {
      target: { value: "restore_power" },
    });
    expect(onGoalChange).toHaveBeenLastCalledWith("恢复东部应急供电网络");
    fireEvent.click(screen.getByRole("button", { name: "开始目标" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByRole("combobox", { name: "选择任务" }), {
      target: { value: "__custom_goal__" },
    });
    expect(screen.getByLabelText("自定义目标")).toBeVisible();
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
        actors={[{ key: "han", name: "韩烈", role_name: "将军", current_node_name: "首都议事厅", command_reachability: "ONLINE" }]}
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

  it("renders a Region resource summary as available over known total", () => {
    const resourceIntelligence: ResourceIntelligence = {
      total_regions: 1,
      visible_region_count: 1,
      regions: {
        north: {
          region_name: "North Region",
          resource_inventory_visibility: "VISIBLE",
          resource_survey_completed: true,
          resources: {
           general_engineering_parts: {
             resource_name: "General Engineering Parts",
             known_available: 5,
             known_total: 105,
             pools: [
               {
                 pool_key: "north_emergency_engineering_stock",
                 quantity: 5,
                 facility_key: null,
                 facility_name: null,
                 availability: "AVAILABLE",
               },
               {
                 pool_key: "north_heavy_equipment_stock",
                 quantity: 50,
                 facility_key: "heavy_equipment_yard",
                 facility_name: "Heavy Equipment Yard",
                 availability: "UNAVAILABLE",
               },
             ],
           },
            known_zero: {
              resource_name: "Known Zero",
              known_available: 0,
              known_total: 0,
              pools: [],
            },
            known_twenty: {
              resource_name: "Known Twenty",
              known_available: 20,
              known_total: 20,
              pools: [],
            },
            known_five: {
              resource_name: "Known Five",
              known_available: 5,
              known_total: 5,
              pools: [],
            },
            known_eighty: {
              resource_name: "Known Eighty",
              known_available: 80,
              known_total: 80,
              pools: [],
            },
            unknown_total: {
              resource_name: "Unknown Total",
              known_available: 12,
              known_total: null,
              pools: [],
            },
         },
       },
     },
      global_resources: {},
    };

    render(
      <KnownWorldAccordions
        resources={[]}
        resourceIntelligence={resourceIntelligence}
        visibleNodes={[]}
        actors={[]}
        knownFacts={[]}
      />,
    );

    expect(
      Array.from(document.querySelectorAll(".knowledge-status-pill")).map(
        (node) => node.textContent,
      ),
    ).toEqual(["5 / 105", "0", "20", "5", "80", "12"]);
 });

  it("filters structural relations and presents meaningful relations without machine keys", () => {
    render(
      <KnownWorldAccordions
        resources={[]}
        visibleNodes={[]}
        actors={[]}
        knownFacts={[]}
        knownRelations={[
          {
            relation_key: "hospital-location",
            source_node_key: "central_hospital",
            relation_type_key: "located_in",
            target_node_key: "central_district",
            source_node_name: "中央医院",
            target_node_name: "中央城区",
          },
          {
            relation_key: "corridor-endpoint",
            source_node_key: "west_freight_corridor",
            relation_type_key: "endpoint",
            target_node_key: "west_logistics_district",
            source_node_name: "西部货运走廊",
            target_node_name: "西部物流区",
          },
          {
            relation_key: "power-link",
            source_node_key: "southeast_power_station",
            relation_type_key: "supplies_power_to",
            target_node_key: "east_substation",
            source_node_name: "东南应急发电站",
            target_node_name: "东部配电站",
          },
        ]}
      />,
    );

    expect(screen.getByTestId("knowledge-accordion-relations")).toBeVisible();
    expect(screen.queryByText("located_in")).not.toBeInTheDocument();
  });

  it("keeps the relation section and omits the global action requirement section", () => {
    render(
      <KnownWorldAccordions
        resources={[]}
        visibleNodes={[]}
        actors={[]}
        knownFacts={[]}
        knownRelations={[
          {
            relation_key: "location",
            source_node_key: "facility",
            relation_type_key: "located_in",
            target_node_key: "region",
          },
        ]}
      />,
    );

    expect(screen.queryByTestId("knowledge-accordion-relations")).not.toBeInTheDocument();
  });

  it("derives actor groups from the current plan and action without persistent Actor status", () => {
    const initial = groupActorsByTask(actorFixtures, null);
    expect(initial.map((group) => group.key)).toEqual(["idle"]);
    expect(initial[0].actors.map((actor) => actor.name)).toEqual([
      "应急物流一队",
      "电力抢修二队",
      "市政抢修一队",
    ]);

    const planned = groupActorsByTask(
      actorFixtures,
      actorTask("AWAITING_ACTION_ACK", [
        actorStep("step-1", "应急物流一队", "PENDING"),
        actorStep("step-2", "电力抢修二队", "PENDING"),
      ]),
    );
    expect(planned.map((group) => [group.key, group.actors.map((actor) => actor.name)])).toEqual([
      ["planned", ["应急物流一队", "电力抢修二队"]],
      ["idle", ["市政抢修一队"]],
    ]);

    const active = groupActorsByTask(
      actorFixtures,
      actorTask(
        "AWAITING_ACTION_ACK",
        [
          actorStep("step-1", "应急物流一队", "PENDING"),
          actorStep("step-2", "电力抢修二队", "PENDING"),
        ],
        actorBriefing("应急物流一队"),
      ),
    );
    expect(active.map((group) => [group.key, group.actors.map((actor) => actor.name)])).toEqual([
      ["active", ["应急物流一队"]],
      ["planned", ["电力抢修二队"]],
      ["idle", ["市政抢修一队"]],
    ]);

    const afterActionWithFollowUp = groupActorsByTask(
      actorFixtures,
      actorTask(
        "AWAITING_ACTION_ACK",
        [
          actorStep("step-1", "应急物流一队", "COMPLETED"),
          actorStep("step-2", "应急物流一队", "PENDING"),
          actorStep("step-3", "电力抢修二队", "PENDING"),
        ],
        actorBriefing("电力抢修二队"),
      ),
    );
    expect(afterActionWithFollowUp.map((group) => [group.key, group.actors.map((actor) => actor.name)])).toEqual([
      ["active", ["电力抢修二队"]],
      ["planned", ["应急物流一队"]],
      ["idle", ["市政抢修一队"]],
    ]);

    const afterActionWithoutFollowUp = groupActorsByTask(
      actorFixtures,
      actorTask(
        "AWAITING_ACTION_ACK",
        [
          actorStep("step-1", "应急物流一队", "COMPLETED"),
          actorStep("step-2", "电力抢修二队", "PENDING"),
        ],
        actorBriefing("电力抢修二队"),
      ),
    );
    expect(afterActionWithoutFollowUp.find((group) => group.key === "idle")?.actors.map((actor) => actor.name)).toEqual([
      "应急物流一队",
      "市政抢修一队",
    ]);

    const completed = groupActorsByTask(
      actorFixtures,
      actorTask(
        "COMPLETED",
        [
          actorStep("step-1", "应急物流一队", "COMPLETED"),
          actorStep("step-2", "电力抢修二队", "COMPLETED"),
        ],
        null,
        "COMPLETED",
      ),
    );
    expect(completed.map((group) => group.key)).toEqual(["idle"]);
    expect(completed[0].actors).toHaveLength(3);
  });

  it("renders actor groups with the latest current location pill", () => {
    render(
      <KnownWorldAccordions
        resources={[]}
        visibleNodes={[]}
        actors={actorFixtures.map((actor) =>
          actor.key === "logistics" ? { ...actor, current_node_name: "北部工业区" } : actor,
        )}
        knownFacts={[]}
        task={actorTask(
          "AWAITING_ACTION_ACK",
          [actorStep("step-1", "应急物流一队", "PENDING")],
          actorBriefing("应急物流一队"),
        )}
      />,
    );
    fireEvent.click(within(screen.getByTestId("knowledge-accordion-actors")).getByRole("button"));
    expect(screen.getByText("行动中 · 1")).toBeVisible();
    expect(screen.getByText("应急物流一队")).toBeVisible();
    expect(screen.getByText("北部工业区")).toHaveClass("knowledge-status-pill");
    expect(screen.getByText("待命中 · 2")).toBeVisible();
    expect(screen.queryByText("当前参与者")).not.toBeInTheDocument();
  });

  it("默认展开最新方案、折叠旧方案，并允许查看冻结历史", () => {
    render(<PlanHistory task={task} />);
    expect(document.querySelectorAll(".plan-history-card")).toHaveLength(2);
    expect(screen.getByText("乙 · 新行动")).toBeVisible();
    expect(screen.queryByText("甲 · 旧行动", { selector: ".plan-history-steps strong" })).not.toBeInTheDocument();
    const latestToggle = screen.getByRole("button", { name: /调整方案 1 · 执行中/ });
    expect(latestToggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(latestToggle);
    expect(latestToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("乙 · 新行动")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /初始方案 · 已调整/ }));
    expect(screen.getByText("甲 · 旧行动", { selector: ".plan-history-steps strong" })).toBeVisible();
    expect(screen.getByText("甲 · 取消行动").closest("li")).toHaveClass("cancelled");
  });

  it("任务日志只渲染已经进入历史的安全事件", () => {
    render(<Timeline task={task} />);
    expect(screen.getByText("任务已接受")).toBeVisible();
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

  it("groups spatial knowledge and reuses the action location projection", () => {
    render(
      <KnownWorldAccordions
        resources={[
          { key: "parts-central", name: "Parts", value: 0, reserved_value: 0, scope_region_key: "central", scope_region_name: "Central Region" },
          { key: "parts-north", name: "Parts", value: 0, reserved_value: 0, scope_region_key: "north", scope_region_name: "North Region" },
          { key: "parts-west", name: "Parts", value: 10, reserved_value: 0, scope_region_key: "west", scope_region_name: "West Region" },
        ]}
        visibleNodes={[
          { key: "central", name: "Central Region", accessible: true, node_type_key: "region", region_key: "central", region_name: "Central Region" },
          { key: "west", name: "West Region", accessible: true, node_type_key: "region", region_key: "west", region_name: "West Region" },
          { key: "hospital", name: "Central Hospital", accessible: true, node_type_key: "facility", region_key: "central", region_name: "Central Region" },
          { key: "corridor", name: "West Corridor", accessible: true, node_type_key: "transport", endpoint_region_keys: ["central", "west"], endpoint_region_names: ["Central Region", "West Region"] },
        ]}
        actors={[]}
        knownFacts={[{ node_key: "corridor", fact_key: "passable", name: "Passability", value: false, node_name: "West Corridor", endpoint_region_keys: ["central", "west"], endpoint_region_names: ["Central Region", "West Region"] }]}
      />,
    );
    expect(screen.getAllByText("Parts")).toHaveLength(3);
    fireEvent.click(within(screen.getByTestId("knowledge-accordion-locations")).getByRole("button"));
    const locations = within(screen.getByTestId("knowledge-accordion-locations"));
    expect(locations.getByText("Central Region")).toBeVisible();
    expect(locations.getByText("West Region")).toBeVisible();
    expect(screen.getByText("Central Hospital")).not.toBeVisible();
    fireEvent.click(locations.getByText("Central Region"));
    expect(screen.getByText("Central Hospital")).toBeVisible();
    expect(screen.getAllByText("West Corridor")).toHaveLength(2);

    cleanup();
    const location = { kind: "ROUTE", summary: "Central Region → West Region", detail: null };
    render(
      <PlanHistory
        task={{
          ...task,
          plan_history: [{ ...task.plan_history[0], steps: [{ ...task.plan_history[0].steps[0], location }] }],
        }}
      />,
    );
    expect(screen.getByText("Central Region → West Region")).toBeVisible();
  });

  it("renders only material scoped resource rows and keeps drained rows visible", () => {
    render(
      <KnownWorldAccordions
        resources={[
          { key: "parts-west", name: "Parts", value: 10, reserved_value: 0, scope_region_key: "west", scope_region_name: "West Region" },
        ]}
        visibleNodes={[]}
        actors={[]}
        knownFacts={[]}
      />,
    );
    const resources = within(screen.getByTestId("knowledge-accordion-resources"));
    expect(resources.getByText("West Region")).toBeVisible();
    expect(resources.getByText("10")).toBeVisible();
    expect(resources.getByText("10")).toHaveClass("knowledge-status-pill");
    expect(resources.queryByText("Central Region")).not.toBeInTheDocument();

    cleanup();
    render(
      <KnownWorldAccordions
        resources={[
          { key: "parts-west", name: "Parts", value: 0, reserved_value: 0, scope_region_key: "west", scope_region_name: "West Region" },
          { key: "parts-central", name: "Parts", value: 0, reserved_value: 0, scope_region_key: "central", scope_region_name: "Central Region" },
        ]}
        visibleNodes={[]}
        actors={[]}
        knownFacts={[]}
      />,
    );
    const updatedResources = within(screen.getByTestId("knowledge-accordion-resources"));
    expect(updatedResources.getByText("Central Region")).toBeVisible();
    expect(updatedResources.getAllByText("0")).toHaveLength(2);
  });

  it("flattens facts by region and uses one compact location format in history and timeline", () => {
    render(
      <KnownWorldAccordions
        resources={[]}
        visibleNodes={[]}
        actors={[]}
        knownFacts={[
          { node_key: "hospital", fact_key: "power", name: "Emergency power", value: true, node_name: "Central Hospital", region_key: "central", region_name: "Central Region" },
          { node_key: "hospital", fact_key: "water", name: "Water supply", value: false, node_name: "Central Hospital", region_key: "central", region_name: "Central Region" },
        ]}
      />,
    );
    fireEvent.click(within(screen.getByTestId("knowledge-accordion-facts")).getByRole("button"));
    const facts = within(screen.getByTestId("knowledge-accordion-facts"));
    fireEvent.click(facts.getByText("Central Region"));
    expect(facts.getAllByText("Central Hospital")).toHaveLength(2);
    expect(facts.getByText("Emergency power")).toBeVisible();
    expect(facts.getByText("Water supply")).toBeVisible();
    expect(facts.getByText("是")).toHaveClass("knowledge-status-pill");
    expect(facts.getByText("否")).toHaveClass("knowledge-status-pill");
    expect(facts.queryByText("power")).not.toBeInTheDocument();

    cleanup();
    render(
      <PlanHistory
        task={{
          ...task,
          plan_history: [{
            id: "compact-plan",
            ordinal: 1,
            status: "COMPLETED",
            completed_steps: 1,
            total_steps: 1,
            failed_step_name: null,
            steps: [{
              id: "compact-step",
              sequence: 1,
              action_name: "运输维修部件",
              assigned_actor_name: "应急物流一队",
              status: "COMPLETED",
              result_summary: "行动已完成",
              location: { kind: "ROUTE", summary: "北部工业区 → 中央城区", detail: "电力维修部件 ×10" },
            }],
          }],
        }}
      />,
    );
    expect(screen.getByText("应急物流一队 · 运输维修部件")).toBeVisible();
    expect(screen.getByText("北部工业区 → 中央城区 · 电力维修部件 ×10")).toBeVisible();
    expect(screen.getByText("应急物流一队 · 运输维修部件").closest("li")).toHaveClass("completed");
    expect(screen.queryByText("行动已完成")).not.toBeInTheDocument();

    cleanup();
    render(
      <Timeline
        task={{
          ...task,
          timeline: [{
            ...task.timeline[0],
            id: "compact-result",
            kind: "ACTION_RESULT",
            title: "运输维修部件",
            actor_name: "应急物流一队",
            result_summary: "行动已完成",
            success: true,
            location: { kind: "ROUTE", summary: "北部工业区 → 中央城区", detail: "电力维修部件 ×10" },
          }],
        }}
      />,
    );
    expect(screen.getByText("行动汇报 · 应急物流一队")).toBeVisible();
    expect(screen.getByText("运输维修部件 · 北部工业区 → 中央城区 · 电力维修部件 ×10")).toBeVisible();
    expect(screen.queryByText("行动已完成")).not.toBeInTheDocument();
  });

  it("translates known Fact states and machine values for Player presentation", () => {
    render(
      <KnownWorldAccordions
        resources={[]}
        visibleNodes={[]}
        actors={[]}
        knownFacts={[
          { node_key: "hospital", fact_key: "operational", name: "Operational", value: false, node_name: "Central Hospital" },
          { node_key: "hospital", fact_key: "power_supply", name: "Power supply", value: "UNAVAILABLE", node_name: "Central Hospital" },
          { node_key: "corridor", fact_key: "passable", name: "Passability", value: false, node_name: "West Corridor" },
          { node_key: "hospital", fact_key: "repair_profile", name: "Repair profile", value: "central_hospital", node_name: "Central Hospital" },
          { node_key: "plant", fact_key: "heavy_engineering_support_ready", name: "Heavy support", value: false, node_name: "Water Treatment Plant" },
        ]}
      />,
    );

    const facts = within(screen.getByTestId("knowledge-accordion-facts"));
    fireEvent.click(facts.getByRole("button"));
    expect(facts.getByText("运行状态")).toBeVisible();
    expect(facts.getByText("未运行")).toBeVisible();
    expect(facts.getByText("未供电")).toBeVisible();
    expect(facts.getByText("待修复")).toBeVisible();
    expect(facts.getByText("医院设施")).toBeVisible();
    expect(facts.getByText("未部署")).toBeVisible();
    expect(facts.queryByText("UNAVAILABLE")).not.toBeInTheDocument();
    expect(facts.queryByText("central_hospital")).not.toBeInTheDocument();
  });

  it("uses structured interruption metadata and separates detailed history from compact plan locations", () => {
    render(
      <PlanHistory
        task={{
          ...task,
          plan_history: [{
            id: "interrupted-plan",
            ordinal: 1,
            status: "ADJUSTED",
            completed_steps: 1,
            total_steps: 3,
            failed_step_name: null,
            interruption: {
              kind: "KNOWLEDGE_CONFLICT",
              step_id: "conflict-step",
              sequence: 2,
              step_name: "前往区域",
            },
            steps: [
              {
                id: "completed-step",
                sequence: 1,
                action_name: "检查状态",
                assigned_actor_name: "应急物流一队",
                status: "COMPLETED",
                result_summary: null,
                location: null,
              },
              {
                id: "conflict-step",
                sequence: 2,
                action_name: "前往区域",
                assigned_actor_name: "应急物流一队",
                status: "CANCELLED",
                result_summary: null,
                location: { kind: "ROUTE", summary: "中央城区 → 西部物流区", detail: null },
              },
              {
                id: "transport-step",
                sequence: 3,
                action_name: "运输维修部件",
                assigned_actor_name: "应急物流一队",
                status: "CANCELLED",
                result_summary: null,
                location: {
                  kind: "ROUTE",
                  summary: "西部物流区 → 中央城区",
                  detail: "电力维修部件 ×10",
                },
              },
            ],
          }],
        }}
      />,
    );
    expect(screen.getByRole("button", { name: /初始方案/ })).toHaveTextContent("前往区域 冲突");
    expect(screen.getByText("计划已中断")).toBeVisible();
    const conflictMarker = screen.getByText("计划已中断").closest("li");
    expect(conflictMarker).toHaveTextContent("原因：前往区域 冲突");
    expect(conflictMarker?.previousElementSibling).toHaveTextContent("检查状态");
    expect(conflictMarker?.nextElementSibling).toHaveTextContent("前往区域");
    expect(screen.getByText("西部物流区 → 中央城区 · 电力维修部件 ×10")).toBeVisible();

    cleanup();
    render(
      <PlanHistory
        task={{
          ...task,
          plan_history: [{
            id: "compact-plan",
            ordinal: 1,
            status: "EXECUTING",
            completed_steps: 0,
            total_steps: 1,
            failed_step_name: null,
            interruption: null,
            steps: [{
              id: "transport-node-step",
              sequence: 1,
              action_name: "清理交通通道",
              assigned_actor_name: "市政抢修一队",
              status: "CURRENT",
              result_summary: null,
              location: { kind: "TRANSPORT", summary: "西部货运走廊", detail: null },
            }],
          }],
        }}
      />,
    );
    expect(screen.getByText("西部货运走廊")).toBeVisible();
    expect(screen.queryByText("中央城区 ↔ 西部物流区")).not.toBeInTheDocument();
  });

  it("shows structured direct-failure and knowledge-conflict causes on replan timeline events", () => {
    const replanTimeline = (planId: string) => [{
      id: `plan:${planId}`,
      kind: "PLAN_UPDATED" as const,
      title: "旧标题",
      detail: "不使用的字符串原因",
      actor_name: null,
      result_summary: null,
      success: null,
      knowledge_changes: [],
      occurred_at: null,
    }];
    const replan = {
      id: "replan-plan",
      ordinal: 2,
      status: "EXECUTING" as const,
      completed_steps: 0,
      total_steps: 1,
      failed_step_name: null,
      steps: [{
        id: "replan-step",
        sequence: 1,
        action_name: "清理交通通道",
        assigned_actor_name: "市政抢修一队",
        status: "CURRENT" as const,
        result_summary: null,
      }],
    };

    render(
      <Timeline
        task={{
          ...task,
          plan_history: [
            {
              ...task.plan_history[0],
              id: "failure-plan",
              ordinal: 1,
              interruption: {
                kind: "FAILURE",
                step_id: "failed-step",
                sequence: 1,
                step_name: "前往区域",
              },
              failed_step_name: "前往区域",
            },
            replan,
          ],
          timeline: replanTimeline("replan-plan"),
        }}
      />,
    );
    expect(screen.getByText("Agent 已重新规划")).toBeVisible();
    expect(screen.getByText("原因：前往区域 失败")).toBeVisible();

    cleanup();
    render(
      <Timeline
        task={{
          ...task,
          plan_history: [
            {
              ...task.plan_history[0],
              id: "conflict-plan",
              ordinal: 1,
              interruption: {
                kind: "KNOWLEDGE_CONFLICT",
                step_id: "future-step",
                sequence: 2,
                step_name: "前往区域",
              },
              failed_step_name: null,
            },
            { ...replan, id: "replan-plan-2" },
          ],
          timeline: replanTimeline("replan-plan-2"),
        }}
      />,
    );
    expect(screen.getByText("原因：前往区域 冲突")).toBeVisible();
  });

  it("places a direct-failure interruption marker immediately after the failed step", () => {
    render(
      <PlanHistory
        task={{
          ...task,
          plan_history: [{
            id: "failure-plan",
            ordinal: 1,
            status: "ADJUSTED",
            completed_steps: 0,
            total_steps: 3,
            failed_step_name: "前往区域",
            interruption: {
              kind: "FAILURE",
              step_id: "failed-step",
              sequence: 1,
              step_name: "前往区域",
            },
            steps: [
              {
                id: "failed-step",
                sequence: 1,
                action_name: "前往区域",
                assigned_actor_name: "应急物流一队",
                status: "FAILED",
                result_summary: null,
              },
              {
                id: "transport-step",
                sequence: 2,
                action_name: "运输维修部件",
                assigned_actor_name: "应急物流一队",
                status: "CANCELLED",
                result_summary: null,
              },
            ],
          }],
        }}
      />,
    );
    const marker = screen.getByText("计划已中断").closest("li");
    expect(marker).toHaveTextContent("原因：前往区域 失败");
    expect(marker?.previousElementSibling).toHaveTextContent("前往区域");
    expect(marker?.nextElementSibling).toHaveTextContent("运输维修部件");
  });

  it("renders relay target subtitles in the plan history", () => {
    render(
      <PlanHistory
        task={{
          ...task,
          plan_history: [{
            id: "relay-plan",
            ordinal: 1,
            status: "EXECUTING",
            completed_steps: 0,
            total_steps: 1,
            failed_step_name: null,
            steps: [{
              id: "relay-step",
              sequence: 1,
              action_name: "Relay message",
              assigned_actor_name: "Communications Repair Team",
              subtitle: "North Industrial Area · Industrial Repair Team",
              status: "CURRENT",
              result_summary: null,
            }],
          }],
        }}
      />,
    );
    expect(screen.getByText("Communications Repair Team · Relay message")).toBeVisible();
    const subtitle = screen.getByText("North Industrial Area · Industrial Repair Team");
    expect(subtitle).toBeVisible();
    expect(subtitle.parentElement).toHaveClass("plan-step-subtitle", "action-location-line");
  });

  it("uses the same flag lifecycle mark for accepted and completed task events", () => {
    render(
      <Timeline
        task={{
          ...task,
          timeline: [
            {
              id: "accepted",
              kind: "GOAL_ACCEPTED",
              title: "任务已接受",
              detail: "恢复中央医院应急供电",
              actor_name: null,
              result_summary: null,
              success: null,
              knowledge_changes: [],
              occurred_at: null,
            },
            {
              id: "completed",
              kind: "TASK_COMPLETED",
              title: "目标已完成",
              detail: "恢复中央医院应急供电",
              actor_name: null,
              result_summary: null,
              success: null,
              knowledge_changes: [],
              occurred_at: null,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("任务已接受")).toBeVisible();
    expect(screen.getByText("目标已完成")).toBeVisible();
    expect(screen.getAllByText("任务状态")).toHaveLength(2);
    expect(screen.getAllByText("🚩")).toHaveLength(2);
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
      { id: "task-1", sequence: 1, goal: "让中央医院恢复电力", objective_names: ["恢复中央医院应急供电"], status: "COMPLETED", execution_phase: "COMPLETED", created_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:01:00Z" },
      { id: "task-2", sequence: 2, goal: "第二个目标", objective_names: ["目标二A", "目标二B"], status: "ACTIVE", execution_phase: "AWAITING_ACTION_ACK", created_at: "2026-01-01T00:02:00Z", completed_at: null },
    ]} selectedTaskId="task-2" onSelect={onSelect} />);
    expect(screen.getByTestId("task-tab-task-2")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("task-tab-task-1")).toHaveTextContent("恢复中央医院应急供电");
    expect(screen.queryByText("让中央医院恢复电力")).not.toBeInTheDocument();
    expect(screen.getByTestId("task-tab-task-2")).toHaveTextContent("目标二A · 目标二B");
    fireEvent.click(screen.getByTestId("task-tab-task-1"));
    expect(onSelect).toHaveBeenCalledWith("task-1");
  });
  it("shows a single history boundary only before new tasks", () => {
    const tasks: PlayerGameState["task_history"] = [
      { id: "task-1", sequence: 1, goal: "inherited", objective_names: ["Inherited"], status: "COMPLETED", execution_phase: "COMPLETED", created_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:01:00Z" },
      { id: "task-2", sequence: 2, goal: "new", objective_names: ["New"], status: "ACTIVE", execution_phase: "AWAITING_ACTION_ACK", created_at: "2026-01-01T00:02:00Z", completed_at: null },
    ];
    const onSelect = vi.fn();
    const view = render(<TaskTabs tasks={tasks} inheritedTaskCount={0} selectedTaskId="task-2" onSelect={onSelect} />);
    expect(screen.queryByTestId("history-boundary")).not.toBeInTheDocument();
    view.rerender(<TaskTabs tasks={tasks} inheritedTaskCount={1} selectedTaskId="task-2" onSelect={onSelect} />);
    expect(screen.getByTestId("history-boundary")).toHaveTextContent("该存档初始状态");
  });
  it("renders Knowledge-safe facility state, repair contracts, resources, and source relations", () => {
    render(
      <KnownWorldAccordions
        resources={[
          { key: "general_engineering_parts", name: "General Engineering Parts", value: 5, reserved_value: 0 },
          { key: "municipal_repair_materials", name: "Municipal Repair Materials", value: 20, reserved_value: 0 },
        ]}
        visibleNodes={[
          { key: "east", name: "East Region", accessible: true, node_type_key: "region", region_key: "east", region_name: "East Region" },
          { key: "north", name: "North Region", accessible: true, node_type_key: "region", region_key: "north", region_name: "North Region" },
          { key: "east_distribution_station", name: "East Substation", accessible: true, node_type_key: "facility", region_key: "east", region_name: "East Region" },
          {
            key: "utility_service_depot",
            name: "Utility Service Depot",
            accessible: true,
            node_type_key: "facility",
            region_key: "north",
            region_name: "North Region",
            associated_known_resources: [
              {
                resource_key: "general_engineering_parts",
                resource_name: "General Engineering Parts",
                quantity: 50,
                availability: "UNAVAILABLE",
                availability_requirement: { node_key: "utility_service_depot", fact_key: "operational", value: true },
                availability_requirement_status: "KNOWN",
              },
              {
                resource_key: "general_engineering_parts",
                resource_name: "General Engineering Parts",
                quantity: 35,
                availability: "AVAILABLE",
              },
            ],
          },
          { key: "unknown_facility", name: "Unknown Facility", accessible: true, node_type_key: "facility", region_key: "north", region_name: "North Region" },
          { key: "emergency_generator", name: "Emergency Generator", accessible: true, node_type_key: "facility", region_key: "north", region_name: "North Region" },
        ]}
        actors={[]}
        knownFacts={[
          { node_key: "east_distribution_station", fact_key: "operational", name: "Operational", value: false, node_name: "East Substation", node_type_key: "facility", region_key: "east", region_name: "East Region" },
          { node_key: "east_distribution_station", fact_key: "power_supply", name: "Power supply", value: "UNAVAILABLE", node_name: "East Substation", node_type_key: "facility", region_key: "east", region_name: "East Region" },
          { node_key: "utility_service_depot", fact_key: "operational", name: "Operational", value: false, node_name: "Utility Service Depot", node_type_key: "facility", region_key: "north", region_name: "North Region" },
          { node_key: "utility_service_depot", fact_key: "power_supply", name: "Power supply", value: "UNAVAILABLE", node_name: "Utility Service Depot", node_type_key: "facility", region_key: "north", region_name: "North Region" },
          { node_key: "utility_service_depot", fact_key: "power_generation_capable", name: "Power generation capable", value: false, node_name: "Utility Service Depot", node_type_key: "facility", region_key: "north", region_name: "North Region" },
          { node_key: "utility_service_depot", fact_key: "heavy_engineering_support", name: "Heavy engineering support", value: "UNAVAILABLE", node_name: "Utility Service Depot", node_type_key: "facility", region_key: "north", region_name: "North Region" },
          { node_key: "emergency_generator", fact_key: "power_generation_capable", name: "Power generation capable", value: true, node_name: "Emergency Generator", node_type_key: "facility", region_key: "north", region_name: "North Region" },
        ]}
        knownRelations={[
          {
            relation_key: "east-power-hospital",
            source_node_key: "east_distribution_station",
            relation_type_key: "supplies_power_to",
            target_node_key: "east_community_hospital",
            source_node_name: "East Substation",
            target_node_name: "East Community Hospital",
          },
        ]}
        knownTargetActionContracts={[
          {
            target_key: "utility_service_depot",
            action_key: "repair_industrial_facility",
            action_name: "Repair industrial facility",
            required_actor_role_name: "Industrial Repair Team",
            cost: { general_engineering_parts: 5, municipal_repair_materials: 20 },
            effects: [{ type: "FACT_MUTATION", target: "target_key", fact_key: "operational", value: true }],
          },
        ]}
      />,
    );
    fireEvent.click(within(screen.getByTestId("knowledge-accordion-locations")).getByRole("button"));
    fireEvent.click(screen.getByText("North Region"));
    const locationBefore = window.location.href;
    const utility = screen.getByTestId("facility-card-utility_service_depot");
    expect(utility).toHaveTextContent("\u672a\u4f9b\u7535");
    expect(utility).toHaveTextContent("\u5f85\u4fee\u590d");
    const utilitySummary = utility.querySelector("summary")!;
    expect(utilitySummary).not.toHaveTextContent(/\u4f9b\u7535\s+\u672a\u4f9b\u7535/);
    expect(utilitySummary).not.toHaveTextContent(/\u8bbe\u65bd\s+\u5f85\u4fee\u590d/);
    expect(utilitySummary).toHaveTextContent("+");
    expect(utilitySummary).not.toHaveTextContent("-");
    expect(utility).not.toHaveAttribute("open");
    expect(screen.queryByText("\u53ef\u8bbf\u95ee")).not.toBeInTheDocument();
    expect(screen.getByTestId("facility-card-unknown_facility")).not.toHaveTextContent("\u5f85\u4fee\u590d");
    fireEvent.click(utilitySummary);
    expect(utility).toHaveAttribute("open");
    expect(utility).not.toHaveTextContent("\u00d735");
    expect(utilitySummary).toHaveTextContent("-");
    expect(utilitySummary).not.toHaveTextContent("+");
    expect(window.location.href).toBe(locationBefore);
    expect(screen.getByTestId("knowledge-accordion-locations")).toBeInTheDocument();
    expect(utility).toHaveTextContent("通用工程部件");
    expect(utility).toHaveTextContent("×5");
    expect(utility).toHaveTextContent("×20");
    expect(utility).toHaveTextContent("修复需求：通用工程部件 ×5、市政维修材料 ×20");
    expect(utility).toHaveTextContent("执行队伍：Industrial Repair Team");
    expect(utility).toHaveTextContent("关联资源：通用工程部件 ×50，暂不可用，解锁条件：Utility Service Depot恢复运行");
    expect(utility).toHaveTextContent("重型工程支援：不可用");
    expect(utility).not.toHaveTextContent("修复效果：");
    expect(utility).not.toHaveTextContent("修复后设备正常");
    expect(utility).not.toHaveTextContent("发电能力：不具备");
    fireEvent.click(utilitySummary);
    expect(utility).not.toHaveAttribute("open");
    const unknownFacility = screen.getByTestId("facility-card-unknown_facility");
    const unknownSummary = unknownFacility.querySelector("summary")!;
    expect(unknownSummary.querySelector(".knowledge-facility-toggle")).toHaveTextContent("+");
    fireEvent.click(unknownSummary);
    expect(unknownFacility).toHaveAttribute("open");
    expect(screen.getByTestId("knowledge-accordion-locations")).toBeInTheDocument();
    fireEvent.click(unknownSummary);
    expect(unknownFacility).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("East Region"));
    const substation = screen.getByTestId("facility-card-east_distribution_station");
    const substationSummary = substation.querySelector("summary")!;
    expect(substationSummary.querySelectorAll(".knowledge-facility-status")).toHaveLength(2);
    expect(substationSummary.querySelector(".knowledge-facility-toggle")).toHaveTextContent("+");
    fireEvent.click(substationSummary);
    expect(substationSummary.querySelector(".knowledge-facility-toggle")).toHaveTextContent("-");
    expect(substation).toHaveTextContent("送电能力：未具备");
    expect(substation).toHaveTextContent("可供电：East Community Hospital");
    expect(substation).not.toHaveTextContent("发电能力：");
    const generator = screen.getByTestId("facility-card-emergency_generator");
    fireEvent.click(generator.querySelector("summary")!);
    expect(generator).toHaveTextContent("发电能力：已具备");
    expect(screen.queryByTestId("knowledge-accordion-facts")).not.toBeInTheDocument();
    expect(screen.queryByTestId("knowledge-accordion-relations")).not.toBeInTheDocument();
  });
  it("renders unknown and known Transport passability without Facility controls", () => {
    render(
      <KnownWorldAccordions
        resources={[]}
        visibleNodes={[
          { key: "north", name: "North Region", accessible: true, node_type_key: "region", region_key: "north", region_name: "North Region" },
          { key: "unknown_route", name: "North Corridor", accessible: true, node_type_key: "transport", region_key: "north", region_name: "North Region", endpoint_region_names: ["North Region", "Central Region"] },
          { key: "open_route", name: "Open Corridor", accessible: true, node_type_key: "transport", region_key: "north", region_name: "North Region", endpoint_region_names: ["North Region", "West Region"] },
          { key: "blocked_route", name: "Blocked Corridor", accessible: true, node_type_key: "transport", region_key: "north", region_name: "North Region", endpoint_region_names: ["North Region", "East Region"] },
        ]}
        actors={[]}
        knownFacts={[
          { node_key: "open_route", fact_key: "passable", name: "Passability", value: true, node_name: "Open Corridor", node_type_key: "transport", region_key: "north", region_name: "North Region" },
          { node_key: "blocked_route", fact_key: "passable", name: "Passability", value: false, node_name: "Blocked Corridor", node_type_key: "transport", region_key: "north", region_name: "North Region" },
        ]}
      />,
    );
    const locations = within(screen.getByTestId("knowledge-accordion-locations"));
    fireEvent.click(locations.getByRole("button"));
    fireEvent.click(locations.getByText("North Region"));
    const unknown = screen.getByTestId("transport-card-unknown_route");
    const open = screen.getByTestId("transport-card-open_route");
    const blocked = screen.getByTestId("transport-card-blocked_route");
    expect(unknown).toHaveTextContent("待探索");
    expect(open).toHaveTextContent("可通行");
    expect(blocked).toHaveTextContent("待修复");
    expect(unknown.querySelector("summary")).toBeNull();
    expect(open.querySelector("summary")).toBeNull();
    expect(blocked.querySelector("summary")).toBeNull();
    expect(unknown.querySelector(".knowledge-facility-toggle")).toBeNull();
    expect(unknown.querySelectorAll(".knowledge-transport-column-spacer")).toHaveLength(1);
  });
  it("keeps uncategorized known facts and relations available without empty global sections", () => {
    render(
      <KnownWorldAccordions
        resources={[]}
        visibleNodes={[]}
        actors={[]}
        knownFacts={[{ node_key: "unknown", fact_key: "security", name: "Security", value: "KNOWN", node_name: "Unknown" }]}
        knownRelations={[
          {
            relation_key: "other-relation",
            source_node_key: "unknown",
            relation_type_key: "supports",
            target_node_key: "other",
            source_node_name: "Unknown",
            target_node_name: "Other",
          },
        ]}
      />,
    );
    expect(screen.getByTestId("knowledge-accordion-facts")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-accordion-relations")).toBeInTheDocument();
  });
});
