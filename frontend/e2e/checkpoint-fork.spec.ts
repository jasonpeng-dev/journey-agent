import { expect, test } from "@playwright/test";

import {
  appendFixtureTask,
  currentScenarioName,
  fixtureGame,
  getJson,
  wireApi,
  type GameSummary,
} from "./test-fixtures";

test("Checkpoint / Fork Smoke", async ({ page }) => {
  await wireApi(page);
  const fixture = fixtureGame("fork");
  const source = await getJson<GameSummary>(page, `/api/v1/games/${fixture.gameId}`);

  expect(source.status).toBe("ACTIVE");
  expect(source.active_task_id).toBeNull();
  expect(source.scenario_version_number).toBeGreaterThan(0);

  await page.goto(`/games/${fixture.gameId}`);
  await expect(page.getByText(currentScenarioName, { exact: true }).first()).toBeVisible();
  await expect(page.locator(".plan-history-card")).toBeVisible();
  await expect(page.locator(".timeline-entry").filter({ hasText: "检查状态" })).toBeVisible();

  await page.getByRole("button", { name: "存档", exact: true }).click();
  await expect(page.getByText(/已创建存档/)).toBeVisible();

  const archived = await getJson<GameSummary[]>(page, "/api/v1/games?status=archived");
  const checkpoint = archived.find(
    (game) => game.is_checkpoint && game.checkpointed_from_game_instance_id === fixture.gameId,
  );
  expect(checkpoint).toBeDefined();

  await page.goto("/games");
  const checkpointCard = page.locator(".checkpoint-card").filter({
    hasText: checkpoint!.id.slice(0, 8),
  });
  await expect(checkpointCard).toBeVisible();
  await expect(checkpointCard.getByRole("button", { name: "以归档状态新开一局" })).toBeVisible();
  await checkpointCard.getByRole("button", { name: "以归档状态新开一局" }).click();
  await page.waitForURL(/\/games\/[0-9a-f-]{36}$/);

  const forkId = new URL(page.url()).pathname.split("/").at(-1) ?? "";
  const fork = await getJson<GameSummary>(page, `/api/v1/games/${forkId}`);
  expect(fork.status).toBe("ACTIVE");
  expect(fork.inherited_task_count).toBe(1);

  appendFixtureTask(forkId);
  await page.reload();
  await expect(page.getByTestId("history-boundary")).toBeVisible();
  await expect(page.locator(".task-tabs button")).toHaveCount(2);
  await page.locator(".task-tabs button").first().click();
  await expect(page.locator(".plan-history-card")).toBeVisible();
  await expect(page.locator(".plan-history-steps li.completed")).toBeVisible();
  await expect(page.locator(".timeline-entry").filter({ hasText: "检查状态" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "已知世界", exact: true })).toBeVisible();
});