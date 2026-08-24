import { expect, test } from "@playwright/test";

async function fillCustomGoal(page: import("@playwright/test").Page, value: string) {
  const objectiveSelect = page.getByLabel("选择任务");
  await expect(objectiveSelect).toBeEnabled();
  await objectiveSelect.selectOption("__custom_goal__");
  await page.getByLabel("自定义目标").fill(value);
}

async function abandonCurrentTaskBeforeArchive(page: import("@playwright/test").Page) {
  const abandon = page.getByRole("button", { name: "放弃当前目标" });
  if (await abandon.count() === 0) return;
  await expect(abandon).toBeVisible();
  await expect(abandon).toBeEnabled({ timeout: 30000 });
  await abandon.click();
  await expect(abandon).toHaveCount(0);
}

test("Formal Play 展示真实计划演进、逐轮执行，并可永久删除游戏", async ({ page }) => {
  const apiOrigin = process.env.E2E_API_ORIGIN;
  await page.route("**/api/**", async (route) => {
    const requestUrl = route.request().url();
    const url = apiOrigin
      ? requestUrl.replace("http://127.0.0.1:4173", apiOrigin)
      : requestUrl;
    const response = await route.fetch({ url });
    if (url.includes("/goals") || url.includes("/play/start-planning") || url.includes("/play/replan")) {
      await page.waitForTimeout(350);
    }
    await route.fulfill({ response });
  });
  await page.goto("/scenarios");
  await page.getByRole("link", { name: /Starfire Strategic Command|星火战略指挥部/ }).first().click();
  await page.getByRole("link", { name: "编辑当前草稿" }).click();
  await page.getByRole("link", { name: "验证与发布" }).click();
  await page.getByLabel("可选目标").fill("gather valley intelligence");
  await page.getByRole("button", { name: "启动隔离测试" }).click();
  await expect(page.getByText("沙箱已启动", { exact: true })).toBeVisible();
  await expect(page.getByText("任务状态：已完成", { exact: true })).toBeVisible();

  await page.goto("/games/new");
  const scenarioSelect = page.getByLabel("场景");
  const scenarioOption = scenarioSelect.locator("option").filter({ hasText: /Starfire Strategic Command|星火战略指挥部/ }).last();
  await scenarioSelect.selectOption(await scenarioOption.getAttribute("value") ?? "");
  await page.getByLabel("已发布版本").selectOption({ index: 1 });
  await page.getByRole("button", { name: "创建游戏" }).click();
  await page.waitForURL(/\/games\/[0-9a-f-]{36}$/);
  const gameId = new URL(page.url()).pathname.split("/").at(-1) ?? "";
  await expect(page.locator(".current-report-panel")).toHaveCount(0);
  await expect(page.getByTestId("goal-composer")).toBeVisible();
  await expect(page.getByText("当前 · 下达目标")).toBeVisible();
  await fillCustomGoal(page, "open the northern trade route");
  await page.getByRole("button", { name: "开始目标" }).click();
  await expect(page.getByTestId("goal-resolving-status")).toBeVisible();
  await expect(page.getByTestId("goal-accepted-card")).toBeVisible();
  await expect(page.getByRole("button", { name: "不错，开始规划" })).toBeVisible();
  await page.getByRole("button", { name: "不错，开始规划" }).click();
  await expect(page.getByTestId("planning-status")).toBeVisible();
  await expect(page.getByRole("heading", { name: "已知世界", exact: true })).toBeVisible();
  await expect(page.getByText("正在加载游戏状态……", { exact: true })).toHaveCount(0);

  await expect(page.getByRole("heading", { name: "计划演进" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "任务路线" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "当前执行方案" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /初始方案 · 执行中/ })).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".plan-history-steps li.current")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "任务执行记录" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Agent 当前汇报" })).toBeVisible();
  await expect(page.getByRole("button", { name: "知悉，开始执行" })).toBeVisible();
  await expect(page.locator(".mission-log-panel .player-checkpoint")).toHaveCount(0);
  await expect(page.locator(".task-tabs")).toHaveCSS("flex-shrink", "0");
  await expect(page.locator(".task-tabs")).toHaveCSS("overflow-y", "hidden");
  await expect(page.locator(".timeline-scroll")).toHaveCSS("overflow-y", "auto");
  const logBox = await page.locator(".mission-log-panel").boundingBox();
  const reportBox = await page.locator(".current-report-panel").boundingBox();
  const tabsBox = await page.locator(".task-tabs").boundingBox();
  const timelineBox = await page.locator(".timeline-scroll").boundingBox();
  expect(logBox).not.toBeNull();
  expect(reportBox).not.toBeNull();
  expect(tabsBox).not.toBeNull();
  expect(timelineBox).not.toBeNull();
  expect(tabsBox!.y + tabsBox!.height).toBeLessThanOrEqual(timelineBox!.y + 1);
  expect(logBox!.y + logBox!.height).toBeLessThanOrEqual(reportBox!.y + 1);

  let sawReplanAcknowledgement = false;
  let sawCompletedStep = false;
  for (let round = 0; round < 24; round += 1) {
    if (await page.getByText("目标已完成", { exact: true }).first().isVisible()) break;
    const execute = page.getByRole("button", { name: "知悉，开始执行" });
    if (await execute.isVisible()) {
      await execute.click();
      await expect.poll(async () =>
        (await page.getByRole("button", { name: "收到，继续规划任务" }).isVisible())
        || (await page.getByRole("button", { name: "收到，继续任务" }).isVisible())
        || (await page.getByRole("button", { name: "没事，重新规划" }).isVisible())
        || (await page.getByText("目标已完成", { exact: true }).first().isVisible()),
      ).toBe(true);
    }

    const continueTask = page.getByRole("button", { name: "收到，继续任务" });
    const continuePlanningTask = page.getByRole("button", {
      name: "收到，继续规划任务",
    });
    const replanTask = page.getByRole("button", { name: "没事，重新规划" });
    if (await replanTask.isVisible() || await continuePlanningTask.isVisible()) {
      const failureReplan = await replanTask.isVisible();
      const replanButton = failureReplan ? replanTask : continuePlanningTask;
      const debrief = page.locator('.action-debrief');
      await expect(debrief).toBeVisible();
      sawReplanAcknowledgement = true;
      if (failureReplan) {
        await expect(debrief.getByRole("heading", { name: /^✕/ })).toBeVisible();
        await expect(debrief.getByRole("heading", { name: "新获知识" })).toBeVisible();
      } else {
        const planInvalidationMessage = page.getByTestId('plan-invalidation-message');
        const segmentCompleteMessage = page.getByTestId('segment-complete-message');
        const planInvalidated = await planInvalidationMessage.isVisible();
        const segmentCompleted = await segmentCompleteMessage.isVisible();
        expect(planInvalidated || segmentCompleted).toBe(true);
        await expect(debrief.getByRole("heading", { name: /^✓/ })).toBeVisible();
        sawCompletedStep = true;
      }
      await expect(page.getByTestId('replanning-status')).toHaveCount(0);
      await replanButton.click();
      await expect.poll(async () =>
        (await page.getByTestId('replanning-status').isVisible())
        || (await page.getByText("Agent 已重新规划", { exact: true }).isVisible()),
      ).toBe(true);
      await expect(page.getByRole("heading", { name: "已知世界", exact: true })).toBeVisible();
      await expect(page.getByText("正在加载游戏状态……", { exact: true })).toHaveCount(0);
      await expect.poll(async () => page.getByRole("button", { name: "知悉，开始执行" }).isVisible()).toBe(true);
      await expect(page.locator('.plan-history-card').first()).toBeVisible();
    } else if (await continueTask.isVisible()) {
      sawCompletedStep = true;
      const completedPlan = page.locator(".plan-history-card").filter({ hasText: /[1-9]\d*\/\d+ 完成/ }).last();
      const toggle = completedPlan.getByRole("button");
      if (await toggle.getAttribute("aria-expanded") === "false") await toggle.click();
      await expect(completedPlan.locator("li.completed").first()).toBeVisible();
      await continueTask.click();
      await expect.poll(async () =>
        (await page.getByRole("button", { name: "知悉，开始执行" }).isVisible())
        || (await page.getByText("目标已完成", { exact: true }).first().isVisible()),
      ).toBe(true);
    }
  }

  await expect(page.getByText("目标已完成", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".current-report-panel")).toBeVisible();
  await expect(page.getByTestId("goal-composer")).toBeVisible();
  await expect(page.locator(".current-report-panel .goal-composer-panel")).toHaveCount(0);
  expect(sawReplanAcknowledgement).toBe(true);
  expect(sawCompletedStep).toBe(true);
  await expect(page.locator(".plan-history-card").last()).toHaveClass(/completed/);
  await expect(page.getByText(/等待结算|settle|operation ID/i)).toHaveCount(0);
  await expect(page.getByText(/方案 v\d/)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "已知世界", exact: true })).toBeVisible();
  await fillCustomGoal(page, "gather valley intelligence");
  await page.getByRole("button", { name: "开始目标" }).click();
  await expect(page.getByTestId("goal-composer")).toBeVisible();
  await expect(page.getByTestId("goal-resolving-status")).toBeVisible();
  await expect(page.locator(".current-report-panel")).toBeVisible();
  const resolvingTaskTab = page.locator(".task-tabs button").first();
  await resolvingTaskTab.click();
  await expect(page.getByTestId("goal-resolving-status")).toBeVisible();
  await expect(page.locator(".task-tabs button")).toHaveCount(2);
  const firstTaskTab = page.locator(".task-tabs button").first();
  const secondTaskTab = page.locator(".task-tabs button").last();
  await expect(secondTaskTab).toHaveAttribute("aria-pressed", "true");
  const firstTaskId = await firstTaskTab.getAttribute("data-task-id");
  const secondTaskId = await secondTaskTab.getAttribute("data-task-id");
  expect(firstTaskId).not.toBeNull();
  expect(secondTaskId).not.toBeNull();
  await firstTaskTab.click();
  await expect(page.locator(".task-brief")).toContainText("open the northern trade route");
  await expect(page.locator(".current-report-panel")).toHaveAttribute("data-task-id", firstTaskId!);
  await expect(page.getByTestId("goal-composer")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "不错，开始规划" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "知悉，开始执行" })).toHaveCount(0);
  await secondTaskTab.click();
  await expect(page.locator(".task-brief")).toContainText("gather valley intelligence");
  await expect(page.locator(".current-report-panel")).toHaveAttribute("data-task-id", secondTaskId!);
  await expect(page.getByRole("button", { name: "不错，开始规划" })).toBeVisible();
  await page.getByRole("button", { name: "不错，开始规划" }).click();
  await expect(page.getByTestId("planning-status")).toBeVisible();
  await firstTaskTab.click();
  await expect(page.locator(".task-brief")).toContainText("open the northern trade route");
  await expect(page.locator(".current-report-panel")).toHaveAttribute("data-task-id", firstTaskId!);
  await expect(page.getByTestId("planning-status")).toHaveCount(0);
  await expect(page.getByTestId("replanning-status")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "不错，开始规划" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "知悉，开始执行" })).toHaveCount(0);
  await secondTaskTab.click();
  await expect(page.getByTestId("planning-status")).toBeVisible();
  await expect(page.getByTestId("planning-status")).toHaveCount(0, { timeout: 30000 });
  await abandonCurrentTaskBeforeArchive(page);
  await page.getByRole("button", { name: "结束并归档游戏" }).click();
  await expect(page.getByText("游戏已归档，当前为只读状态。")).toBeVisible();

  await page.goto("/games");
  const card = page.locator(".game-card").filter({ hasText: `游戏 ${gameId.slice(0, 8)}` });
  await expect(card).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await card.getByRole("button", { name: "永久删除" }).click();
  await expect(card).toHaveCount(0);
});

test("不同 Task 之间切换时不会泄漏 Replan 临时状态", async ({ page }) => {
  test.setTimeout(60000);
  const apiOrigin = process.env.E2E_API_ORIGIN;
  await page.route("**/api/**", async (route) => {
    const requestUrl = route.request().url();
    const url = apiOrigin
      ? requestUrl.replace("http://127.0.0.1:4173", apiOrigin)
      : requestUrl;
    if (url.includes("/play/replan")) {
      // Hold the request before it reaches the backend so the browser can
      // exercise Task navigation while the operation is still transient.
      await page.waitForTimeout(5000);
    }
    const response = await route.fetch({ url });
    if (url.includes("/goals") || url.includes("/play/start-planning")) {
      await page.waitForTimeout(350);
    }
    await route.fulfill({ response });
  });

  await page.goto("/games/new");
  const scenarioSelect = page.getByLabel("场景");
  const scenarioOption = scenarioSelect.locator("option").filter({ hasText: /Starfire Strategic Command|星火战略指挥部/ }).last();
  await scenarioSelect.selectOption(await scenarioOption.getAttribute("value") ?? "");
  await page.getByLabel("已发布版本").selectOption({ index: 1 });
  await page.getByRole("button", { name: "创建游戏" }).click();
  await page.waitForURL(/\/games\/[0-9a-f-]{36}$/);
  const gameId = new URL(page.url()).pathname.split("/").at(-1) ?? "";

  // Finish Task 1 first so Task 2 can be used as the long-running Replan case.
  await fillCustomGoal(page, "gather valley intelligence");
  await page.getByRole("button", { name: "开始目标" }).click();
  await expect(page.getByTestId("goal-accepted-card")).toBeVisible();
  await page.getByRole("button", { name: "不错，开始规划" }).click();
  await expect.poll(async () =>
    (await page.getByRole("button", { name: "知悉，开始执行" }).isVisible())
    || (await page.getByText("目标已完成", { exact: true }).first().isVisible()),
  ).toBe(true);
  if (await page.getByRole("button", { name: "知悉，开始执行" }).isVisible()) {
    await page.getByRole("button", { name: "知悉，开始执行" }).click();
    await expect.poll(async () =>
      (await page.getByRole("button", { name: "收到，继续规划任务" }).isVisible())
      || (await page.getByRole("button", { name: "收到，继续任务" }).isVisible())
      || (await page.getByRole("button", { name: "没事，重新规划" }).isVisible())
      || (await page.getByText("目标已完成", { exact: true }).first().isVisible()),
    ).toBe(true);
    const continueTask = page.getByRole("button", {
      name: /^(?:收到，继续任务|收到，继续规划任务)$/,
    });
    if (await continueTask.isVisible()) {
      await continueTask.click();
    }
  }
  await expect(page.getByText("目标已完成", { exact: true }).first()).toBeVisible();

  await fillCustomGoal(page, "open the northern trade route");
  await page.getByRole("button", { name: "开始目标" }).click();
  await expect(page.locator(".task-tabs button")).toHaveCount(2);
  const firstTaskTab = page.locator(".task-tabs button").first();
  const secondTaskTab = page.locator(".task-tabs button").last();
  await secondTaskTab.click();
  // Prime the historical projection before the next write transaction.  The
  // browser can then switch immediately while Task 2's replan is in flight.
  await firstTaskTab.click();
  await expect(page.locator(".task-brief")).toContainText("gather valley intelligence");
  await secondTaskTab.click();
  await expect(page.getByRole("button", { name: "不错，开始规划" })).toBeVisible();
  await page.getByRole("button", { name: "不错，开始规划" }).click();
  await expect.poll(async () =>
    (await page.getByRole("button", { name: "收到，继续规划任务" }).isVisible())
    || (await page.getByRole("button", { name: "知悉，开始执行" }).isVisible())
    || (await page.getByRole("button", { name: "没事，重新规划" }).isVisible())
    || (await page.getByText("目标已完成", { exact: true }).first().isVisible()),
  ).toBe(true);

  const replanTask = page.getByRole("button", {
    name: /^(?:没事，重新规划|收到，继续规划任务)$/,
  });
  for (let round = 0; round < 16; round += 1) {
    if (await replanTask.isVisible()) break;
    const execute = page.getByRole("button", { name: "知悉，开始执行" });
    const continueTask = page.getByRole("button", {
      name: /^(?:收到，继续任务|收到，继续规划任务)$/,
    });
    try {
      if (await execute.isVisible()) {
        await execute.click({ timeout: 1000 });
      } else if (await continueTask.isVisible()) {
        await continueTask.click({ timeout: 1000 });
      } else if (await page.getByText("目标已完成", { exact: true }).first().isVisible()) {
        break;
      }
    } catch {
      // The persisted projection may replace the checkpoint between the
      // visibility probe and the click; retry against the new projection.
    }
    await page.waitForTimeout(100);
  }

  await expect(replanTask).toBeVisible();
  await replanTask.click();
  await firstTaskTab.click();
  await expect(page.locator(".task-brief")).toContainText("gather valley intelligence");
  await expect(page.getByTestId("replanning-status")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "知悉，开始执行" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "没事，重新规划" })).toHaveCount(0);
  await secondTaskTab.click();
  await expect(page.getByTestId("replanning-status")).toBeVisible({ timeout: 12000 });
  await expect(page.locator(".plan-history-card").first()).toBeVisible();
  const timerAtReturn = await page.getByTestId("replanning-status").textContent();
  await expect.poll(async () => page.getByTestId("replanning-status").textContent()).not.toBe(timerAtReturn);

  await expect(page.getByTestId("replanning-status")).toHaveCount(0, { timeout: 30000 });
  await abandonCurrentTaskBeforeArchive(page);
  await page.getByRole("button", { name: "结束并归档游戏" }).click();
  await expect(page.getByText("游戏已归档，当前为只读状态。")).toBeVisible();
  await page.goto("/games");
  const card = page.locator(".game-card").filter({ hasText: `游戏 ${gameId.slice(0, 8)}` });
  await expect(card).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await card.getByRole("button", { name: "永久删除" }).click();
  await expect(card).toHaveCount(0);
});
