import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, api } from "./api";
import { PresentationSettingsPage } from "./pages/PresentationSettingsPage";
import { resolvePresentationProfilePreview } from "./presentationPolicy";
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
        <Routes><Route path="/scenarios/:scenarioId/presentation" element={<PresentationSettingsPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockPageData() {
  vi.spyOn(api, "scenario").mockResolvedValue(scenario);
  vi.spyOn(api, "presentation").mockResolvedValue(current);
  vi.spyOn(api, "presentationRevision").mockResolvedValue({ scenario_id: scenario.id, revision: current.revision });
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
    expect(screen.getByTestId("presentation-save-state")).toHaveTextContent("CLEAN");
    fireEvent.change(screen.getByTestId("presentation-template"), { target: { value: "compact" } });
    expect(screen.getByTestId("presentation-save-state")).toHaveTextContent("DIRTY");

    fireEvent.click(screen.getByTestId("presentation-save-button"));
    await waitFor(() => expect(saved).toHaveBeenCalledWith("scenario-1", 2, expect.objectContaining({ template: "compact" })));
    await waitFor(() => expect(screen.getByTestId("presentation-save-state")).toHaveTextContent("CLEAN"));
    expect(screen.getByText("当前修订号：3")).toBeVisible();
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
    await waitFor(() => expect(screen.getByText("当前修订号：3")).toBeVisible());
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
});
