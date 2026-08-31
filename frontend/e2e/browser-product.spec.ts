import { expect, test } from "@playwright/test";

import {
  currentScenarioName,
  fixtureGame,
  wireApi,
} from "./test-fixtures";

test("Basic Product Smoke", async ({ page }) => {
  await wireApi(page);

  await page.goto("/scenarios");
  await expect(page.getByRole("link", { name: currentScenarioName })).toBeVisible();

  await page.goto("/games/new");
  const scenarioSelect = page.getByLabel("场景");
  const scenarioOption = scenarioSelect.locator("option", { hasText: currentScenarioName });
  await expect(scenarioOption).toHaveCount(1);
  await scenarioSelect.selectOption(await scenarioOption.getAttribute("value") ?? "");

  const versionSelect = page.getByLabel("已发布版本");
  await expect(versionSelect.locator("option").nth(1)).toBeAttached();
  await versionSelect.selectOption({ index: 1 });
  await page.getByRole("button", { name: "创建游戏" }).click();
  await page.waitForURL(/\/games\/[0-9a-f-]{36}$/);

  await expect(page.getByRole("heading", { name: "已知世界", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "任务执行记录", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "计划演进", exact: true })).toBeVisible();
  await expect(page.getByTestId("goal-composer")).toBeVisible();
  await expect(page.getByText("等待下达第一个目标。")).toBeVisible();
});

test("PLAY Presentation Smoke", async ({ page }) => {
  await wireApi(page);
  const fixture = fixtureGame("presentation");

  await page.goto(`/games/${fixture.gameId}`);

  await expect(page.getByRole("heading", { name: "任务执行记录", exact: true })).toBeVisible();
  await expect(page.locator(".task-tabs button")).toHaveCount(1);
  await expect(page.getByText("E2E presentation history", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".plan-history-card")).toBeVisible();
  await expect(page.locator(".plan-history-steps li.completed")).toBeVisible();
  await expect(page.locator(".timeline-entry")).toHaveCount(3);
  await expect(page.locator(".timeline-entry").filter({ hasText: "检查状态" })).toBeVisible();
  await expect(page.locator(".action-debrief")).toBeVisible();
  await expect(page.locator(".action-debrief")).toContainText("新获知识");
  await expect(page.getByRole("heading", { name: "已知世界", exact: true })).toBeVisible();
  await expect(page.getByText("中央通信枢纽").first()).toBeVisible();
});