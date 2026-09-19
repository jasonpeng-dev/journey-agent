import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { ApiError, api } from "./api";
import { PresentationSettingsPage } from "./pages/PresentationSettingsPage";
import { defaultPresentationProfile, resolvePresentationProfilePreview } from "./presentationPolicy";
import type {
  PresentationProfileDocument,
  PresentationProfileHistoryResponse,
  PresentationProfileResponse,
  ScenarioSummary,
} from "./types";

const scenario: ScenarioSummary = {
  id: "scenario-1",
  key: "generic_scenario",
  name: "通用演示场景",
  status: "PUBLISHED",
  draft_revision: 4,
  current_published_version_id: "version-4",
  current_published_version_number: 4,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-15T00:00:00Z",
};

const profile: PresentationProfileDocument = {
  schema_version: 1,
  template: "standard",
  family_overrides: [],
  semantic_overrides: [],
};

const current: PresentationProfileResponse = {
  scenario_id: scenario.id,
  revision: 2,
  profile,
  updated_at: "2026-09-15T00:00:00Z",
};

const history: PresentationProfileHistoryResponse = {
  scenario_id: scenario.id,
  revisions: [
    { scenario_id: scenario.id, revision: 2, profile, created_at: "2026-09-15T00:00:00Z" },
    { scenario_id: scenario.id, revision: 1, profile: { ...profile, template: "compact" }, created_at: "2026-09-14T00:00:00Z" },
  ],
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/scenarios/scenario-1/presentation"]}>
        <Routes><Route path="/scenarios/:scenarioId/presentation" element={<PresentationSettingsPage />} /><Route path="/scenarios/:scenarioId" element={<div>Scenario Detail</div>} /></Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function LocationProbe() {
  return <output data-testid="settings-location">{useLocation().pathname}</output>;
}

function mockPageData(currentProfile: PresentationProfileResponse = current) {
  vi.spyOn(api, "scenario").mockResolvedValue(scenario);
  vi.spyOn(api, "presentation").mockResolvedValue(currentProfile);
  vi.spyOn(api, "presentationRevision").mockResolvedValue({ scenario_id: scenario.id, revision: currentProfile.revision });
  vi.spyOn(api, "presentationHistory").mockResolvedValue(history);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Presentation settings workstation", () => {
  it("uses the bounded profile resolver for local preview without adding actor capabilities", () => {
    const preview = resolvePresentationProfilePreview({
      ...profile,
      template: "compact",
      actor_team: { field_order: ["NAME", "CAPABILITIES", "STATUS"] },
    }, 8);

    expect(preview.revision).toBe(8);
    expect(preview.template).toBe("compact");
    expect(preview.default_open).toBe("COLLAPSED");
    expect(preview.actor_fields).toEqual(["NAME", "STATUS"]);
  });

  it("loads the bounded workstation, marks local edits dirty, and saves with the current revision", async () => {
    mockPageData();
    const saved = vi.spyOn(api, "savePresentation").mockResolvedValue({
      ...current,
      revision: 3,
      profile: { ...profile, template: "compact" },
    });

    renderPage();
    await screen.findByTestId("presentation-settings-panel");

    const title = screen.getByTestId("presentation-page-title");
    const summary = screen.getByTestId("presentation-page-summary");
    expect(within(title).getByRole("heading", { name: "界面设置" })).toBeVisible();
    expect(within(summary).getByText(scenario.name)).toHaveClass("presentation-scenario-badge");
    expect(title.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(summary).getByTestId("presentation-save-state")).toHaveTextContent("CLEAN");
    expect(screen.getByTestId("presentation-save-state")).toHaveTextContent("CLEAN");
    fireEvent.change(screen.getByTestId("presentation-template"), { target: { value: "compact" } });
    expect(screen.getByTestId("presentation-save-state")).toHaveTextContent("DIRTY");

    fireEvent.click(screen.getByTestId("presentation-save-button"));
    await waitFor(() => expect(saved).toHaveBeenCalledWith("scenario-1", 2, expect.objectContaining({ template: "compact" })));
    await waitFor(() => expect(screen.getByTestId("presentation-save-state")).toHaveTextContent("CLEAN"));
    expect(screen.getByText("修订 3")).toBeVisible();
  });

  it("restores a historical profile through the same optimistic-concurrency endpoint", async () => {
    mockPageData();
    const restored = vi.spyOn(api, "restorePresentation").mockResolvedValue({
      ...current,
      revision: 3,
      profile: { ...profile, template: "compact" },
    });

    renderPage();
    await screen.findByTestId("presentation-history");
    fireEvent.click(screen.getAllByRole("button", { name: "恢复此版本" })[1]);
    await waitFor(() => expect(restored).toHaveBeenCalledWith("scenario-1", 2, 1));
    await waitFor(() => expect(screen.getByText("修订 3")).toBeVisible());
  });

  it("surfaces a 409 as CONFLICT without replacing the local working copy", async () => {
    mockPageData();
    vi.spyOn(api, "savePresentation").mockRejectedValue(new ApiError("revision changed", 409, "PRESENTATION_REVISION_CONFLICT", {}));

    renderPage();
    await screen.findByTestId("presentation-settings-panel");
    fireEvent.change(screen.getByTestId("presentation-template"), { target: { value: "detailed" } });
    fireEvent.click(screen.getByTestId("presentation-save-button"));
    await waitFor(() => expect(screen.getByTestId("presentation-save-state")).toHaveTextContent("CONFLICT"));
    expect(screen.getByTestId("presentation-template")).toHaveValue("detailed");
  });

  it("moves all actions to the top and guards dirty return without silently discarding", async () => {
    mockPageData();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await screen.findByTestId("presentation-settings-panel");

    expect(screen.getByTestId("presentation-save-button")).toBeDisabled();
    expect(screen.getByTestId("presentation-discard-button")).toBeDisabled();
    expect(screen.getByTestId("presentation-return-button")).toBeEnabled();
    expect(document.querySelector(".presentation-workstation-footer")).not.toBeInTheDocument();
    const notes = screen.getByTestId("presentation-bottom-notes");
    expect(within(notes).getByText("只调整安全信息的排列与密度，不修改场景、知识或 Agent 行为。")).toBeVisible();
    expect(within(notes).getByText("保存后的界面设置适用于此场景的所有版本和游戏实例。")).toBeVisible();
    for (const testId of [
      "presentation-save-button",
      "presentation-restore-default-button",
      "presentation-discard-button",
      "presentation-return-button",
    ]) {
      expect(screen.getByTestId(testId)).toHaveClass("action-button-centered");
    }
    fireEvent.click(screen.getByTestId("presentation-restore-default-button"));
    expect(screen.getByTestId("presentation-save-state")).toHaveTextContent("CLEAN");

    fireEvent.change(screen.getByTestId("presentation-template"), { target: { value: "compact" } });
    expect(screen.getByTestId("presentation-save-button")).toBeEnabled();
    expect(screen.getByTestId("presentation-discard-button")).toBeEnabled();
    fireEvent.click(screen.getByTestId("presentation-return-button"));
    expect(confirm).toHaveBeenCalled();
    expect(screen.getByTestId("settings-location")).toHaveTextContent("/scenarios/scenario-1/presentation");
    expect(screen.getByTestId("presentation-template")).toHaveValue("compact");

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByTestId("presentation-return-button"));
    await waitFor(() => expect(screen.getByTestId("settings-location")).toHaveTextContent("/scenarios/scenario-1"));
  });

  it("returns directly to Scenario Detail when settings are clean", async () => {
    mockPageData();
    const confirm = vi.spyOn(window, "confirm");
    renderPage();
    await screen.findByTestId("presentation-settings-panel");
    fireEvent.click(screen.getByTestId("presentation-return-button"));
    await waitFor(() => expect(screen.getByTestId("settings-location")).toHaveTextContent("/scenarios/scenario-1"));
    expect(confirm).not.toHaveBeenCalled();
  });

  it("restores canonical defaults locally, then supports discard or normal revisioned save", async () => {
    const customized: PresentationProfileResponse = {
      ...current,
      profile: { ...profile, template: "detailed" },
    };
    mockPageData(customized);
    const saved = vi.spyOn(api, "savePresentation").mockResolvedValue({
      ...current,
      revision: 3,
      profile: defaultPresentationProfile(),
    });
    renderPage();
    await screen.findByTestId("presentation-settings-panel");

    fireEvent.click(screen.getByTestId("presentation-restore-default-button"));
    expect(screen.getByTestId("presentation-template")).toHaveValue("standard");
    expect(screen.getByTestId("presentation-save-state")).toHaveTextContent("DIRTY");
    expect(document.querySelector(".presentation-revision")).toHaveTextContent("修订 2");
    expect(saved).not.toHaveBeenCalled();
    expect(screen.getByTestId("settings-location")).toHaveTextContent("/scenarios/scenario-1/presentation");

    fireEvent.click(screen.getByTestId("presentation-discard-button"));
    expect(screen.getByTestId("presentation-template")).toHaveValue("detailed");
    expect(screen.getByTestId("presentation-save-state")).toHaveTextContent("CLEAN");

    fireEvent.click(screen.getByTestId("presentation-restore-default-button"));
    fireEvent.click(screen.getByTestId("presentation-save-button"));
    await waitFor(() => expect(saved).toHaveBeenCalledWith("scenario-1", 2, defaultPresentationProfile()));
    await waitFor(() => expect(screen.getByText("修订 3")).toBeVisible());
  });
});
