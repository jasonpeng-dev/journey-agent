import { expect, test, type Page } from "@playwright/test";
import { getJson, wireApi } from "./test-fixtures";

type JsonObject = Record<string, unknown>;
type ScenarioSummary = { id: string; key: string; name: string; draft_revision: number; current_published_version_id: string | null };
type ScenarioVersion = { id: string; version_number: number; schema_version: number };
type DraftPayload = { revision: number; definition_document: JsonObject };

const portabilityKey = "scenario_portability_e2e";

function apiOrigin(): string {
  return (process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:4173").replace(/\/$/, "");
}

async function sourceScenario(page: Page): Promise<ScenarioSummary> {
  const scenarios = await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios");
  const scenario = scenarios.find((candidate) => candidate.key === portabilityKey);
  expect(scenario, `missing ${portabilityKey} E2E fixture`).toBeTruthy();
  return scenario!;
}

async function fetchArtifact(page: Page, endpoint: string): Promise<JsonObject> {
  const response = await page.request.get(`${apiOrigin()}${endpoint}`);
  expect(response.ok(), `${response.status()} ${endpoint}`).toBe(true);
  return response.json() as Promise<JsonObject>;
}

async function uploadArtifact(dialog: ReturnType<Page["getByRole"]>, artifact: JsonObject, fileName: string) {
  await dialog.locator('input[type="file"]').setInputFiles({ name: fileName, mimeType: "application/json", buffer: Buffer.from(JSON.stringify(artifact), "utf8") });
}

async function importAsNew(page: Page, artifact: JsonObject, targetKey: string, fileName: string): Promise<ScenarioSummary> {
  await page.goto("/scenarios");
  await page.getByRole("button", { name: "导入场景", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导入场景" });
  await uploadArtifact(dialog, artifact, fileName);
  await expect(dialog.getByRole("textbox", { name: "目标 Scenario key" })).toHaveValue(String((artifact.scenario as JsonObject).key));
  await dialog.getByRole("textbox", { name: "目标 Scenario key" }).fill(targetKey);
  await dialog.getByRole("button", { name: "检查目标 key", exact: true }).click();
  await expect(dialog.locator("code").filter({ hasText: targetKey })).toBeVisible();
  await dialog.getByRole("button", { name: "确认导入", exact: true }).click();
  await expect.poll(async () => (await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios")).find((candidate) => candidate.key === targetKey)).toBeTruthy();
  return (await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios")).find((candidate) => candidate.key === targetKey)!;
}

test("external artifact import always creates a new Scenario and never offers existing Draft", async ({ page }) => {
  await wireApi(page);
  const source = await sourceScenario(page);
  const draftArtifact = await fetchArtifact(page, `/api/v1/scenarios/${source.id}/draft/artifact`);
  await page.goto("/scenarios");
  await page.getByRole("button", { name: "导入场景", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导入场景" });
  await uploadArtifact(dialog, draftArtifact, "conflict.draft.scenario.json");
  await expect(dialog.getByText("该场景 key 已存在。导入始终创建新的场景，请指定一个未使用的 key。", { exact: true })).toBeVisible();
  await expect(dialog.getByText("导入到已有场景草稿")).toHaveCount(0);
  const imported = await importAsNew(page, draftArtifact, "portability_draft_import_e2e", "draft.scenario.json");
  await expect(page).toHaveURL(new RegExp(`/scenarios/${imported.id}/edit/overview$`));
  expect(await getJson<ScenarioVersion[]>(page, `/api/v1/scenarios/${imported.id}/versions`)).toHaveLength(0);
});

test("Release import starts target at Published v1 and Scenario Detail owns version export", async ({ page }) => {
  await wireApi(page);
  const source = await sourceScenario(page);
  const versions = await getJson<ScenarioVersion[]>(page, `/api/v1/scenarios/${source.id}/versions`);
  const release = versions.find((version) => version.version_number === 1)!;
  const artifact = await fetchArtifact(page, `/api/v1/scenarios/${source.id}/versions/${release.id}/artifact`);
  const imported = await importAsNew(page, artifact, "portability_release_import_e2e", "release.scenario.json");
  await expect(page).toHaveURL(new RegExp(`/scenarios/${imported.id}$`));
  await expect(page.getByTestId("latest-artifact-export")).toBeVisible();
  await expect(page.getByText("用此版本开局", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("draft-artifact-export")).toHaveCount(0);
  expect(await getJson<ScenarioVersion[]>(page, `/api/v1/scenarios/${imported.id}/versions`)).toHaveLength(1);
});

test("historical restore previews and hands a candidate to a dirty Editor without persistence", async ({ page }) => {
  await wireApi(page);
  const source = await sourceScenario(page);
  const versions = await getJson<ScenarioVersion[]>(page, `/api/v1/scenarios/${source.id}/versions`);
  const version = versions.at(-1)!;
  await page.goto(`/scenarios/${source.id}`);
  const persistedBefore = await getJson<DraftPayload>(page, `/api/v1/scenarios/${source.id}/draft`);
  const versionCard = page.getByRole("article").filter({ hasText: `版本 ${version.version_number}` });
  await versionCard.getByRole("button", { name: "恢复到当前草稿", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: `恢复版本 v${version.version_number}` });
  const generatePreview = dialog.getByRole("button", { name: "生成恢复预览", exact: true });
  if (await generatePreview.count()) await generatePreview.click();
  await expect(dialog).toContainText("将版本");
  await dialog.getByRole("button", { name: /载入当前草稿工作副本|返回编辑器/ }).click();
  await expect(page).toHaveURL(new RegExp(`/scenarios/${source.id}/edit/overview$`));
  await expect(page.getByText("有未保存修改", { exact: true })).toBeVisible();
  expect((await getJson<DraftPayload>(page, `/api/v1/scenarios/${source.id}/draft`)).revision).toBe(persistedBefore.revision);
});

test("historical restore can be discarded without rehydrating the transient candidate", async ({ page }) => {
  await wireApi(page);
  const source = await sourceScenario(page);
  const versions = await getJson<ScenarioVersion[]>(page, `/api/v1/scenarios/${source.id}/versions`);
  const version = versions.at(-1)!;
  const persistedBefore = await getJson<DraftPayload>(page, `/api/v1/scenarios/${source.id}/draft`);
  await page.goto(`/scenarios/${source.id}`);
  const versionCard = page.getByRole("article").filter({ hasText: `版本 ${version.version_number}` });
  await versionCard.getByRole("button").first().click();
  const dialog = page.getByRole("dialog");
  const previewButton = dialog.locator("button.editor-button-secondary").filter({ hasText: /生成恢复预览/ });
  if (await previewButton.count()) await previewButton.click();
  await dialog.locator("button.editor-button-primary").last().click();
  await expect(page).toHaveURL(new RegExp(`/scenarios/${source.id}/edit/overview$`));
  await expect(page.locator(".save-state.dirty")).toBeVisible();

  await page.locator(".editor-heading-actions > button").nth(2).click();
  const discardDialog = page.locator('[role="dialog"]');
  await discardDialog.locator("button.editor-button-danger").click();
  await expect(page.locator(".save-state.unchanged")).toBeVisible();
  await expect(page.locator(".save-state.dirty")).toHaveCount(0);
  await expect.poll(async () => (await getJson<DraftPayload>(page, `/api/v1/scenarios/${source.id}/draft`)).revision).toBe(persistedBefore.revision);
  await page.waitForTimeout(250);
  await expect(page.locator(".save-state.dirty")).toHaveCount(0);
});

test("historical restore saves once and remains clean", async ({ page }) => {
  await wireApi(page);
  const source = await sourceScenario(page);
  const versions = await getJson<ScenarioVersion[]>(page, `/api/v1/scenarios/${source.id}/versions`);
  const version = versions.at(-1)!;
  const persistedBefore = await getJson<DraftPayload>(page, `/api/v1/scenarios/${source.id}/draft`);
  await page.goto(`/scenarios/${source.id}`);
  const versionCard = page.getByRole("article").filter({ hasText: `版本 ${version.version_number}` });
  await versionCard.getByRole("button").first().click();
  const dialog = page.getByRole("dialog");
  const previewButton = dialog.locator("button.editor-button-secondary").filter({ hasText: /生成恢复预览/ });
  if (await previewButton.count()) await previewButton.click();
  await dialog.locator("button.editor-button-primary").last().click();
  await expect(page).toHaveURL(new RegExp(`/scenarios/${source.id}/edit/overview$`));
  await expect(page.locator(".save-state.dirty")).toBeVisible();

  await page.locator(".editor-heading-actions > button").nth(1).click();
  await expect.poll(async () => (await getJson<DraftPayload>(page, `/api/v1/scenarios/${source.id}/draft`)).revision).toBe(persistedBefore.revision + 1);
  await expect(page.locator(".save-state.unchanged")).toBeVisible();
  await expect(page.locator(".save-state.dirty")).toHaveCount(0);
  await page.waitForTimeout(250);
  await expect.poll(async () => (await getJson<DraftPayload>(page, `/api/v1/scenarios/${source.id}/draft`)).revision).toBe(persistedBefore.revision + 1);
});

test("Editor retains Draft export and removes artifact import entry", async ({ page }) => {
  await wireApi(page);
  const source = await sourceScenario(page);
  await page.goto(`/scenarios/${source.id}/edit/overview`);
  await expect(page.getByRole("button", { name: "导出当前草稿", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "导入到当前草稿", exact: true })).toHaveCount(0);
});

test("V3 Published version creates an exact-pinned Game and exposes a non-submitting quick input select", async ({ page }) => {
  await wireApi(page);
  const source = await sourceScenario(page);
  const versions = await getJson<ScenarioVersion[]>(page, `/api/v1/scenarios/${source.id}/versions`);
  const historical = versions.find((version) => version.version_number === 1)!;
  const latest = versions.find((version) => version.version_number === 2)!;

  await page.goto(`/games/new?scenarioId=${source.id}&versionId=${latest.id}`);
  await expect(page.locator("select").nth(0)).toHaveValue(source.id);
  await expect(page.locator("select").nth(1)).toHaveValue(latest.id);
  await page.locator(".form-card > button").click();
  await page.waitForURL(/\/games\/[0-9a-f-]{36}$/);

  const gameId = new URL(page.url()).pathname.split("/").at(-1)!;
  const summary = await getJson<{ scenario_version_id: string }>(page, `/api/v1/games/${gameId}`);
  expect(summary.scenario_version_id).toBe(latest.id);
  const play = await getJson<{ scenario_metadata: { quick_inputs: string[] } }>(page, `/api/v1/games/${gameId}/play`);
  expect(play.scenario_metadata.quick_inputs).toEqual(["Portability browser fixture goal v2"]);
  let goalSubmissions = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && /\/goals$/.test(request.url())) goalSubmissions += 1;
  });
  const quickInput = page.getByRole("combobox", { name: "选择快捷目标" });
  await expect(quickInput).toBeVisible();
  await quickInput.selectOption({ label: "Portability browser fixture goal v2" });
  await expect(page.locator("#goal")).toHaveValue("Portability browser fixture goal v2");
  expect(goalSubmissions).toBe(0);

  const historicalGameResponse = await page.request.post(`${apiOrigin()}/api/v1/games`, {
    data: {
      scenario_id: source.id,
      scenario_version_id: historical.id,
      idempotency_key: `portability-historical-${Date.now()}`,
    },
  });
  expect(historicalGameResponse.status()).toBe(201);
  const historicalGame = await historicalGameResponse.json() as { id: string };
  const historicalPlay = await getJson<{ scenario_metadata: { quick_inputs: string[] } }>(page, `/api/v1/games/${historicalGame.id}/play`);
  expect(historicalPlay.scenario_metadata.quick_inputs).toEqual(["Portability browser fixture goal v1"]);
});

test("a V3 Published scenario with no quick inputs keeps free-text goals available", async ({ page }) => {
  await wireApi(page);
  const scenarios = await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios");
  const empty = scenarios.find((candidate) => candidate.key === "scenario_portability_empty_quick_inputs_e2e");
  expect(empty).toBeTruthy();
  const versions = await getJson<ScenarioVersion[]>(page, `/api/v1/scenarios/${empty!.id}/versions`);
  const version = versions[0];
  await page.goto(`/games/new?scenarioId=${empty!.id}&versionId=${version.id}`);
  await expect(page.locator("select").nth(1)).toHaveValue(version.id);
  await page.locator(".form-card > button").click();
  await page.waitForURL(/\/games\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("combobox", { name: "选择快捷目标" })).toHaveCount(0);
  await expect(page.locator("#goal")).toBeVisible();
});

test("Scenario delete without Games removes the imported Scenario series", async ({ page }) => {
  await wireApi(page);
  const source = await sourceScenario(page);
  const artifact = await fetchArtifact(page, `/api/v1/scenarios/${source.id}/draft/artifact`);
  const imported = await importAsNew(page, artifact, "portability_delete_without_game_e2e", "delete.draft.scenario.json");

  await page.goto(`/scenarios/${imported.id}`);
  await page.getByTestId("delete-scenario").click();
  const dialog = page.getByRole("dialog", { name: new RegExp(`删除「${imported.name}」`) });
  await expect(dialog.getByText("此操作将永久删除整个场景及全部版本")).toBeVisible();
  await dialog.getByRole("button", { name: "永久删除当前场景", exact: true }).click();
  await expect(page).toHaveURL(/\/scenarios$/);
  const deleted = await page.request.get(`${apiOrigin()}/api/v1/scenarios/${imported.id}`);
  expect(deleted.status()).toBe(404);
});

test("Scenario delete with a Game blocks and highlights the dependent instance", async ({ page }) => {
  await wireApi(page);
  const source = await sourceScenario(page);
  const versions = await getJson<ScenarioVersion[]>(page, `/api/v1/scenarios/${source.id}/versions`);
  const latest = versions.find((version) => version.version_number === 2) ?? versions[0];
  const gameResponse = await page.request.post(`${apiOrigin()}/api/v1/games`, {
    data: { scenario_version_id: latest.id, idempotency_key: `portability-delete-${Date.now()}` },
  });
  expect(gameResponse.status()).toBe(201);
  const game = await gameResponse.json() as { id: string };

  await page.goto(`/scenarios/${source.id}`);
  await page.getByTestId("delete-scenario").click();
  const dialog = page.getByRole("dialog", { name: new RegExp(`删除「${source.name}」`) });
  await expect(dialog.getByText("该场景仍被游戏实例使用。请先手动删除这些游戏实例。")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "永久删除当前场景", exact: true })).toHaveCount(0);
  await dialog.locator(`a[href="/games?highlight=${game.id}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/games\\?highlight=${game.id}$`));
  await expect(page.locator(`[data-game-id="${game.id}"]`)).toHaveClass(/game-card-highlight/);
});
