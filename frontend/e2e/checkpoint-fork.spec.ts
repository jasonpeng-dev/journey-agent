import { expect, test } from "@playwright/test";

type GameSummary = {
  id: string;
  scenario_version_id: string;
  scenario_version_number: number;
  scenario_content_hash: string;
  status: string;
  runtime_revision: number;
  active_task_id: string | null;
};

type GameHistory = {
  tasks: unknown[];
  operations: unknown[];
  decisions: unknown[];
};

async function fillCustomGoal(page: import("@playwright/test").Page, value: string) {
  const objectiveSelect = page.getByLabel("选择任务");
  await expect(objectiveSelect).toBeEnabled();
  await objectiveSelect.selectOption("__custom_goal__");
  await page.getByLabel("自定义目标").fill(value);
}

async function wireApi(page: import("@playwright/test").Page) {
  const apiOrigin = process.env.E2E_API_ORIGIN;
  await page.route("**/api/**", async (route) => {
    const requestUrl = route.request().url();
    const url = apiOrigin
      ? requestUrl.replace("http://127.0.0.1:4173", apiOrigin)
      : requestUrl;
    const response = await route.fetch({ url });
    await route.fulfill({ response });
  });
}

async function apiUrl(path: string) {
  return (process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:4173").replace(/\/$/, "") + path;
}

async function getJson<T>(page: import("@playwright/test").Page, path: string): Promise<T> {
  const response = await page.request.get(await apiUrl(path));
  if (!response.ok()) {
    throw new Error(response.status() + " " + path);
  }
  return response.json() as Promise<T>;
}

async function createStableGame(page: import("@playwright/test").Page) {
  await page.goto("/games/new");
  const scenarioSelect = page.getByLabel("场景");
  const scenarioOption = scenarioSelect
    .locator("option")
    .filter({ hasText: /Starfire Strategic Command|星火战略指挥部/ })
    .last();
  await scenarioSelect.selectOption(await scenarioOption.getAttribute("value") ?? "");
  await page.getByLabel("已发布版本").selectOption({ index: 1 });
  await page.getByRole("button", { name: "创建游戏" }).click();
  await page.waitForURL(/\/games\/[0-9a-f-]{36}$/);
  return new URL(page.url()).pathname.split("/").at(-1) ?? "";
}

async function cardFor(page: import("@playwright/test").Page, gameId: string) {
  const card = page
    .locator(".game-card")
    .filter({ hasText: "游戏 " + gameId.slice(0, 8) })
    .first();
  await expect(card).toBeVisible();
  return card;
}

test("归档游戏可从卡片和详情 Fork，目标隔离且源删除不影响目标", async ({ page }) => {
  test.setTimeout(120000);
  await wireApi(page);

  const sourceId = await createStableGame(page);
  const sourceBeforeArchive = await getJson<GameSummary>(page, "/api/v1/games/" + sourceId);
  expect(sourceBeforeArchive.status).toBe("ACTIVE");
  expect(sourceBeforeArchive.active_task_id).toBeNull();

  await page.getByRole("button", { name: "结束并归档游戏" }).click();
  await expect(page.getByRole("button", { name: "以此归档状态新开一局" })).toBeVisible();
  const archivedSource = await getJson<GameSummary>(page, "/api/v1/games/" + sourceId);
  expect(archivedSource.status).toBe("ARCHIVED");
  expect(archivedSource.runtime_revision).toBe(sourceBeforeArchive.runtime_revision + 1);
  const sourceHistory = await getJson<GameHistory>(page, "/api/v1/games/" + sourceId + "/history");
  expect(sourceHistory.tasks).toHaveLength(0);
  expect(sourceHistory.operations).toHaveLength(0);
  expect(sourceHistory.decisions).toHaveLength(0);

  await page.goto("/games");
  const sourceCard = await cardFor(page, sourceId);
  await sourceCard.getByRole("button", { name: "以归档状态新开一局" }).click();
  await page.waitForURL(/\/games\/[0-9a-f-]{36}$/);
  const targetB = new URL(page.url()).pathname.split("/").at(-1) ?? "";
  expect(targetB).not.toBe(sourceId);
  const targetBSummary = await getJson<GameSummary>(page, "/api/v1/games/" + targetB);
  expect(targetBSummary.status).toBe("ACTIVE");
  expect(targetBSummary.runtime_revision).toBe(1);
  expect(targetBSummary.scenario_version_id).toBe(sourceBeforeArchive.scenario_version_id);
  expect(targetBSummary.scenario_version_number).toBe(sourceBeforeArchive.scenario_version_number);
  expect(targetBSummary.scenario_content_hash).toBe(sourceBeforeArchive.scenario_content_hash);
  await expect(page.getByTestId("goal-composer")).toBeVisible();
  await expect(page.locator(".current-report-panel")).toHaveCount(0);
  const targetBHistoryBeforeMutation = await getJson<GameHistory>(page, "/api/v1/games/" + targetB + "/history");
  expect(targetBHistoryBeforeMutation.tasks).toHaveLength(0);
  expect(targetBHistoryBeforeMutation.operations).toHaveLength(0);
  expect(targetBHistoryBeforeMutation.decisions).toHaveLength(0);

  await fillCustomGoal(page, "gather valley intelligence");
  await page.getByRole("button", { name: "开始目标" }).click();
  await expect(page.getByTestId("goal-accepted-card")).toBeVisible();
  const targetBHistoryAfterMutation = await getJson<GameHistory>(page, "/api/v1/games/" + targetB + "/history");
  expect(targetBHistoryAfterMutation.tasks).toHaveLength(1);
  const sourceAfterTargetMutation = await getJson<GameSummary>(page, "/api/v1/games/" + sourceId);
  expect(sourceAfterTargetMutation.status).toBe("ARCHIVED");
  expect(sourceAfterTargetMutation.active_task_id).toBeNull();
  const sourceHistoryAfterTargetMutation = await getJson<GameHistory>(page, "/api/v1/games/" + sourceId + "/history");
  expect(sourceHistoryAfterTargetMutation.tasks).toHaveLength(0);

  await page.goto("/games");
  const sourceCardForC = await cardFor(page, sourceId);
  await sourceCardForC.getByRole("button", { name: "以归档状态新开一局" }).click();
  await page.waitForURL(/\/games\/[0-9a-f-]{36}$/);
  const targetC = new URL(page.url()).pathname.split("/").at(-1) ?? "";
  expect(targetC).not.toBe(sourceId);
  expect(targetC).not.toBe(targetB);
  const targetCSummary = await getJson<GameSummary>(page, "/api/v1/games/" + targetC);
  expect(targetCSummary.status).toBe("ACTIVE");
  expect(targetCSummary.scenario_version_id).toBe(sourceBeforeArchive.scenario_version_id);
  expect((await getJson<GameHistory>(page, "/api/v1/games/" + targetC + "/history")).tasks).toHaveLength(0);

  await page.goto("/games/" + sourceId);
  await expect(page.getByRole("button", { name: "以此归档状态新开一局" })).toBeVisible();
  await page.getByRole("button", { name: "以此归档状态新开一局" }).click();
  await page.waitForURL((url) => url.pathname !== "/games/" + sourceId);
  const targetD = new URL(page.url()).pathname.split("/").at(-1) ?? "";
  expect(targetD).not.toBe(sourceId);
  expect(targetD).not.toBe(targetB);
  expect(targetD).not.toBe(targetC);
  const targetDSummary = await getJson<GameSummary>(page, "/api/v1/games/" + targetD);
  expect(targetDSummary.status).toBe("ACTIVE");
  expect(targetDSummary.scenario_version_id).toBe(sourceBeforeArchive.scenario_version_id);
  expect((await getJson<GameHistory>(page, "/api/v1/games/" + targetD + "/history")).tasks).toHaveLength(0);

  await page.goto("/games");
  const sourceCardForDelete = await cardFor(page, sourceId);
  page.once("dialog", (dialog) => dialog.accept());
  await sourceCardForDelete.getByRole("button", { name: "永久删除" }).click();
  await expect(sourceCardForDelete).toHaveCount(0);
  expect((await page.request.get(await apiUrl("/api/v1/games/" + sourceId))).status()).toBe(404);

  for (const targetId of [targetB, targetC, targetD]) {
    const target = await getJson<GameSummary>(page, "/api/v1/games/" + targetId);
    expect(target.status).toBe("ACTIVE");
    expect(target.runtime_revision).toBe(1);
    expect(target.scenario_version_id).toBe(sourceBeforeArchive.scenario_version_id);
  }
  expect((await getJson<GameHistory>(page, "/api/v1/games/" + targetB + "/history")).tasks).toHaveLength(1);
  expect((await getJson<GameHistory>(page, "/api/v1/games/" + targetC + "/history")).tasks).toHaveLength(0);
  expect((await getJson<GameHistory>(page, "/api/v1/games/" + targetD + "/history")).tasks).toHaveLength(0);
});
