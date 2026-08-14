import { expect, test } from "@playwright/test";

test("Formal Play 展示真实计划演进、逐轮执行，并可永久删除游戏", async ({ page }) => {
  const apiOrigin = process.env.E2E_API_ORIGIN;
  if (apiOrigin) {
    await page.route("http://127.0.0.1:4173/api/**", async (route) => {
      const response = await route.fetch({
        url: route.request().url().replace("http://127.0.0.1:4173", apiOrigin),
      });
      await route.fulfill({ response });
    });
  }
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
  await page.getByLabel("下达高层目标").fill("open the northern trade route");
  await page.getByRole("button", { name: "开始目标" }).click();

  await expect(page.getByRole("heading", { name: "计划演进" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "任务路线" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "当前执行方案" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /初始方案 · 执行中/ })).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".plan-history-steps li.current")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "任务执行记录" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Agent 当前汇报" })).toBeVisible();
  await expect(page.getByRole("button", { name: "知悉，执行" })).toBeVisible();
  await expect(page.locator(".mission-log-panel .player-checkpoint")).toHaveCount(0);

  let sawFailureKnowledgeReplan = false;
  let sawCompletedStep = false;
  for (let round = 0; round < 24; round += 1) {
    if (await page.getByText("目标已完成", { exact: true }).first().isVisible()) break;
    const execute = page.getByRole("button", { name: "知悉，执行" });
    if (await execute.isVisible()) {
      await execute.click();
      await expect.poll(async () =>
        (await page.getByRole("button", { name: "收到，继续任务" }).isVisible())
        || (await page.getByText("目标已完成", { exact: true }).first().isVisible()),
      ).toBe(true);
    }

    const continueTask = page.getByRole("button", { name: "收到，继续任务" });
    if (await continueTask.isVisible()) {
      const debrief = page.locator(".action-debrief");
      await expect(debrief).toBeVisible();
      if (await debrief.evaluate((element) => element.classList.contains("failed"))) {
        sawFailureKnowledgeReplan = true;
        await expect(debrief.getByRole("heading", { name: /^✕/ })).toBeVisible();
        await expect(debrief.getByRole("heading", { name: "新获知情报" })).toBeVisible();
        await expect(debrief.getByText("计划调整", { exact: true })).toBeVisible();
        await expect.poll(() => page.locator(".plan-history-card").count()).toBeGreaterThan(1);
        const failedPlan = page.locator(".plan-history-card").filter({ hasText: /失败/ }).last();
        await expect(failedPlan.getByRole("button")).toHaveAttribute("aria-expanded", "false");
        await expect(page.locator(".plan-history-card").last().getByRole("button")).toHaveAttribute("aria-expanded", "true");
        await failedPlan.getByRole("button").click();
        await expect(failedPlan.locator("li.failed")).toBeVisible();
        await expect(failedPlan.locator("li.cancelled").first()).toBeVisible();
      } else {
        sawCompletedStep = true;
        const completedPlan = page.locator(".plan-history-card").filter({ hasText: /[1-9]\d*\/\d+ 完成/ }).last();
        const toggle = completedPlan.getByRole("button");
        if (await toggle.getAttribute("aria-expanded") === "false") await toggle.click();
        await expect(completedPlan.locator("li.completed").first()).toBeVisible();
      }
      await continueTask.click();
      await expect(page.getByRole("button", { name: "知悉，执行" })).toBeVisible();
    }
  }

  await expect(page.getByText("目标已完成", { exact: true }).first()).toBeVisible();
  expect(sawFailureKnowledgeReplan).toBe(true);
  expect(sawCompletedStep).toBe(true);
  await expect(page.locator(".plan-history-card").last()).toHaveClass(/completed/);
  await expect(page.getByText(/等待结算|settle|operation ID/i)).toHaveCount(0);
  await expect(page.getByText(/方案 v\d/)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "已知世界", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "结束并归档游戏" }).click();
  await expect(page.getByText("已归档游戏为只读状态。")).toBeVisible();

  await page.goto("/games");
  const card = page.locator(".game-card").filter({ hasText: `游戏 ${gameId.slice(0, 8)}` });
  await expect(card).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await card.getByRole("button", { name: "永久删除" }).click();
  await expect(card).toHaveCount(0);
});
