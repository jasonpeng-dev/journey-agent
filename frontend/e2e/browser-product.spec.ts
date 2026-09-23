import { expect, test } from "@playwright/test";

import {
  currentScenarioName,
  fixtureGame,
  getJson,
  wireApi,
} from "./test-fixtures";

type ScenarioSummary = { id: string; name: string };

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
  await expect(page.locator(".timeline-entry").filter({ hasText: fixture.actionName })).toBeVisible();
  await expect(page.locator(".action-debrief")).toBeVisible();
  await expect(page.locator(".action-debrief")).toContainText("新获知识");
  await expect(page.getByRole("heading", { name: "已知世界", exact: true })).toBeVisible();
  await expect(page.getByText(fixture.targetName, { exact: true }).first()).toBeVisible();
});

test("Scenario Editor preserves an unsaved Node through authoring navigation", async ({ page }) => {
  await wireApi(page);
  const scenarios = await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios");
  const scenario = scenarios.find((candidate) => candidate.name === currentScenarioName);
  expect(scenario).toBeTruthy();

  await page.goto(`/scenarios/${scenario!.id}/edit/world-entities`);
  await page.getByRole("button", { name: "＋ 节点" }).click();
  await expect(page.getByLabel("节点类型 *", { exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "创建并关联" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "前往节点类型" })).toBeVisible();
  await page.getByLabel("节点类型 *", { exact: true }).selectOption({ index: 1 });
  await expect(page.getByLabel("节点类型 *", { exact: true })).not.toHaveValue("");

  await page.getByRole("link", { name: "配置检查" }).click();
  await expect(page).toHaveURL(/\/edit\/configuration-check/);
  await page.getByRole("link", { name: "初始化", exact: true }).click();
  await expect(page).toHaveURL(/\/edit\/initialization/);
  await page.getByRole("link", { name: "全局规划指引" }).click();
  await expect(page).toHaveURL(/\/edit\/planning-instructions/);
  await page.getByRole("link", { name: "世界实体" }).click();
  await page.getByText("New node", { exact: true }).first().click();

  await page.getByRole("button", { name: "＋ 添加事实" }).first().click();
  await expect(page.locator('input[value="新事实"]')).toBeVisible();
  await page.getByRole("link", { name: "前往初始化", exact: true }).click();
  await expect(page).toHaveURL(/\/edit\/initialization/);
  await page.getByRole("link", { name: "世界实体" }).click();
  await page.getByText("New node", { exact: true }).first().click();
  await page.getByRole("button", { name: "删除节点" }).click();
  const dialog = page.getByRole("dialog", { name: "删除「New node」？" });
  await dialog.getByRole("button", { name: "删除" }).click();
  await expect(page.locator(".object-list-item", { hasText: "New node" })).toHaveCount(0);
  await expect(page.getByText("有未保存修改")).toHaveCount(0);
});
