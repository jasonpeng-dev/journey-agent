import { expect, test, type Page } from "@playwright/test";

import {
  currentScenarioName,
  fixtureGame,
  getJson,
  revealTargetApplicabilityFixture,
  targetApplicabilityFixture,
  wireApi,
} from "./test-fixtures";

type ScenarioSummary = { id: string; name: string };
type DraftPayload = { definition_document: Record<string, unknown> };
type PlayerTargetContract = { action_key: string; target_key: string };
type PlayerState = { known_target_action_contracts?: PlayerTargetContract[] };

async function createNode(page: Page, key: string, name?: string) {
  await page.getByRole("button", { name: "＋ 节点" }).click();
  await page.getByLabel("\u7a33\u5b9a\u952e *", { exact: true }).fill(key);
  await page.getByRole("button", { name: "创建", exact: true }).click();
  if (name) await page.locator(`[data-field-path="node.${key}.name"] input`).fill(name);
  await page.locator(`[data-field-path="node.${key}.node_type_key"] select`).selectOption({ index: 1 });
}

async function setNodeInitialization(page: Page, key: string) {
  await page.getByRole("link", { name: "前往初始化", exact: true }).click();
  await expect(page).toHaveURL(/\/edit\/initialization/);
  await page.locator(`[data-field-path="world.nodes.${key}.initial_access"] select`).selectOption("choice:0");
  await page.locator(`[data-field-path="world.nodes.${key}.initial_visibility"] select`).selectOption("choice:0");
}

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
  let draftWrites = 0;
  page.on("request", (request) => {
    if (request.method() === "PUT" && /\/api\/v1\/scenarios\/[^/]+\/draft$/.test(request.url())) draftWrites += 1;
  });

  await page.goto(`/scenarios/${scenario!.id}/edit/world-entities`);
  await page.getByRole("button", { name: "＋ 节点" }).click();
  await page.getByLabel("\u7a33\u5b9a\u952e *", { exact: true }).fill("e2e_unsaved_node");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  const initializationPreview = page.getByRole("region", { name: "开局配置只读预览" });
  await expect(initializationPreview.getByText("暂时无法生成完整开局配置")).toBeVisible();
  await expect(initializationPreview.getByText(/填写显示名称/)).toBeVisible();
  await expect(initializationPreview.locator("input, select, textarea")).toHaveCount(0);
  await expect(page.getByText("有未保存修改")).toBeVisible();

  await page.getByRole("link", { name: "配置检查", exact: true }).click();
  await expect(page).toHaveURL(/\/edit\/configuration-check/);
  await expect(page.getByText(/当前工作副本有 \d+ 项待处理配置/)).toBeVisible();
  const nodeTypeIssue = page.locator(".global-issue")
    .filter({ hasText: "节点「e2e_unsaved_node」" })
    .filter({ hasText: "节点类型" });
  await expect(nodeTypeIssue).toHaveCount(1);
  await expect(nodeTypeIssue.getByText("必须处理")).toBeVisible();
  await nodeTypeIssue.getByRole("link", { name: "定位到节点类型" }).click();
  await expect(page).toHaveURL(new RegExp(`/edit/world-entities/e2e_unsaved_node\\?focus_path=node_type_key$`));
  const globallyLocatedNodeType = page.locator('[data-field-path="node.e2e_unsaved_node.node_type_key"]');
  await expect(globallyLocatedNodeType.locator("select")).toBeFocused();
  await expect(globallyLocatedNodeType).toHaveClass(/is-focus-highlighted/);
  await expect(page.getByText("有未保存修改")).toBeVisible();

  await page.locator('[data-field-path="node.e2e_unsaved_node.name"] input').fill("E2E unsaved node");
  await page.locator('[data-field-path="node.e2e_unsaved_node.node_type_key"] select').selectOption({ index: 1 });
  await page.setViewportSize({ width: 390, height: 844 });
  const nodeTypeActions = page.locator('[data-field-path="node.e2e_unsaved_node.node_type_key"] .field-action-row');
  await expect(nodeTypeActions).toBeVisible();
  await expect(nodeTypeActions).toHaveCSS("flex-wrap", "wrap");
  const actionRowFits = await nodeTypeActions.evaluate((row) => row.scrollWidth <= row.clientWidth + 1);
  expect(actionRowFits).toBeTruthy();
  await page.setViewportSize({ width: 1280, height: 900 });

  const initialAccessAction = initializationPreview.locator('a[href$="focus_path=world.nodes.e2e_unsaved_node.initial_access"]');
  await expect(initialAccessAction).toBeVisible();
  await expect(initializationPreview.locator('a[href$="focus_path=world.nodes.e2e_unsaved_node.initial_visibility"]')).toBeVisible();
  await expect(initialAccessAction).toHaveAttribute("href", new RegExp(`/scenarios/${scenario!.id}/edit/initialization\\?domain=nodes&group=node-type%3A.*&item=node%3Ae2e_unsaved_node&focus_path=world.nodes.e2e_unsaved_node.initial_access`));
  await initialAccessAction.click();
  await expect(page).toHaveURL(new RegExp(`/edit/initialization\\?domain=nodes&group=node-type%3A.*&item=node%3Ae2e_unsaved_node&focus_path=world.nodes.e2e_unsaved_node.initial_access`));
  const nodeAccessField = page.locator('[data-field-path="world.nodes.e2e_unsaved_node.initial_access"] select');
  await expect(nodeAccessField).toBeFocused();
  const issueTrigger = page.getByRole("button", { name: /查看问题（\d+）/ });
  const initialIssueCount = Number((await issueTrigger.innerText()).match(/\d+/)?.[0]);
  await nodeAccessField.selectOption("choice:0");
  await expect.poll(async () => Number((await page.getByRole("button", { name: /查看问题（\d+）/ }).innerText()).match(/\d+/)?.[0])).toBeLessThan(initialIssueCount);
  await expect(page.getByText("有未保存修改")).toBeVisible();

  await page.getByRole("button", { name: "查看问题（1）" }).click();
  const issueDialog = page.getByRole("dialog", { name: "待完善配置" });
  const nodeIssueGroup = issueDialog.getByRole("region", { name: /世界实体 · E2E unsaved node/ });
  await expect(nodeIssueGroup.getByText("初始可见性", { exact: true })).toBeVisible();
  const visibilityIssueRow = nodeIssueGroup.locator("li").filter({ hasText: "初始可见性" });
  await visibilityIssueRow.getByRole("link", { name: "前往", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/edit/initialization\\?domain=nodes&group=node-type%3A.*&item=node%3Ae2e_unsaved_node&focus_path=world.nodes.e2e_unsaved_node.initial_visibility`));
  await expect(issueDialog).toHaveCount(0);
  const nodeVisibility = page.locator('[data-field-path="world.nodes.e2e_unsaved_node.initial_visibility"] select');
  await expect(nodeVisibility).toBeFocused();
  await nodeVisibility.selectOption("choice:0");
  await expect(page.getByRole("button", { name: /查看问题/ })).toHaveCount(0);
  await expect(page.getByTestId("initialization-four-panel-workspace").locator("xpath=preceding-sibling::*[1]")).toHaveClass("initialization-workspace-header");
  await expect(page.getByText("有未保存修改")).toBeVisible();

  await page.getByRole("link", { name: "世界实体", exact: true }).click();
  await page.getByText("E2E unsaved node", { exact: true }).first().click();
  const readonlyInitialization = page.getByRole("region", { name: "开局配置只读预览" });
  await expect(readonlyInitialization.getByText("节点访问状态")).toBeVisible();
  await expect(readonlyInitialization.getByText("可用")).toBeVisible();
  await expect(readonlyInitialization.getByText("已知")).toBeVisible();
  await expect(readonlyInitialization.locator("select, input, textarea")).toHaveCount(0);

  await page.getByRole("link", { name: "配置检查" }).click();
  await expect(page).toHaveURL(/\/edit\/configuration-check/);
  await page.getByRole("link", { name: "初始化", exact: true }).click();
  await expect(page).toHaveURL(/\/edit\/initialization/);
  await page.getByRole("link", { name: "全局规划指引" }).click();
  await expect(page).toHaveURL(/\/edit\/planning-instructions/);
  await page.getByRole("link", { name: "世界实体" }).click();
  await page.getByText("E2E unsaved node", { exact: true }).first().click();

  await page.getByRole("button", { name: "＋ 添加事实" }).first().click();
  await page.getByLabel("\u4e8b\u5b9e\u952e *", { exact: true }).fill("e2e_unsaved_fact");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await page.locator('[data-field-path="node.e2e_unsaved_node.facts.0.name"] input').fill("E2E unsaved fact");
  await page.locator('[id^="fact-card-e2e_unsaved_node-key"]').getByRole("link", { name: "前往初始化", exact: true }).click();
  await expect(page).toHaveURL(/\/edit\/initialization/);
  const nodeAccess = page.locator('[data-field-path="world.nodes.e2e_unsaved_node.initial_access"] select');
  await expect(nodeAccess.locator("option[value='']")).toHaveCount(0);
  await expect(nodeAccess.locator("option")).toHaveText(["可用", "锁定"]);
  await expect(nodeAccess).toHaveValue("choice:0");
  await nodeAccess.selectOption("choice:1");
  await expect(page.getByTestId("initialization-four-panel-workspace")).toBeVisible();
  await page.getByRole("link", { name: "世界实体" }).click();
  await page.getByText("E2E unsaved node", { exact: true }).first().click();
  await page.getByRole("button", { name: "删除节点" }).click();
  const dialog = page.getByRole("dialog", { name: "删除「E2E unsaved node」？" });
  await dialog.getByRole("button", { name: "删除" }).click();
  await expect(page.locator(".object-list-item", { hasText: "E2E unsaved node" })).toHaveCount(0);
  await expect(page.getByText("有未保存修改")).toHaveCount(0);
  expect(draftWrites).toBe(0);
});

test("Configuration Check activates an exact nested ExpectedOutcome focus target", async ({ page }) => {
  await wireApi(page);
  const scenarios = await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios");
  const scenario = scenarios.find((candidate) => candidate.name === currentScenarioName);
  expect(scenario).toBeTruthy();
  let draftWrites = 0;
  page.on("request", (request) => {
    if (request.method() === "PUT" && /\/api\/v1\/scenarios\/[^/]+\/draft$/.test(request.url())) draftWrites += 1;
  });

  await page.goto(`/scenarios/${scenario!.id}/edit/actions`);
  await page.getByRole("button", { name: "＋ 行动" }).click();
  const actionDialog = page.getByRole("dialog", { name: "\u521b\u5efa\u884c\u52a8" });
  await actionDialog.getByLabel("\u7a33\u5b9a\u952e *", { exact: true }).fill("repair_facility");
  await actionDialog.getByRole("button", { name: "创建", exact: true }).click();

  await page.getByRole("button", { name: "＋ 添加结果" }).click();
  const outcomeDialog = page.getByRole("dialog", { name: "创建结果" });
  await outcomeDialog.getByLabel("结果代码 *", { exact: true }).fill("COMMUNICATIONS_REPAIRED");
  await outcomeDialog.getByRole("button", { name: "创建", exact: true }).click();
  const createdOutcome = page.locator('[data-focus-path="action.repair_facility.expected_outcomes.COMMUNICATIONS_REPAIRED"]');
  await expect(createdOutcome).toBeVisible();
  await expect(page.getByText("有未保存修改")).toBeVisible();

  await page.getByRole("link", { name: "配置检查", exact: true }).click();
  await expect(page).toHaveURL(/\/edit\/configuration-check/);
  const outcomeNameIssue = page.locator(".global-issue")
    .filter({ has: page.locator('a[href$="focus_path=expected_outcomes.0.name"]') });
  await expect(outcomeNameIssue).toHaveCount(1);
  await expect(outcomeNameIssue.getByText("请按要求完善显示名称。")).toBeVisible();
  await outcomeNameIssue.getByRole("link", { name: "定位到显示名称" }).click();
  await expect(page).toHaveURL(/\/edit\/actions\/repair_facility\?focus_path=expected_outcomes\.0\.name$/);

  const outcomeCard = page.locator('[data-focus-path="action.repair_facility.expected_outcomes.COMMUNICATIONS_REPAIRED"]');
  await expect(outcomeCard).toBeVisible();
  await expect(outcomeCard.locator(".nested-card-toggle")).toHaveAttribute("aria-expanded", "true");
  const outcomeName = page.locator('[data-field-path="action.repair_facility.expected_outcomes.0.name"]');
  await expect(outcomeName.locator("input")).toBeFocused();
  await expect(outcomeName).toHaveClass(/is-focus-highlighted/);
  await expect(page.getByText("有未保存修改")).toBeVisible();
  expect(draftWrites).toBe(0);
});

test("Initialization preview issues stay scoped to the focused Node while the dialog stays global", async ({ page }) => {
  await wireApi(page);
  const scenarios = await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios");
  const scenario = scenarios.find((candidate) => candidate.name === currentScenarioName);
  expect(scenario).toBeTruthy();
  const nodeAKey = "e3_scope_node_a";
  const nodeBKey = "e3_scope_node_b";
  const nodeCKey = "e3_scope_node_c";

  await page.goto(`/scenarios/${scenario!.id}/edit/world-entities`);
  await createNode(page, nodeAKey, "E3 Scope Node A");
  let preview = page.getByRole("region", { name: "开局配置只读预览" });
  await expect(preview.getByText("该对象还有 2 项配置未完成")).toBeVisible();
  await expect(preview.getByText("还需要填写初始访问状态。")).toBeVisible();
  await expect(preview.getByText("还需要填写初始可见性。")).toBeVisible();

  await createNode(page, nodeBKey);
  await page.locator(`[data-field-path="node.${nodeBKey}.name"] input`).fill("");
  await setNodeInitialization(page, nodeBKey);
  await page.getByRole("link", { name: "世界实体", exact: true }).click();
  await page.locator(".object-list-item").filter({ hasText: nodeBKey }).click();
  await expect(page).toHaveURL(new RegExp(`/edit/world-entities/${nodeBKey}$`));
  preview = page.getByRole("region", { name: "开局配置只读预览" });
  await expect(preview.getByText("该对象还有 1 项配置未完成")).toBeVisible();
  await expect(preview.getByText(/填写显示名称/)).toBeVisible();
  await expect(preview.getByText("还需要填写初始访问状态。")).toHaveCount(0);
  await expect(preview.getByText("还需要填写初始可见性。")).toHaveCount(0);

  await createNode(page, nodeCKey, "E3 Scope Node C");
  await setNodeInitialization(page, nodeCKey);
  await page.getByRole("link", { name: "世界实体", exact: true }).click();
  await page.getByText("E3 Scope Node C", { exact: true }).first().click();
  await expect(page).toHaveURL(new RegExp(`/edit/world-entities/${nodeCKey}$`));
  preview = page.getByRole("region", { name: "开局配置只读预览" });
  await expect(preview.getByText("节点访问状态")).toBeVisible();
  await expect(preview.getByText("可用")).toBeVisible();
  await expect(preview.getByText("玩家知识")).toBeVisible();
  await expect(preview.getByText("已知")).toBeVisible();
  await expect(preview.locator(".initialization-preview-object-issues")).toHaveCount(0);
  await expect(preview.getByText(/草稿还有/)).toHaveCount(0);

  await page.getByRole("link", { name: "初始化", exact: true }).click();
  await expect(page).toHaveURL(/\/edit\/initialization$/);
  const trigger = page.getByRole("button", { name: "查看问题（3）" });
  await expect(trigger).toBeVisible();
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "待完善配置" });
  await expect(dialog.getByText("当前草稿还有 3 项需要处理。")).toBeVisible();
  const groupA = dialog.getByRole("region", { name: "世界实体 · E3 Scope Node A" });
  const groupB = dialog.getByRole("region", { name: nodeBKey });
  await expect(groupA.getByText("初始访问状态", { exact: true })).toBeVisible();
  await expect(groupA.getByText("初始可见性", { exact: true })).toBeVisible();
  await expect(groupB.getByText("显示名称", { exact: true })).toBeVisible();
  await groupA.locator("li").filter({ hasText: "初始访问状态" }).getByRole("link", { name: "前往", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/edit/initialization\\?domain=nodes&group=node-type%3A.*&item=node%3A${nodeAKey}&focus_path=world.nodes.${nodeAKey}.initial_access`));
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(`[data-field-path="world.nodes.${nodeAKey}.initial_access"] select`)).toBeFocused();
  await expect(page.getByText("有未保存修改")).toBeVisible();
});

test("Initialization dropdowns preserve required, nullable, and model-default semantics", async ({ page }) => {
  await wireApi(page);
  const scenarios = await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios");
  const scenario = scenarios.find((candidate) => candidate.name === currentScenarioName);
  expect(scenario).toBeTruthy();
  const draft = await getJson<{ definition_document: { world?: { resources?: unknown[] } } }>(page, `/api/v1/scenarios/${scenario!.id}/draft`);
  expect(draft.definition_document.world?.resources?.length ?? 0).toBeGreaterThan(0);

  await page.goto(`/scenarios/${scenario!.id}/edit/initialization`);
  await expect(page.getByTestId("initialization-four-panel-workspace")).toBeVisible();
  await page.getByRole("region", { name: "类别" }).getByRole("button", { name: "资源" }).click();
  await page.getByRole("region", { name: "分类" }).getByRole("button", { name: "资源池" }).click();
  await page.getByRole("region", { name: "对象" }).getByRole("button", { name: /新增资源池/ }).click();

  const dialog = page.getByRole("dialog", { name: "创建资源池" });
  await dialog.getByLabel("资源池键").fill("e2e_unsaved_pool");
  await dialog.getByRole("button", { name: "创建", exact: true }).click();

  const workspace = page.getByTestId("initialization-four-panel-workspace");
  const configuration = page.getByRole("region", { name: "初始化配置" });
  const resource = configuration.getByRole("combobox", { name: "资源定义" });
  await expect(resource.locator("option[value='']")).toHaveCount(0);
  await resource.selectOption("choice:0");
  await configuration.getByRole("combobox", { name: "所属区域" }).selectOption("__initialization_nullable_unset__");
  await configuration.getByRole("combobox", { name: "所在设施" }).selectOption("__initialization_nullable_unset__");
  await configuration.getByRole("combobox", { name: "可用状态" }).selectOption("__initialization_model_default__");
  await configuration.getByRole("combobox", { name: "可见性" }).selectOption("__initialization_model_default__");
  await configuration.locator('[data-field-path="initialization.resource_pools.e2e_unsaved_pool.quantity"] input').fill("0");

  await expect(workspace).toBeVisible();
  await expect(page.getByRole("status", { name: "待完善配置" })).toHaveCount(0);
  await expect(configuration.getByRole("combobox", { name: "所属区域" })).toHaveValue("__initialization_nullable_unset__");
  await expect(configuration.getByRole("combobox", { name: "可用状态" })).toHaveValue("__initialization_model_default__");
  await expect(configuration.getByRole("combobox", { name: "可见性" })).toHaveValue("__initialization_model_default__");
  await expect(configuration.getByText("系统默认").first()).toBeVisible();
  await expect(page.getByText("有未保存修改")).toBeVisible();
});

test("Initialization typed Facts share full-width controls and keep authored enum tokens through dirty navigation", async ({ page }) => {
  await wireApi(page);
  const scenarios = await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios");
  const scenario = scenarios.find((candidate) => candidate.name === currentScenarioName);
  expect(scenario).toBeTruthy();
  const draft = await getJson<{
    definition_document: {
      world?: {
        nodes?: Array<{
          key: string;
          name: string;
          node_type_key?: string;
          facts?: Array<{ key: string; name: string; value_type: string; initial_value?: unknown }>;
        }>;
        node_types?: Array<{ key: string; name: string }>;
      };
    };
  }>(page, `/api/v1/scenarios/${scenario!.id}/draft`);
  const nodes = draft.definition_document.world?.nodes ?? [];
  const node = nodes.find((candidate) => candidate.facts?.some((fact) => fact.value_type === "BOOLEAN" && typeof fact.initial_value === "boolean"));
  expect(node, "The generic E2E scenario should contain a Boolean Fact").toBeTruthy();
  const booleanFact = node!.facts!.find((fact) => fact.value_type === "BOOLEAN" && typeof fact.initial_value === "boolean")!;
  let draftWrites = 0;
  page.on("request", (request) => {
    if (request.method() === "PUT" && new RegExp(`/api/v1/scenarios/${scenario!.id}/draft$`).test(new URL(request.url()).pathname)) draftWrites += 1;
  });

  await page.goto(`/scenarios/${scenario!.id}/edit/initialization`);
  const nodeTypeName = draft.definition_document.world?.node_types?.find((candidate) => candidate.key === node!.node_type_key)?.name ?? node!.node_type_key ?? "";
  await page.getByRole("region", { name: "类别" }).getByRole("button", { name: "节点" }).click();
  await page.getByRole("region", { name: "分类" }).getByRole("button", { name: nodeTypeName }).click();
  await page.getByRole("region", { name: "对象" }).getByRole("button", { name: node!.name }).click();
  const booleanPath = `[data-field-path="world.nodes.${node!.key}.facts.${booleanFact.key}.initial_value"]`;
  const enumPath = `[data-field-path="world.nodes.${node!.key}.facts.e2e_binary_availability.initial_value"]`;
  const booleanField = page.locator(`${booleanPath} select`);
  const enumField = page.locator(`${enumPath} select`);
  await expect(booleanField).toBeVisible();
  await expect(enumField).toBeVisible();
  await expect(page.locator(booleanPath)).toHaveClass(/initialization-field/);
  await expect(page.locator(enumPath)).toHaveClass(/initialization-field/);
  await expect(booleanField.locator("option")).toHaveText(["是", "否"]);
  await expect(enumField.locator("option")).toHaveText(["是", "否"]);
  await expect(page.getByRole("radio")).toHaveCount(0);
  const fieldWidths = await Promise.all([
    booleanField.evaluate((element) => element.getBoundingClientRect().width),
    enumField.evaluate((element) => element.getBoundingClientRect().width),
  ]);
  expect(Math.abs(fieldWidths[0] - fieldWidths[1])).toBeLessThanOrEqual(1);

  const nextBoolean = booleanFact.initial_value === true ? "false" : "true";
  await booleanField.selectOption(nextBoolean);
  await enumField.selectOption("choice:1");
  await expect(page.getByText("有未保存修改")).toBeVisible();

  await page.getByRole("link", { name: "世界实体", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/edit/world-entities$`));
  await page.getByText(node!.name, { exact: true }).first().click();
  await expect(page).toHaveURL(new RegExp(`/edit/world-entities/${node!.key}$`));
  await page.getByRole("link", { name: "前往初始化", exact: true }).click();
  await expect(booleanField).toHaveValue(nextBoolean);
  await expect(enumField).toHaveValue("choice:1");
  await expect(page.getByText("有未保存修改")).toBeVisible();
  expect(draftWrites).toBe(0);
});

test("Initialization Derived State uses the Other overview and readonly truth/knowledge fields", async ({ page }) => {
  await wireApi(page);
  const scenarios = await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios");
  let scenario = scenarios.find((candidate) => candidate.name === currentScenarioName);
  let draft: DraftPayload | undefined;
  for (const candidate of scenarios) {
    const candidateDraft = await getJson<DraftPayload>(page, `/api/v1/scenarios/${candidate.id}/draft`);
    const candidateDerivedStates = Array.isArray(candidateDraft.definition_document.derived_states) ? candidateDraft.definition_document.derived_states : [];
    if (candidateDerivedStates.length > 0) {
      scenario = candidate;
      draft = candidateDraft;
      break;
    }
  }
  expect(scenario).toBeTruthy();
  expect(draft).toBeTruthy();
  const derivedStates = Array.isArray(draft!.definition_document.derived_states) ? draft!.definition_document.derived_states : [];
  expect(derivedStates.length, "The generic E2E scenario should contain a Derived State").toBeGreaterThan(0);
  let draftWrites = 0;
  page.on("request", (request) => {
    if (request.method() === "PUT" && new RegExp(`/api/v1/scenarios/${scenario!.id}/draft$`).test(new URL(request.url()).pathname)) draftWrites += 1;
  });

  await page.goto(`/scenarios/${scenario!.id}/edit/initialization`);
  const workspace = page.getByTestId("initialization-four-panel-workspace");
  const category = workspace.getByRole("region", { name: "类别" });
  await category.getByRole("button", { name: "其他" }).click();
  await expect(page).toHaveURL(/domain=derived/);
  const overview = workspace.getByRole("region", { name: "分类" });
  await expect(overview.getByRole("heading", { name: "其他概览" })).toBeVisible();
  const derivedGroup = overview.getByRole("button", { name: /派生状态/ });
  await expect(derivedGroup).toContainText(String(derivedStates.length));
  await derivedGroup.click();
  await expect(page).toHaveURL(/domain=derived&group=derived-states/);

  const objects = workspace.getByRole("region", { name: "对象" });
  await expect(objects.getByRole("heading", { name: "具体派生状态" })).toBeVisible();
  await objects.locator(".initialization-panel-body > button").first().click();
  await expect(page).toHaveURL(/domain=derived&group=derived-states&item=derived%3A/);
  await expect(objects.locator("button.selected")).toHaveCount(1);
  const configuration = workspace.getByRole("region", { name: "初始化配置" });
  await expect(configuration.getByText("真实初始结果", { exact: false })).toBeVisible();
  await expect(configuration.getByText("玩家可知结果", { exact: true })).toBeVisible();
  await expect(configuration.getByText("组成条件", { exact: true })).toBeVisible();
  await expect(configuration.locator("input, select, textarea")).toHaveCount(0);
  await expect(configuration.locator(".readonly-field-display").first()).toBeVisible();
  const southeastItem = objects.getByRole("button", { name: /东南持续应急发电保障/ });
  await expect(southeastItem).toBeVisible();
  await southeastItem.click();
  await expect(configuration.getByText("共同揭示条件", { exact: true })).toBeVisible();
  await expect(configuration.getByText("影响条件", { exact: true })).toBeVisible();
  await expect(configuration.getByText("3 项", { exact: true })).toBeVisible();
  await expect(configuration.getByText(/满足后，玩家\/智能体才能获知以下条件/)).toHaveCount(1);
  await expect(page.getByText("有未保存修改")).toHaveCount(0);
  expect(draftWrites).toBe(0);
});

test("Migrated target applicability keeps Rule targets and Knowledge visibility separate", async ({ page }) => {
  await wireApi(page);
  const fixture = targetApplicabilityFixture();
  const scenarios = await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios");
  const scenario = scenarios.find((candidate) => candidate.name === fixture.scenarioName);
  expect(scenario).toBeTruthy();

  const draft = await getJson<DraftPayload>(page, `/api/v1/scenarios/${scenario!.id}/draft`);
  const document = draft.definition_document;
  const serialized = JSON.stringify(document);
  expect(serialized).not.toContain("repair_profile");
  expect(serialized).not.toContain("维修类型");
  expect(serialized).not.toContain("设施维修类型");
  const rules = Array.isArray(document.rules) ? document.rules as Array<Record<string, unknown>> : [];
  const targetRules = rules.filter((rule) => rule.action_key === fixture.actionKey && Array.isArray(rule.applicable_target_keys));
  expect(targetRules.some((rule) => (rule.applicable_target_keys as unknown[]).includes(fixture.knownTargetKey))).toBe(true);
  expect(targetRules.some((rule) => (rule.applicable_target_keys as unknown[]).includes(fixture.hiddenTargetKey))).toBe(true);

  await page.goto(`/scenarios/${scenario!.id}/edit/actions/${fixture.actionKey}`);
  await expect(page.getByText("目标信息可见性", { exact: true })).toBeVisible();
  await expect(page.getByText(fixture.knownTargetKey, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(fixture.hiddenTargetKey, { exact: true }).first()).toBeVisible();
  await expect(page.getByText("repair_profile", { exact: true })).toHaveCount(0);

  const knownRule = targetRules.find((rule) => (rule.applicable_target_keys as unknown[]).includes(fixture.knownTargetKey));
  const hiddenRule = targetRules.find((rule) => (rule.applicable_target_keys as unknown[]).includes(fixture.hiddenTargetKey));
  expect(knownRule?.key).toBeTruthy();
  expect(hiddenRule?.key).toBeTruthy();
  await page.goto(`/scenarios/${scenario!.id}/edit/rules/${knownRule!.key}`);
  await expect(page.getByText("适用目标", { exact: true })).toBeVisible();
  await expect(page.getByText(fixture.knownTargetKey, { exact: true }).first()).toBeVisible();
  await page.goto(`/scenarios/${scenario!.id}/edit/rules/${hiddenRule!.key}`);
  await expect(page.getByText("适用目标", { exact: true })).toBeVisible();
  await expect(page.getByText(fixture.hiddenTargetKey, { exact: true }).first()).toBeVisible();

  await page.goto(`/scenarios/${scenario!.id}/edit/initialization`);
  await expect(page.getByText("repair_profile", { exact: true })).toHaveCount(0);
  await expect(page.getByText("维修类型", { exact: true })).toHaveCount(0);
  await expect(page.getByText("设施维修类型", { exact: true })).toHaveCount(0);

  const initial = await getJson<PlayerState>(page, `/api/v1/games/${fixture.gameId}/play`);
  const initialContracts = initial.known_target_action_contracts ?? [];
  expect(initialContracts.some((item) => item.action_key === fixture.actionKey && item.target_key === fixture.knownTargetKey)).toBe(true);
  expect(initialContracts.some((item) => item.action_key === fixture.actionKey && item.target_key === fixture.hiddenTargetKey)).toBe(false);

  revealTargetApplicabilityFixture(fixture.gameId, fixture.hiddenTargetKey);
  const afterInspect = await getJson<PlayerState>(page, `/api/v1/games/${fixture.gameId}/play`);
  expect((afterInspect.known_target_action_contracts ?? []).some(
    (item) => item.action_key === fixture.actionKey && item.target_key === fixture.hiddenTargetKey,
  )).toBe(true);
});

test("Scenario Editor routes legacy source hints to Resources and Public References to Goal System", async ({ page }) => {
  await wireApi(page);
  const scenarios = await getJson<ScenarioSummary[]>(page, "/api/v1/scenarios");
  const scenario = scenarios.find((candidate) => candidate.name === currentScenarioName);
  expect(scenario).toBeTruthy();

  const legacyItem = encodeURIComponent(JSON.stringify(["medicine"]));
  await page.goto(`/scenarios/${scenario!.id}/edit/public-knowledge?owner=collection&collection=resource_source_hints&item=${legacyItem}`);
  await expect(page).toHaveURL(new RegExp(`/edit/resources/medicine\\?focus_path=source_hint$`));
  await expect(page.getByRole("heading", { name: "药品", level: 3 })).toBeVisible();
  await expect(page.getByText(/\u6765\u6e90\u63d0\u793a\u7531\u8d44\u6e90\u5b9a\u4e49/)).toBeVisible();
  await page.getByRole("button", { name: /\u914d\u7f6e\u6765\u6e90\u63d0\u793a/ }).click();
  await expect(page.getByText("\u8bf7\u9009\u62e9\u4e3b\u8981\u533a\u57df\u6216\u6dfb\u52a0\u81f3\u5c11\u4e00\u4e2a\u5019\u9009\u533a\u57df\u3002")).toBeVisible();
  const sourceHintOwnerLink = page.getByRole("link", { name: "前往区域" });
  await expect(sourceHintOwnerLink).toHaveAttribute("href", /world-entities/);
  await expect(page.getByRole("link", { name: "资源来源提示" })).toHaveCount(0);

  await page.goto(`/scenarios/${scenario!.id}/edit/public-references`);
  await expect(page).toHaveURL(new RegExp(`/edit/terminology-references$`));
  await expect(page.getByRole("heading", { name: "术语与引用" })).toBeVisible();
  await expect(page.getByRole("link", { name: "术语与引用" })).toBeVisible();
  await expect(page.getByRole("link", { name: "公开信息" })).toHaveCount(0);
});
