import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { api } from "./api";
import { ScenarioDetailPage } from "./pages/ScenarioDetailPage";
import { ScenarioLibraryPage } from "./pages/ScenarioLibraryPage";
import type { Draft, ScenarioSummary, ScenarioVersion } from "./types";

const scenario: ScenarioSummary = {
  id: "scenario-1",
  key: "linjiang_infrastructure_recovery_v2_0_final_local",
  name: "临江市灾后基础设施恢复 v2.0 Final",
  status: "PUBLISHED",
  draft_revision: 14,
  current_published_version_id: "version-14",
  current_published_version_number: 14,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-15T00:00:00Z",
};

const secondScenario: ScenarioSummary = {
  ...scenario,
  id: "scenario-2",
  key: "medical_emergency_local",
  name: "医疗应急演练",
  current_published_version_id: null,
  current_published_version_number: null,
};

const draft: Draft = {
  scenario_id: scenario.id,
  revision: scenario.draft_revision,
  definition_document: {},
  validation_status: "VALID",
  validation_issues: [],
  content_hash: null,
  base_scenario_version_id: null,
  updated_at: scenario.updated_at,
};

const version: ScenarioVersion = {
  id: "version-14",
  scenario_id: scenario.id,
  version_number: 14,
  schema_version: 2,
  content_hash: "a".repeat(64),
  published_at: "2026-09-15T00:00:00Z",
};

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function renderWithRouter(ui: ReactNode, initialEntries: string[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        {ui}
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Scenario Library and Detail presentation", () => {
  it("renders every scenario as a compact single-column row with right-aligned actions", async () => {
    vi.spyOn(api, "scenarios").mockResolvedValue([scenario, secondScenario]);

    renderWithRouter(<ScenarioLibraryPage />, ["/scenarios"]);

    const list = await screen.findByTestId("scenario-library-list");
    expect(list).toHaveClass("scenario-list");
    expect(screen.getAllByTestId(/scenario-row-/)).toHaveLength(2);
    expect(screen.getByText(scenario.name)).toBeVisible();
    expect(screen.getByText("已发布版本 14")).toBeVisible();
    expect(within(screen.getByTestId("scenario-row-scenario-1")).getByText("当前草稿修订号：14")).toBeVisible();
    expect(within(screen.getByTestId("scenario-row-scenario-1")).getByRole("link", { name: "查看场景" })).toHaveAttribute("href", "/scenarios/scenario-1");
    expect(screen.getByRole("button", { name: "直接开始测试" })).toBeVisible();
    expect(screen.getByTestId("scenario-row-scenario-2")).toHaveTextContent("医疗应急演练");
    expect(screen.getByTestId("scenario-row-scenario-2")).not.toHaveTextContent("直接开始测试");
  });

  it("keeps direct test behavior and navigates to the created game", async () => {
    vi.spyOn(api, "scenarios").mockResolvedValue([scenario]);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue("00000000-0000-0000-0000-000000000001");
    const createGame = vi.spyOn(api, "createGame").mockResolvedValue({
      id: "game-1",
      scenario_id: scenario.id,
      scenario_name: scenario.name,
      scenario_version_id: version.id,
      scenario_version_number: version.version_number,
      scenario_content_hash: version.content_hash,
      status: "ACTIVE",
      runtime_revision: 1,
      is_checkpoint: false,
      checkpointed_from_game_instance_id: null,
      checkpoint_source_runtime_revision: null,
      inherited_task_count: 0,
      active_task_id: null,
      created_at: scenario.created_at,
      updated_at: scenario.updated_at,
    });

    renderWithRouter(<ScenarioLibraryPage />, ["/scenarios"]);
    (await screen.findByRole("button", { name: "直接开始测试" })).click();

    await waitFor(() => expect(createGame).toHaveBeenCalledWith(version.id, "00000000-0000-0000-0000-000000000001"));
    expect(screen.getByTestId("location")).toHaveTextContent("/games/game-1");
  });

  it("places a stable library return link beside the edit action without changing history controls", async () => {
    vi.spyOn(api, "scenario").mockResolvedValue(scenario);
    vi.spyOn(api, "draft").mockResolvedValue(draft);
    vi.spyOn(api, "versions").mockResolvedValue([version]);

    renderWithRouter(
      <Routes>
        <Route path="/scenarios/:scenarioId" element={<ScenarioDetailPage />} />
      </Routes>,
      ["/scenarios/scenario-1"],
    );

    await screen.findByText(scenario.name);
    expect(screen.getByRole("link", { name: "编辑当前草稿" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/overview");
    expect(screen.getByRole("link", { name: "返回场景库" })).toHaveAttribute("href", "/scenarios");
    expect(screen.getByText("版本历史")).toBeVisible();
    expect(screen.getByText("版本 14")).toBeVisible();

    screen.getByRole("link", { name: "返回场景库" }).click();
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/scenarios"));
  });
});
