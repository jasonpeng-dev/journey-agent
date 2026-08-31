import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, type Page } from "@playwright/test";

export const currentScenarioName = "临江市灾后基础设施恢复 v2.0";

export type GameSummary = {
  id: string;
  scenario_version_id: string;
  scenario_version_number: number;
  scenario_content_hash: string;
  status: string;
  runtime_revision: number;
  active_task_id: string | null;
  is_checkpoint: boolean;
  checkpointed_from_game_instance_id: string | null;
  inherited_task_count: number;
};

function apiOrigin(): string {
  return (process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:4173").replace(/\/$/, "");
}

export async function wireApi(page: Page): Promise<void> {
  const origin = process.env.E2E_API_ORIGIN;
  await page.route("**/api/**", async (route) => {
    const requestUrl = route.request().url();
    const url = origin
      ? requestUrl.replace("http://127.0.0.1:4173", origin)
      : requestUrl;
    // Let Playwright own the response lifecycle. Waiting for fetch/fulfill here
    // can outlive the page during teardown when the UI has an in-flight request.
    await route.continue({ url });
  });
}

export async function getJson<T>(page: Page, endpoint: string): Promise<T> {
  const response = await page.request.get(`${apiOrigin()}${endpoint}`);
  expect(response.ok(), `${response.status()} ${endpoint}`).toBe(true);
  return response.json() as Promise<T>;
}

export function fixtureGame(kind: "presentation" | "fork"): { gameId: string } {
  const frontendRoot = path.basename(process.cwd()) === "frontend"
    ? process.cwd()
    : path.resolve(process.cwd(), "frontend");
  const repositoryRoot = path.resolve(frontendRoot, "..");
  const fixtureDb = process.env.E2E_FIXTURE_DB;
  if (!fixtureDb) {
    throw new Error(
      "Thin browser E2E requires E2E_FIXTURE_DB pointing at the isolated seeded test database.",
    );
  }
  const python = process.env.E2E_PYTHON
    ?? path.resolve(repositoryRoot, ".venv", "Scripts", "python.exe");
  const script = path.resolve(frontendRoot, "e2e", "prepare_history_fixture.py");
  const fixtureDatabaseUrl = process.env.DATABASE_URL
    ?? `sqlite+pysqlite:///${path.resolve(fixtureDb).replace(/\\/g, "/")}`;
  const output = execFileSync(python, [script, kind], {
    cwd: repositoryRoot,
    env: { ...process.env, DATABASE_URL: fixtureDatabaseUrl, E2E_FIXTURE_DB: fixtureDb },
    encoding: "utf8",
  });
  return JSON.parse(output.trim()) as { gameId: string };
}
export function appendFixtureTask(gameId: string): void {
  const frontendRoot = path.basename(process.cwd()) === "frontend"
    ? process.cwd()
    : path.resolve(process.cwd(), "frontend");
  const repositoryRoot = path.resolve(frontendRoot, "..");
  const fixtureDb = process.env.E2E_FIXTURE_DB;
  if (!fixtureDb) {
    throw new Error(
      "Thin browser E2E requires E2E_FIXTURE_DB pointing at the isolated seeded test database.",
    );
  }
  const python = process.env.E2E_PYTHON
    ?? path.resolve(repositoryRoot, ".venv", "Scripts", "python.exe");
  const script = path.resolve(frontendRoot, "e2e", "prepare_history_fixture.py");
  const fixtureDatabaseUrl = process.env.DATABASE_URL
    ?? `sqlite+pysqlite:///${path.resolve(fixtureDb).replace(/\\/g, "/")}`;
  execFileSync(python, [script, "append", gameId], {
    cwd: repositoryRoot,
    env: { ...process.env, DATABASE_URL: fixtureDatabaseUrl, E2E_FIXTURE_DB: fixtureDb },
    encoding: "utf8",
  });
}
