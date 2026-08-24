import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import { GameCards } from "./pages/GamesPage";
import type { GameSummary } from "./types";

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function renderCards(game: GameSummary) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/games"]}>
        <GameCards games={[game]} archived={game.status === "ARCHIVED"} />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const archivedGame: GameSummary = {
  id: "source-game",
  scenario_id: "scenario",
  scenario_version_id: "version",
  scenario_version_number: 1,
  scenario_content_hash: "a".repeat(64),
  status: "ARCHIVED",
  runtime_revision: 4,
  active_task_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Game lifecycle actions", () => {
  it("offers Fork from an archived card and navigates to the new target", async () => {
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue("creation-key");
    const fork = vi.spyOn(api, "forkGame").mockResolvedValue({
      ...archivedGame,
      id: "forked-game",
      status: "ACTIVE",
      runtime_revision: 1,
    });
    renderCards(archivedGame);

    expect(screen.getByRole("link", { name: "查看记录" })).toBeVisible();
    screen.getByRole("button", { name: "以归档状态新开一局" }).click();
    await waitFor(() => expect(fork).toHaveBeenCalledWith("source-game", "creation-key"));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/games/forked-game"));
  });

  it("keeps archive available only for stable active cards", () => {
    const active = {
      ...archivedGame,
      id: "active-game",
      status: "ACTIVE" as const,
      runtime_revision: 1,
      active_task_id: "task",
    };
    renderCards(active);
    const archive = screen.getByRole("button", { name: "结束并归档" });
    expect(archive).toBeDisabled();
    expect(archive).toHaveAttribute("title", "当前有活动任务，完成或放弃后才能归档");
  });
});
