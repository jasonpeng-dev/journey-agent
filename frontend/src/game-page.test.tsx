import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ActionExecutionControls,
  GoalComposer,
  KnownWorldAccordions,
  MissionLogPanel,
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
  planningRefetchInterval,
  segmentCompletionMessage,
  syncPlayStateCaches,
} from "./playPresentation";
import type {
  PlayerGameState,
  PublicPlanStep,
  PublicPlanningAttempt,
  PublicPlanningCycle,
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

function planningAttempt(
  callType: PublicPlanningAttempt["call_type"],
  status: PublicPlanningAttempt["status"],
  overrides: Partial<PublicPlanningAttempt> = {},
): PublicPlanningAttempt {
  return {
    attempt_index: 0,
    call_type: callType,
    status,
    started_at: null,
    finished_at: null,
    duration_ms: null,
    provider_outcome: status === "ERROR" ? "ERROR" : "SUCCESS",
    provider_latency_ms: null,
    validator_summary: [],
    provider_error_category: null,
    provider_error_code: null,
    accepted_step_count: status === "ACCEPTED" ? 3 : 0,
    ...overrides,
  };
}

function planningCycle(
  id: string,
  cycleType: PublicPlanningCycle["cycle_type"],
  status: string,
  attempts: PublicPlanningAttempt[],
  overrides: Partial<PublicPlanningCycle> = {},
): PublicPlanningCycle {
  return {
    id,
    cycle_type: cycleType,
    status,
    started_at: null,
    finished_at: null,
    wall_clock_duration_ms: null,
    attempt_count: attempts.length,
    final_outcome: status,
    attempts,
    ...overrides,
  };
}

function planEvent(
  id: string,
  kind: "PLAN_CREATED" | "PLAN_UPDATED",
  durationMs = 999,
  planningCycleId: string | null = null,
): PublicTask["timeline"][number] {
  return {
    ...task.timeline[0],
    id,
    kind,
    planning_cycle_id: planningCycleId,
    title: kind === "PLAN_CREATED" ? "旧标题" : "另一个旧标题",
    detail: "不应显示的计划细节",
    result_summary: "不应显示的计划结果",
    duration_ms: durationMs,
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
    expect(screen.queryByText("选择任务", { selector: "label" })).not.toBeInTheDocument();
    expect(screen.queryByText(/智能体只会选择当前精确版本/)).not.toBeInTheDocument();
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

  it("polls authoritative PLAY state while planning is active and stops at a terminal state", () => {
    const runningState = {
      current_task: { planning_process: [{ status: "RUNNING" }] },
    } as PlayerGameState;
    const terminalState = {
      current_task: { planning_process: [{ status: "ACCEPTED" }] },
    } as PlayerGameState;

    expect(planningRefetchInterval(runningState, null, false)).toBe(1500);
    expect(planningRefetchInterval(terminalState, null, false)).toBe(false);
    expect(
      planningRefetchInterval(
        undefined,
        { kind: "planning", taskId: "task", startedAt: 0 },
        false,
      ),
    ).toBe(1500);
    expect(
      planningRefetchInterval(
        undefined,
        { kind: "replanning", taskId: "task", startedAt: 0 },
        false,
      ),
    ).toBe(1500);
    expect(planningRefetchInterval(runningState, null, true)).toBe(false);
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
    expect(
      Array.from(document.querySelectorAll<HTMLElement>(".knowledge-accordion")).slice(0, 3).map(
        (section) => section.dataset.testid,
      ),
    ).toEqual([
      "knowledge-accordion-locations",
      "knowledge-accordion-actors",
      "knowledge-accordion-resources",
    ]);
    expect(within(screen.getByTestId("knowledge-accordion-resources")).getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(within(screen.getByTestId("knowledge-accordion-locations")).getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(within(screen.getByTestId("knowledge-accordion-actors")).getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    const locations = within(screen.getByTestId("knowledge-accordion-locations"));
    expect(locations.queryByText("首都议事厅")).not.toBeInTheDocument();
    expect(screen.queryByText("韩烈")).not.toBeInTheDocument();
    expect(screen.queryByText("山谷安全")).not.toBeInTheDocument();

    fireEvent.click(locations.getByRole("button"));
    expect(locations.getByText("首都议事厅")).toBeVisible();
    const actors = within(screen.getByTestId("knowledge-accordion-actors"));
    fireEvent.click(actors.getByRole("button"));
    expect(actors.getByText("韩烈")).toBeVisible();
    const resourcesSection = screen.getByTestId("knowledge-accordion-resources");
    const resourceRegionSummary = resourcesSection.querySelector("details.knowledge-region > summary");
    expect(resourceRegionSummary).not.toBeNull();
    expect(resourcesSection.querySelectorAll("details.knowledge-region[open]")).toHaveLength(1);
    expect(screen.getByText("粮食")).toBeVisible();
    fireEvent.click(resourceRegionSummary!);
    expect(resourcesSection.querySelectorAll("details.knowledge-region[open]")).toHaveLength(0);
    fireEvent.click(resourceRegionSummary!);
    expect(screen.getByText("粮食")).toBeVisible();
    expect(screen.getByText("可用资源状态")).toBeVisible();
  });

  it("offers continuous execution beside the current action and freezes both controls while running", () => {
    const onStart = vi.fn();
    const onContinuous = vi.fn();
    const { rerender } = render(
      <ActionExecutionControls
        disabled={false}
        starting={false}
        continuousExecuting={false}
        onStart={onStart}
        onContinuous={onContinuous}
      />,
    );

    expect(screen.getByRole("button", { name: "知悉，开始执行" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "连续执行" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "连续执行" }));
    expect(onContinuous).toHaveBeenCalledTimes(1);

    rerender(
      <ActionExecutionControls
        disabled
        starting={false}
        continuousExecuting
        onStart={onStart}
        onContinuous={onContinuous}
      />,
    );
    expect(screen.getByRole("button", { name: "知悉，开始执行" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "连续执行" })).toBeDisabled();
    expect(screen.getByTestId("continuous-execution-status")).toHaveTextContent("执行中…");
  });

  it("opens non-empty resource regions by default and preserves manual state across rerenders", () => {
    const resources = [
      { key: "known", name: "Known", value: 10, reserved_value: 0, scope_region_key: "known", scope_region_name: "Known Region" },
      { key: "empty", name: "Empty", value: 0, reserved_value: 0, scope_region_key: "empty", scope_region_name: "Empty Region" },
    ];
    const view = render(
      <KnownWorldAccordions resources={resources} visibleNodes={[]} actors={[]} knownFacts={[]} />,
    );

    const resourceSection = within(screen.getByTestId("knowledge-accordion-resources"));
    const knownRegion = resourceSection.getByText("Known Region").closest("details")!;
    const emptyRegion = resourceSection.getByText("Empty Region").closest("details")!;
    expect(knownRegion).toHaveAttribute("open");
    expect(emptyRegion).not.toHaveAttribute("open");

    fireEvent.click(knownRegion.querySelector("summary")!);
    fireEvent.click(emptyRegion.querySelector("summary")!);
    view.rerender(
      <KnownWorldAccordions resources={resources} visibleNodes={[]} actors={[]} knownFacts={[]} />,
    );

    const rerenderedResourceSection = within(screen.getByTestId("knowledge-accordion-resources"));
    expect(rerenderedResourceSection.getByText("Known Region").closest("details")).not.toHaveAttribute("open");
    expect(rerenderedResourceSection.getByText("Empty Region").closest("details")).toHaveAttribute("open");
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

    expect(screen.getByText("资源 · 已探查区域 1 / 1")).toBeVisible();
    expect(screen.getByText("North Region")).toBeVisible();
    expect(screen.getByText("已完成查探")).toBeVisible();
    expect(
      Array.from(document.querySelectorAll(".knowledge-status-pill")).map(
        (node) => node.textContent,
      ),
    ).toEqual(["5 / 105", "20", "5", "80", "12"]);
    expect(screen.queryByText("Known Zero")).not.toBeInTheDocument();
 });

  it("保留未查探区域的可见资源并独立显示查探状态", () => {
    const resourceIntelligence: ResourceIntelligence = {
      total_regions: 3,
      visible_region_count: 3,
      regions: {
        surveyed: {
          region_name: "已查探区域",
          resource_inventory_visibility: "VISIBLE",
          resource_survey_completed: true,
          resources: {},
        },
        unsurveyed: {
          region_name: "未查探区域",
          resource_inventory_visibility: "HIDDEN",
          resource_survey_completed: false,
          resources: {
            municipal_repair_materials: {
              resource_name: "市政维修材料",
              known_available: 10,
              known_total: 10,
              pools: [
                {
                  pool_key: "known-inflow",
                  quantity: 10,
                  facility_key: null,
                  facility_name: null,
                  availability: "AVAILABLE",
                },
              ],
            },
          },
        },
        hidden: {
          region_name: "完全未知区域",
          resource_inventory_visibility: "HIDDEN",
          resource_survey_completed: false,
          resources: {},
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
        task={{
          ...task,
          timeline: [
            {
              ...task.timeline[0],
              kind: "ACTION_RESULT",
              title: "历史运输 ×99",
              result_summary: null,
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("资源 · 已探查区域 1 / 3")).toBeVisible();
    expect(screen.getByText("已查探区域")).toBeVisible();
    expect(screen.getAllByText("已完成查探")).toHaveLength(1);
    expect(screen.getByText("未查探区域")).toBeVisible();
    expect(screen.getByText("完全未知区域")).toBeVisible();
    expect(screen.getAllByText("未完成查探")).toHaveLength(2);
    expect(screen.getByText("市政维修材料")).toBeVisible();
    expect(screen.getByText("10")).toBeVisible();
    expect(screen.getByText("已确认")).toBeVisible();
    expect(screen.queryByText("已知资源")).not.toBeInTheDocument();
    expect(screen.queryByText("历史运输 ×99")).not.toBeInTheDocument();
    expect(screen.queryByText("隐藏资源")).not.toBeInTheDocument();
  });

  it("未查探区域只显示正数已知资源，空区域保留占位提示", () => {
    const resourceIntelligence: ResourceIntelligence = {
      total_regions: 2,
      visible_region_count: 0,
      regions: {
        empty: {
          region_name: "空区域",
          resource_inventory_visibility: "HIDDEN",
          resource_survey_completed: false,
          resources: {
            hidden_zero: {
              resource_name: "隐藏的零库存",
              known_available: 0,
              known_total: 0,
              pools: [],
            },
          },
        },
        mixed: {
          region_name: "混合区域",
          resource_inventory_visibility: "HIDDEN",
          resource_survey_completed: false,
          resources: {
            known: {
              resource_name: "已知转入资源",
              known_available: 10,
              known_total: 10,
              pools: [],
            },
            zero: {
              resource_name: "不应显示的零库存",
              known_available: 0,
              known_total: 0,
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

    fireEvent.click(screen.getByText("空区域"));
    expect(screen.getByText("空区域")).toBeVisible();
    expect(screen.getByText("混合区域")).toBeVisible();
    expect(screen.getAllByText("暂无资源信息")).toHaveLength(1);
    expect(screen.getByText("已知转入资源")).toBeVisible();
    expect(screen.getByText("10")).toBeVisible();
    expect(screen.queryByText("隐藏的零库存")).not.toBeInTheDocument();
    expect(screen.queryByText("不应显示的零库存")).not.toBeInTheDocument();
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

  it("默认以紧凑模式展示最新方案，并允许循环查看冻结历史", () => {
    render(<PlanHistory task={task} />);
    expect(document.querySelectorAll(".plan-history-card")).toHaveLength(2);
    expect(screen.getByText("乙 · 新行动")).toBeVisible();
    expect(screen.queryByText("甲 · 旧行动", { selector: ".plan-history-steps strong" })).not.toBeInTheDocument();
    const latestToggle = screen.getByRole("button", { name: /执行方案 2 · 执行中/ });
    expect(latestToggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(latestToggle);
    expect(latestToggle).toHaveTextContent("收起");
    expect(latestToggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(latestToggle);
    expect(latestToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("乙 · 新行动")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /执行方案 1 · 已调整/ }));
    expect(screen.getByText("甲 · 旧行动", { selector: ".plan-history-steps strong" })).toBeVisible();
    expect(screen.getByText("甲 · 取消行动").closest("li")).toHaveClass("cancelled");
  });

  it("用玩家状态、规划次数和资源信息展示三档方案卡", () => {
    const cycle = planningCycle(
      "cycle-stage",
      "REPLAN",
      "ACCEPTED",
      [planningAttempt("REPLAN", "REJECTED"), planningAttempt("REPAIR", "ACCEPTED")],
    );
    const steps: PublicTask["plan_history"][number]["steps"] = Array.from(
      { length: 6 },
      (_, index) => ({
        id: `stage-step-${index + 1}`,
        sequence: index + 1,
        action_name: index === 5 ? "运输资源" : index === 4 ? "修复设施" : `行动 ${index + 1}`,
        assigned_actor_name: "应急物流一队",
        status: "COMPLETED" as const,
        result_summary: null,
        location: index === 5
          ? { kind: "ROUTE" as const, summary: "中央城区 → 东部居民区", detail: "旧资源文字 ×10" }
          : index === 4
            ? { kind: "FACILITY" as const, summary: "东部居民区 · 应急设施", detail: null }
          : null,
        resource_usage: index === 5
          ? [
              { resource_key: "municipal", resource_name: "市政维修材料", amount: 10 },
              { resource_key: "general", resource_name: "通用工程部件", amount: 5 },
              { resource_key: "electrical", resource_name: "电力维修部件", amount: 2 },
            ]
          : index === 4
            ? [
                { resource_key: "water", resource_name: "水务系统部件", amount: 15 },
                { resource_key: "general", resource_name: "通用工程部件", amount: 5 },
              ]
          : [],
        resource_usage_kind: index === 5 ? "TRANSPORT" as const : index === 4 ? "CONSUME" as const : null,
      }),
    );
    const plan = {
      id: "stage-plan",
      ordinal: 2,
      status: "COMPLETED" as const,
      display_status: "STAGE_COMPLETED" as const,
      display_reason: "获取资源信息",
      duration_ms: 130000,
      planning_cycle_id: cycle.id,
      completed_steps: 6,
      total_steps: 6,
      failed_step_name: null,
      steps,
    };
    const { rerender } = render(
      <PlanHistory task={{ ...task, plan_history: [plan], planning_process: [cycle] }} />,
    );

    const card = screen.getByRole("button", { name: /执行方案 2 · 阶段完成/ });
    expect(within(card).queryByText("2m 10s")).not.toBeInTheDocument();
    expect(within(card).queryByText("2 次尝试")).not.toBeInTheDocument();
    expect(card).toHaveTextContent("展开全部");
    expect(screen.getByText("运输：市政维修材料 ×10 · 通用工程部件 ×5 · 电力维修部件 ×2")).toBeVisible();
    expect(screen.getByText(/消耗：水务系统部件 ×15 · 通用工程部件 ×5/)).toBeVisible();

    fireEvent.click(card);
    expect(card).toHaveTextContent("收起");
    expect(screen.getByText(/应急物流一队 · 行动 1/)).toBeVisible();
    fireEvent.click(card);
    expect(card).toHaveTextContent("展开");
    expect(screen.queryByText(/应急物流一队 · 行动 1/)).not.toBeInTheDocument();

    const updatedPlan = { ...plan, display_status: "OBJECTIVE_COMPLETED" as const };
    rerender(<PlanHistory task={{ ...task, plan_history: [updatedPlan], planning_process: [cycle] }} />);
    expect(screen.getByRole("button", { name: /执行方案 2 · 目标完成/ })).toBeVisible();
    expect(screen.queryByText("2 次尝试")).not.toBeInTheDocument();
  });

  it("普通 polling 不重置方案 compact viewport 的手动滚动位置", () => {
    const steps = Array.from({ length: 8 }, (_, index) => ({
      id: `scroll-step-${index + 1}`,
      sequence: index + 1,
      action_name: `行动 ${index + 1}`,
      assigned_actor_name: "应急物流一队",
      status: "COMPLETED" as const,
      result_summary: null,
      location: null,
    }));
    const scrollPlan = {
      ...task.plan_history[0],
      id: "scroll-plan",
      ordinal: 1,
      status: "COMPLETED" as const,
      display_status: "STAGE_COMPLETED" as const,
      completed_steps: 8,
      total_steps: 8,
      steps,
    };
    const view = { ...task, plan_history: [scrollPlan] };
    const { rerender } = render(<PlanHistory task={view} />);
    const viewport = document.querySelector<HTMLElement>(".plan-history-step-viewport.compact");
    expect(viewport).not.toBeNull();
    expect(viewport!.querySelectorAll(".plan-history-steps > li")).toHaveLength(8);
    viewport!.scrollTop = 123;
    rerender(<PlanHistory task={{
      ...view,
      version: 2,
      plan_history: [{
        ...scrollPlan,
        steps: steps.map((step, index) => index === 5 ? { ...step, status: "CURRENT" as const } : step),
      }],
    }} />);
    expect(viewport!.scrollTop).toBe(123);
  });

  it("在执行历史结果行展示实际资源语义，而不是重复路线详情", () => {
    render(
      <Timeline
        task={{
          ...task,
          timeline: [{
            ...task.timeline[0],
            id: "transport-result-with-usage",
            kind: "ACTION_RESULT",
            title: "运输资源",
            actor_name: "应急物流一队",
            result_summary: "行动已完成",
            success: true,
            location: { kind: "ROUTE", summary: "西部物流区 → 中央城区", detail: "旧资源文字 ×10" },
            resource_usage: [
              { resource_key: "municipal", resource_name: "市政维修材料", amount: 10 },
              { resource_key: "general", resource_name: "通用工程部件", amount: 5 },
            ],
            resource_usage_kind: "TRANSPORT",
          }],
        }}
      />,
    );
    expect(screen.getByText("运输资源 · 西部物流区 → 中央城区")).toBeVisible();
    expect(screen.getByText("资源已运输 · 市政维修材料 ×10 · 通用工程部件 ×5")).toBeVisible();
    expect(screen.queryByText(/旧资源文字/)).not.toBeInTheDocument();
    expect(screen.queryByText("行动已完成")).not.toBeInTheDocument();
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

  it("规划进行中时统一显示正在规划", () => {
    const initial = planningCycle(
      "cycle-running-initial",
      "INITIAL",
      "RUNNING",
      [planningAttempt("INITIAL_PLAN", "RUNNING")],
    );
    const replan = planningCycle(
      "cycle-running-replan",
      "REPLAN",
      "RUNNING",
      [planningAttempt("REPLAN", "RUNNING")],
    );
    const repair = planningCycle(
      "cycle-running-repair",
      "REPLAN",
      "RUNNING",
      [
        planningAttempt("REPLAN", "REJECTED", {
          validator_summary: [{ code: "INFORMATION_BOUNDARY_REQUIRED" }],
        }),
        planningAttempt("REPAIR", "RUNNING", { attempt_index: 1 }),
      ],
    );
    render(
      <Timeline
        task={{
          ...task,
          timeline: [
            planEvent("running-initial", "PLAN_CREATED", 999, initial.id),
            planEvent("running-replan", "PLAN_UPDATED", 999, replan.id),
            planEvent("running-repair", "PLAN_UPDATED", 999, repair.id),
          ],
          planning_process: [initial, replan, repair],
        }}
      />,
    );

    expect(screen.getAllByText("Agent 正在规划")).toHaveLength(3);
    expect(screen.queryByText("Agent 已完成计划")).not.toBeInTheDocument();
    expect(screen.queryByText("Agent 已重新规划")).not.toBeInTheDocument();
  });

  it("规划从进行中到完成时更新同一张卡片", () => {
    const running = planningCycle(
      "cycle-live",
      "INITIAL",
      "RUNNING",
      [planningAttempt("INITIAL_PLAN", "RUNNING")],
    );
    const accepted = planningCycle(
      "cycle-live",
      "INITIAL",
      "ACCEPTED",
      [planningAttempt("INITIAL_PLAN", "ACCEPTED", { duration_ms: 31000 })],
      { wall_clock_duration_ms: 31000 },
    );
    const timeline = [planEvent("plan-live", "PLAN_CREATED", 999, running.id)];
    const { rerender } = render(
      <Timeline task={{ ...task, timeline, planning_process: [running] }} />,
    );

    expect(screen.getAllByTestId("planning-cycle-cycle-live")).toHaveLength(1);
    expect(screen.getByText("Agent 正在规划")).toBeVisible();

    rerender(<Timeline task={{ ...task, timeline, planning_process: [accepted] }} />);

    expect(screen.getAllByTestId("planning-cycle-cycle-live")).toHaveLength(1);
    expect(screen.getByText("Agent 已完成计划")).toBeVisible();
    expect(screen.queryByText("Agent 正在规划")).not.toBeInTheDocument();
  });

  it("operation transient state is scoped to its Task", () => {
    const operation = { kind: "planning" as const, taskId: "task-2", startedAt: 100 };
    expect(operationBelongsToTask(operation, "task-2")).toBe(true);
    expect(operationBelongsToTask(operation, "task-1")).toBe(false);
    expect(operationBelongsToTask(null, "task-2")).toBe(false);
    expect(formatDuration(0)).toBe("1s");
    expect(formatDuration(435000)).toBe("7m 15s");
    expect(formatDuration(null)).toBeNull();
  });

  it("任务执行记录使用加高面板布局", () => {
    render(
      <MissionLogPanel>
        <span>timeline</span>
      </MissionLogPanel>,
    );
    expect(screen.getByTestId("mission-log-panel")).toHaveClass("mission-log-panel--tall");
  });

  it("把单次初始规划详情嵌入执行方案卡并可展开", () => {
    const cycle = planningCycle(
      "cycle-initial",
      "INITIAL",
      "ACCEPTED",
      [planningAttempt("INITIAL_PLAN", "ACCEPTED", { duration_ms: 31000 })],
      { wall_clock_duration_ms: 31000 },
    );
    render(
      <Timeline
        task={{
          ...task,
          plan_history: [task.plan_history[0]],
          timeline: [planEvent("plan:plan-1:created", "PLAN_CREATED", 999, "cycle-initial")],
          planning_process: [cycle],
        }}
      />,
    );
    const cycleCard = screen.getByTestId("planning-cycle-cycle-initial");
    expect(screen.getAllByText("Agent 已完成计划")).toHaveLength(1);
    expect(screen.getByText("· 31s")).toBeVisible();
    expect(screen.getByText("1 次尝试")).toBeVisible();
    expect(screen.getByText("Agent 已完成计划").parentElement).toHaveClass("timeline-plan-headline");
    expect(screen.getAllByRole("button", { name: "▸ 查看规划详情" })).toHaveLength(1);
    expect(document.querySelectorAll(".planning-details-toggle")).toHaveLength(1);
    expect(screen.queryByTestId("planning-attempt-cycle-initial-0")).not.toBeInTheDocument();

    fireEvent.click(within(cycleCard).getByRole("button"));
    expect(within(cycleCard).getAllByText("第 1 次尝试 · 已完成")).toHaveLength(1);
    expect(screen.getByText("1 次尝试")).toBeVisible();
    expect(screen.getByRole("button", { name: "▾ 收起规划详情" })).toBeVisible();
    expect(within(cycleCard).getByText("已生成 3 步执行方案")).toBeVisible();
    expect(within(cycleCard).queryByText("模型：成功")).not.toBeInTheDocument();
    expect(within(cycleCard).queryByText("Validator：通过")).not.toBeInTheDocument();
    expect(screen.getByTestId("planning-attempt-cycle-initial-0")).toBeVisible();
  });

  it("按顺序展示重新规划和修复规划的全部 attempts", () => {
    const initial = planningCycle(
      "cycle-initial",
      "INITIAL",
      "ACCEPTED",
      [planningAttempt("INITIAL_PLAN", "ACCEPTED")],
    );
    const replan = planningCycle(
      "cycle-replan",
      "REPLAN",
      "ACCEPTED",
      [
        planningAttempt("REPLAN", "REJECTED", {
          validator_summary: [{ code: "RESOURCE_INVENTORY_UNKNOWN" }],
          duration_ms: 82000,
        }),
        planningAttempt("REPAIR", "ACCEPTED", {
          attempt_index: 1,
          duration_ms: 48000,
          accepted_step_count: 7,
        }),
      ],
      { wall_clock_duration_ms: 130000 },
    );
    render(
      <Timeline
        task={{
          ...task,
          timeline: [
            planEvent("plan:plan-1:created", "PLAN_CREATED", 999, "cycle-initial"),
            planEvent("plan:plan-2", "PLAN_UPDATED", 999, "cycle-replan"),
          ],
          planning_process: [initial, replan],
        }}
      />
    );
    const cycleCard = screen.getByTestId("planning-cycle-cycle-replan");
    fireEvent.click(within(cycleCard).getByRole("button"));
    const attempts = within(cycleCard).getAllByTestId(/planning-attempt-cycle-replan-/);
    expect(attempts).toHaveLength(2);
    expect(attempts[0]).toHaveTextContent("第 1 次尝试 · 需要调整");
    expect(attempts[1]).toHaveTextContent("第 2 次尝试 · 已完成");
    expect(within(cycleCard).getByText("当前方案需要调整")).toBeVisible();
    expect(within(cycleCard).getByText("已生成 7 步执行方案")).toBeVisible();
    expect(screen.getByText("· 2m 10s")).toBeVisible();
    expect(screen.getByText("2 次尝试")).toBeVisible();
    expect(screen.getByText("Agent 已重新规划").parentElement).toHaveClass("timeline-plan-headline");
    expect(within(cycleCard).queryByText("2 次尝试")).not.toBeInTheDocument();
    expect(within(cycleCard).queryByText("重新规划 · 已通过")).not.toBeInTheDocument();
    expect(within(attempts[0]).getByText("1m 22s")).toBeVisible();
    expect(within(attempts[1]).getByText("48s")).toBeVisible();
    expect(document.querySelectorAll(".planning-details-toggle")).toHaveLength(2);
  });

  it("失败的重新规划和修复规划也能展开且只显示中文摘要", () => {
    const initial = planningCycle(
      "cycle-initial",
      "INITIAL",
      "ACCEPTED",
      [planningAttempt("INITIAL_PLAN", "ACCEPTED")],
    );
    const cycle = planningCycle(
      "cycle-error",
      "REPLAN",
      "ERROR",
      [
        planningAttempt("REPLAN", "REJECTED", {
          validator_summary: [{ code: "INFORMATION_BOUNDARY_REQUIRED" }],
        }),
        planningAttempt("REPAIR", "ERROR", {
          attempt_index: 1,
          provider_outcome: "ERROR",
          provider_error_category: "RemoteProtocolError",
          provider_error_code: "MODEL_PROVIDER_HTTP_ERROR",
        }),
      ],
      { wall_clock_duration_ms: 435000 },
    );
    render(
      <Timeline
        task={{
          ...task,
          plan_history: [task.plan_history[0]],
          timeline: [
            planEvent("plan:plan-1:created", "PLAN_CREATED", 999, "cycle-initial"),
            planEvent("planning-cycle:cycle-error", "PLAN_UPDATED", 999, "cycle-error"),
          ],
          planning_process: [initial, cycle],
        }}
      />
    );
    const cycleCard = screen.getByTestId("planning-cycle-cycle-error");
    expect(screen.getByTestId("planning-cycle-cycle-initial")).toBeVisible();
    expect(screen.getByText("2 次尝试")).toBeVisible();
    expect(document.querySelectorAll(".planning-details-toggle")).toHaveLength(2);
    fireEvent.click(within(cycleCard).getByRole("button"));
    expect(within(cycleCard).getAllByTestId(/planning-attempt-cycle-error-/)).toHaveLength(2);
    expect(within(cycleCard).getByText("第 1 次尝试 · 需要调整")).toBeVisible();
    expect(within(cycleCard).getByText("第 2 次尝试 · 未能完成")).toBeVisible();
    expect(within(cycleCard).getByText("规划服务未能完成本次规划")).toBeVisible();
    expect(within(cycleCard).getByText("当前方案需要调整")).toBeVisible();
    expect(within(cycleCard).queryByText("模型：成功")).not.toBeInTheDocument();
    expect(within(cycleCard).queryByText("模型：调用失败")).not.toBeInTheDocument();
    expect(screen.queryByText("REPLAN")).not.toBeInTheDocument();
    expect(screen.queryByText("REPAIR")).not.toBeInTheDocument();
    expect(screen.queryByText("SUCCESS")).not.toBeInTheDocument();
    expect(screen.queryByText("INFORMATION_BOUNDARY_REQUIRED")).not.toBeInTheDocument();
    expect(screen.queryByText("MODEL_PROVIDER_HTTP_ERROR")).not.toBeInTheDocument();
  });

  it("右侧计划历史不再显示 planning attempt timeline", () => {
    render(
      <PlanHistory
        task={{
          ...task,
          planning_process: [
            planningCycle("cycle-history", "REPLAN", "ACCEPTED", [
              planningAttempt("REPLAN", "ACCEPTED"),
            ]),
          ],
        }}
      />
    );
    expect(screen.getByText("乙 · 新行动")).toBeVisible();
    expect(screen.queryByTestId("planning-process")).not.toBeInTheDocument();
    expect(screen.queryByTestId("planning-cycle-cycle-history")).not.toBeInTheDocument();
    expect(screen.queryByText("查看规划详情")).not.toBeInTheDocument();
  });

  it("旧 cycle 缺少 attempts 时显示无可用规划明细", () => {
    const cycle = planningCycle("cycle-legacy", "INITIAL", "ERROR", [], {
      wall_clock_duration_ms: 10000,
    });
    render(
      <Timeline
        task={{
          ...task,
          plan_history: [],
          timeline: [planEvent("planning-cycle:cycle-legacy", "PLAN_CREATED", 999, "cycle-legacy")],
          planning_process: [cycle],
        }}
      />,
    );
    const cycleCard = screen.getByTestId("planning-cycle-cycle-legacy");
    fireEvent.click(within(cycleCard).getByRole("button"));
    expect(within(cycleCard).getByText("无可用规划明细")).toBeVisible();
    expect(within(cycleCard).queryByTestId(/planning-attempt-/)).not.toBeInTheDocument();
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
    expect(screen.getAllByText("Parts")).toHaveLength(1);
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
    fireEvent.click(updatedResources.getByText("West Region"));
    fireEvent.click(updatedResources.getByText("Central Region"));
    expect(updatedResources.getByText("Central Region")).toBeVisible();
    expect(updatedResources.getAllByText("暂无资源信息")).toHaveLength(2);
    expect(updatedResources.queryByText("0")).not.toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: /执行方案 1/ })).toHaveTextContent("前往区域 冲突");
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
