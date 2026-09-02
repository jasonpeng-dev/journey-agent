import { defineConfig, devices } from "@playwright/test";

const managedServers = process.env.E2E_MANAGED_SERVERS === "1";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "line",
  outputDir: process.env.E2E_ARTIFACT_DIR ?? "test-results",
  use: { baseURL: "http://127.0.0.1:4173", trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  ...(managedServers
    ? {}
    : {
        webServer: {
          command: "npm run dev -- --host 127.0.0.1 --port 4173",
          url: "http://127.0.0.1:4173",
          reuseExistingServer: !process.env.CI,
        },
      }),
});
