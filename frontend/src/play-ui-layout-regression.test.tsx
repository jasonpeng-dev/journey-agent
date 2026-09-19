import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { KnownWorldAccordions, PlanHistory } from "./pages/GamePage";
import type { PlayerGameState, PublicPlanHistory, PublicTask } from "./types";

const actor = (
  key: string,
  reachability: "ONLINE" | "DISCONNECTED",
): PlayerGameState["actors"][number] => ({
  key,
  name: `${key} Team`,
  role_name: `${key} Role`,
  current_node_name: "East District",
  status: "IDLE",
  task_name: null,
  command_reachability: reachability,
});

const step = (id: string, actionName: string): PublicPlanHistory["steps"][number] => ({
  id,
  sequence: 1,
  action_name: actionName,
  assigned_actor_name: "Logistics Team",
  status: "COMPLETED",
  result_summary: null,
});

const plan = (
  id: string,
  ordinal: number,
  summary: string,
  actionName: string,
): PublicPlanHistory => ({
  id,
  ordinal,
  status: "COMPLETED",
  display_status: "STAGE_COMPLETED",
  display_reason: summary,
  completed_steps: 1,
  total_steps: 1,
  failed_step_name: null,
  steps: [step(`${id}-step`, actionName)],
});

const taskWithHistoricalPlan = (historical: PublicPlanHistory): PublicTask => ({
  id: "task-1",
  version: 1,
  goal: "Restore city services",
  status: "ACTIVE",
  execution_phase: "AWAITING_PLAN_ATTEMPT",
  pacing_version: 1,
  objective_names: [],
  roadmap: { stages: [] },
  plan: null,
  plan_history: [historical, plan("latest", 2, "Continue execution", "Continue action")],
  planning_process: [],
  timeline: [],
  briefing: null,
  debrief: null,
  explanation: null,
});

describe("PLAY focused layout regressions", () => {
  afterEach(cleanup);

  it("groups location and reachability pills on the right of Actor copy", () => {
    const { getByTestId } = render(
      <KnownWorldAccordions
        resources={[]}
        visibleNodes={[]}
        actors={[actor("Comms", "DISCONNECTED"), actor("Power", "ONLINE")]}
        knownFacts={[]}
      />,
    );
    fireEvent.click(within(getByTestId("knowledge-accordion-actors")).getByRole("button"));

    for (const [name, role, reachability] of [
      ["Comms Team", "Comms Role", "\u5931\u8054"],
      ["Power Team", "Power Role", "\u5728\u7ebf"],
    ] as const) {
      const entry = within(getByTestId("knowledge-accordion-actors")).getByText(name).closest(".actor-entry");
      expect(entry).not.toBeNull();
      const copy = entry!.querySelector(".knowledge-entry-copy")!;
      const badges = entry!.querySelector(".actor-status-pills")!;
      expect(within(copy as HTMLElement).getByText(name)).toBeVisible();
      expect(within(copy as HTMLElement).getByText(role)).toBeVisible();
      expect(badges.querySelector('[data-actor-field="LOCATION"]')).toHaveTextContent("East District");
      expect(badges.querySelector('[data-actor-field="COMMAND_REACHABILITY"]')).toHaveTextContent(reachability);
      expect(badges.children).toHaveLength(2);
    }
  });

  it.each([
    ["\u83b7\u53d6\u8d44\u6e90\u4fe1\u606f", "Survey resources"],
    ["\u51c6\u5907\u7ee7\u7eed\u89c4\u5212", "Continue planning"],
  ])("renders only the header while a historical Plan is collapsed: %s", (summary, actionName) => {
    const { container } = render(
      <PlanHistory task={taskWithHistoricalPlan(plan("historical", 1, summary, actionName))} />,
    );
    const card = container.querySelector(".plan-history-card")!;
    const toggle = card.querySelector(".plan-history-toggle")! as HTMLButtonElement;

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(toggle).getByText(new RegExp(summary))).toBeVisible();
    expect(within(card as HTMLElement).getAllByText(new RegExp(summary))).toHaveLength(1);
    expect(within(card as HTMLElement).queryByText(actionName)).not.toBeInTheDocument();
    expect(card.querySelector(".plan-history-steps")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(within(card as HTMLElement).getByText(new RegExp(actionName))).toBeVisible();
    expect(card.querySelector(".plan-history-steps")).toBeInTheDocument();
  });
});
