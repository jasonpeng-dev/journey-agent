import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { api } from "./api";
import { App } from "./App";
import { NewScenarioPage } from "./pages/NewScenarioPage";
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
  it("renders every scenario as one accessible link to Scenario Detail without legacy actions", async () => {
    vi.spyOn(api, "scenarios").mockResolvedValue([scenario, secondScenario]);

    renderWithRouter(<ScenarioLibraryPage />, ["/scenarios"]);

    const list = await screen.findByTestId("scenario-library-list");
    expect(list).toHaveClass("scenario-list");
    expect(screen.getAllByTestId(/scenario-row-/)).toHaveLength(2);
    const createScenarioButton = screen.getByRole("link", { name: "新建场景" });
    expect(createScenarioButton).toHaveAttribute("href", "/scenarios/new");
    expect(screen.getByRole("button").className).toBe(createScenarioButton.className);
    expect(screen.getByRole("link", { name: "新建场景" })).toHaveAttribute("href", "/scenarios/new");
    expect(screen.getByText(scenario.name)).toBeVisible();
    expect(screen.getByText("已发布版本 14")).toBeVisible();
    const firstRow = screen.getByTestId("scenario-row-scenario-1");
    expect(within(firstRow).getByText("当前草稿修订号：14")).toBeVisible();
    expect(firstRow).toHaveAttribute("href", "/scenarios/scenario-1");
    expect(firstRow.tagName).toBe("A");
    expect(screen.queryByRole("button", { name: "直接开始测试" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "界面设置" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "查看场景" })).not.toBeInTheDocument();
    expect(screen.getByTestId("scenario-row-scenario-2")).toHaveTextContent("医疗应急演练");
    expect(screen.getByTestId("scenario-row-scenario-2")).not.toHaveTextContent("直接开始测试");
  });

  it("keeps creation in the Scenario Library heading and removes it from global navigation", async () => {
    vi.spyOn(api, "scenarios").mockResolvedValue([scenario]);
    renderWithRouter(<App />, ["/scenarios"]);

    await screen.findByTestId("scenario-library-list");
    const globalNavigation = within(screen.getByRole("banner")).getByRole("navigation");
    expect(within(globalNavigation).getByRole("link", { name: "场景库" })).toBeVisible();
    expect(within(globalNavigation).getByRole("link", { name: "游戏" })).toBeVisible();
    expect(within(globalNavigation).queryByRole("link", { name: "新建场景" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: "新建场景" })).toHaveClass("primary-button");
  });

  it("provides a top-right return from scenario creation to the Scenario Library", async () => {
    vi.spyOn(api, "examples").mockResolvedValue([]);
    renderWithRouter(
      <Routes>
        <Route path="/scenarios/new" element={<NewScenarioPage />} />
        <Route path="/scenarios" element={<div>Scenario Library</div>} />
      </Routes>,
      ["/scenarios/new"],
    );

    const returnLink = screen.getByRole("link", { name: "返回场景库" });
    expect(returnLink).toHaveAttribute("href", "/scenarios");
    expect(returnLink).toHaveClass("action-button-centered");
    expect(returnLink.closest(".page-heading")).toHaveClass("new-scenario-heading");
    fireEvent.click(returnLink);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/scenarios"));
  });

  it("navigates from the whole Scenario row to its Detail route", async () => {
    vi.spyOn(api, "scenarios").mockResolvedValue([scenario]);
    renderWithRouter(<ScenarioLibraryPage />, ["/scenarios"]);
    fireEvent.click(await screen.findByTestId("scenario-row-scenario-1"));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/scenarios/scenario-1"));
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
    expect(screen.getByRole("link", { name: "编辑当前草稿" })).toHaveClass("primary-button");
    expect(screen.getByRole("link", { name: "编辑当前草稿" })).toHaveClass("editor-button", "editor-button-primary");
    expect(screen.getByTestId("presentation-settings-link")).toHaveAttribute("href", "/scenarios/scenario-1/presentation");
    expect(screen.getByTestId("presentation-settings-link")).toHaveClass("primary-button");
    expect(screen.queryByText("适用于此场景的所有版本和游戏实例")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回场景库" })).toHaveClass("navigation-away-button");
    expect(screen.getByRole("link", { name: "编辑当前草稿" })).toHaveClass("scenario-detail-action-button");
    expect(screen.getByTestId("presentation-settings-link")).toHaveClass("scenario-detail-action-button");
    expect(screen.getByRole("link", { name: "返回场景库" })).toHaveClass("scenario-detail-action-button");
    expect(screen.getByRole("link", { name: "编辑当前草稿" })).toHaveClass("action-button-centered");
    expect(screen.getByTestId("presentation-settings-link")).toHaveClass("action-button-centered");
    expect(screen.getByRole("link", { name: "返回场景库" })).toHaveClass("action-button-centered");
    expect(screen.getByRole("link", { name: "返回场景库" })).toHaveClass("editor-button", "editor-button-secondary");
    expect(screen.getByText("版本历史")).toBeVisible();
    expect(screen.getByText("版本 14")).toBeVisible();
    const versionCard = screen.getByRole("article");
    expect(within(versionCard).getByRole("button", { name: "恢复到当前草稿" })).toHaveClass("editor-button", "editor-button-primary");
    expect(within(versionCard).getByRole("button", { name: "不支持当前格式导出" })).toHaveClass("editor-button", "editor-button-secondary");

    screen.getByRole("link", { name: "返回场景库" }).click();
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/scenarios"));
  });
});
