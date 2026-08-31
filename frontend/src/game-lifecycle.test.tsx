import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
  scenario_name: "测试场景系列",
  scenario_version_id: "version",
  scenario_version_number: 1,
  scenario_content_hash: "a".repeat(64),
  status: "ARCHIVED",
  runtime_revision: 4,
  is_checkpoint: false,
  checkpointed_from_game_instance_id: null,
  checkpoint_source_runtime_revision: null,
  inherited_task_count: 0,
  active_task_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Game lifecycle actions", () => {
  it("shows the authoritative scenario series name above the version without exposing its hash", () => {
    renderCards(archivedGame);

    expect(screen.getByText("测试场景系列")).toHaveClass("game-card-scenario-name");
    expect(screen.getByText("场景版本 1")).toHaveClass("game-card-scenario-version");
    expect(screen.queryByText("aaaaaaaaaaaa")).not.toBeInTheDocument();
  });

  it("offers Fork from an archived card and navigates to the new target", async () => {
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue("00000000-0000-0000-0000-000000000001");
    const fork = vi.spyOn(api, "forkGame").mockResolvedValue({
      ...archivedGame,
      id: "forked-game",
      status: "ACTIVE",
      runtime_revision: 1,
    });
    renderCards(archivedGame);

    expect(screen.getByRole("link", { name: "查看记录" })).toBeVisible();
    screen.getByRole("button", { name: "以归档状态新开一局" }).click();
    await waitFor(() => expect(fork).toHaveBeenCalledWith("source-game", "00000000-0000-0000-0000-000000000001"));
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
    expect(screen.getByRole("button", { name: "存档" })).toBeDisabled();
  });
  it("creates a checkpoint without leaving the active game", async () => {
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue("00000000-0000-0000-0000-000000000002");
    const checkpoint = vi.spyOn(api, "checkpointGame").mockResolvedValue({
      ...archivedGame,
      id: "checkpoint-game",
      is_checkpoint: true,
      checkpointed_from_game_instance_id: "active-game",
      checkpoint_source_runtime_revision: 4,
      status: "ARCHIVED",
    });
    renderCards({ ...archivedGame, id: "active-game", status: "ACTIVE", active_task_id: null });
    screen.getByRole("button", { name: "存档" }).click();
    await waitFor(() => expect(checkpoint).toHaveBeenCalledWith("active-game", 4, "00000000-0000-0000-0000-000000000002"));
    expect(screen.getByTestId("location")).toHaveTextContent("/games");
  });

  it("distinguishes checkpoint cards and keeps them forkable", () => {
    renderCards({
      ...archivedGame,
      is_checkpoint: true,
      checkpointed_from_game_instance_id: "source-game",
      checkpoint_source_runtime_revision: 4,
    });
    expect(screen.getByText("存档 · 来源 source-g")).toBeVisible();
    expect(screen.getByRole("button", { name: "以归档状态新开一局" })).toBeEnabled();
  });
});
