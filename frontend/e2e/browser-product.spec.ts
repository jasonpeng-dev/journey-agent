import { expect, test } from "@playwright/test";

test("published Scenario starts an exact-version Game and completes a Goal", async ({ page }) => {
  await page.goto("/games");
  await page.getByRole("link", { name: "New Game" }).click();
  await page.getByLabel("Scenario").selectOption({ index: 1 });
  await page.getByLabel("Published version").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Create Game" }).click();
  await expect(page.getByRole("heading", { name: /Game/ })).toBeVisible();
  await page.getByLabel("What do you want to achieve?").fill("gather valley intelligence");
  await page.getByRole("button", { name: "Start Goal" }).click();
  await expect(page.getByText(/Gather Northern Valley Intelligence.*COMPLETED/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Current Plan" })).toBeVisible();
  await expect(page.getByText("Known World")).toBeVisible();
  await page.getByRole("button", { name: "End Game" }).click();
  await expect(page.getByText("Archived games are read-only.")).toBeVisible();
});
