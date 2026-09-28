import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  KnownWorldAccordions,
  MissionRoadmap,
  PlanHistory,
  Timeline,
} from "./pages/GamePage";
import type { MissionRoadmapStage, PlayerGameState, PublicTask } from "./types";

const presentation = (overrides: Partial<NonNullable<PlayerGameState["presentation"]>> = {}) => ({
  revision: 1,
  template: "standard" as const,
  density: "STANDARD" as const,
  default_open: "COMPACT" as const,
  summary_slot: "HEADER" as const,
  entity_detail: "DETAIL" as const,
  knowledge_level: "A+B" as const,
  semantic_order: ["NAME"],
  resource_order: ["NAME"],
  relation_order: ["TYPE"],
  actor_fields: ["NAME", "ROLE", "LOCATION", "STATUS", "TASK", "COMMAND_REACHABILITY"],
  roadmap_detail: "DETAIL" as const,
  plan_default: "COMPACT" as const,
  timeline_density: "STANDARD" as const,
  ...overrides,
});

const stage: MissionRoadmapStage = {
  key: "stage-1",
  name: "Restore the clinic",
  description: "Public stage",
  status: "CURRENT",
  objective_key: "objective-1",
  requirements: [{
    key: "operation-1",
    kind: "ACTION_COMPLETED",
    description: "Internal description",
    action_key: "repair",
    action_name: "Repair clinic",
    actor_key: "team",
    actor_name: "Repair Team",
    target_key: "clinic",
    target_name: "Clinic",
    parameter_constraints: { resources: [{ resource_key: "parts", amount: 3 }] },
    operation_status: "PENDING",
  }],
};

const task = (overrides: Partial<PublicTask> = {}): PublicTask => ({
  id: "task-1",
  version: 1,
  goal: "Restore the clinic",
  status: "ACTIVE",
  execution_phase: "AWAITING_ACTION_ACK",
  pacing_version: 1,
  objective_names: ["Restore the clinic"],
  roadmap: { stages: [] },
  plan: null,
  plan_history: [{
    id: "plan-1",
    ordinal: 1,
    status: "EXECUTING",
    display_status: "EXECUTING",
    display_reason: null,
    completed_steps: 0,
    total_steps: 1,
    failed_step_name: null,
    steps: [{
      id: "step-1",
      sequence: 1,
      action_name: "Repair clinic",
      assigned_actor_name: "Repair Team",
      status: "CURRENT",
      result_summary: null,
    }],
  }],
  timeline: [{
    id: "event-1",
    kind: "TASK_BLOCKED",
    title: "Task blocked",
    detail: "Internal event detail",
    actor_name: "Repair Team",
    result_summary: "The task is blocked",
    success: false,
    knowledge_changes: [],
    occurred_at: null,
  }],
  briefing: null,
  debrief: null,
  explanation: "The task is blocked",
  ...overrides,
});

describe("live presentation policy", () => {
  afterEach(cleanup);

  it("keeps roadmap truth visible while compact mode limits action detail", () => {
    render(
      <MissionRoadmap
        stages={[{ ...stage, status: "COMPLETED" }]}
        nodeNames={{ clinic: "Clinic" }}
        presentation={presentation({ roadmap_detail: "SUMMARY" })}
      />,
    );

    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Repair clinic")).toBeVisible();
    expect(screen.queryByText("Repair Team")).not.toBeInTheDocument();
    expect(screen.getByRole("list").firstElementChild).toHaveClass("completed");
  });

  it("applies actor allowlists without exposing unrequested safe fields", () => {
    render(
      <KnownWorldAccordions
        presentation={presentation({ actor_fields: ["NAME", "STATUS"] })}
        resources={[]}
        visibleNodes={[]}
        actors={[{
          key: "team",
          name: "Repair Team",
          role_name: "Response",
          current_node_name: "Clinic",
          status: "ACTIVE",
          task_name: "Restore the clinic",
          command_reachability: "ONLINE",
        }]}
        knownFacts={[]}
      />,
    );

    fireEvent.click(screen.getByTestId("knowledge-accordion-actors").querySelector("button")!);
    expect(screen.getByText("Repair Team")).toBeVisible();
    expect(screen.getByText("行动中")).toBeVisible();
    expect(screen.queryByText("Response")).not.toBeInTheDocument();
    expect(screen.queryByText("Clinic")).not.toBeInTheDocument();
  });

  it("uses profile plan defaults, preserves local toggles, and keeps blocked timeline events", () => {
    const compact = presentation({ plan_default: "COLLAPSED", timeline_density: "COMPACT" });
    render(<PlanHistory task={task()} presentation={compact} />);
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");

    render(<Timeline task={task()} presentation={compact} />);
    expect(screen.getByText(/任务状态/)).toBeVisible();
    expect(screen.getByText("目标暂时无法推进")).toBeVisible();
    expect(screen.queryByText("Internal event detail")).not.toBeInTheDocument();
  });
});
