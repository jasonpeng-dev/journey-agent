import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(frontendRoot, "..");
const python = process.env.E2E_PYTHON
  ?? path.join(repositoryRoot, ".venv", process.platform === "win32" ? "Scripts" : "bin", "python")
  + (process.platform === "win32" ? ".exe" : "");
const viteEntry = path.join(frontendRoot, "node_modules", "vite", "bin", "vite.js");
const playwrightCli = path.join(
  frontendRoot,
  "node_modules",
  "@playwright",
  "test",
  "cli.js",
);

const managedProcesses = new Set();
let terminationPromise;

function delay(milliseconds) {
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));
}

function spawnCommand(label, command, args, cwd, env) {
  const child = spawn(command, args, {
    cwd,
    env,
    shell: false,
    stdio: "inherit",
    windowsHide: true,
  });
  const entry = { label, child };
  managedProcesses.add(entry);
  let settled = false;
  const completion = new Promise((resolve) => {
    child.once("error", (error) => {
      if (settled) return;
      settled = true;
      managedProcesses.delete(entry);
      resolve({ code: null, signal: null, error });
    });
    child.once("close", (code, signal) => {
      if (settled) return;
      settled = true;
      managedProcesses.delete(entry);
      resolve({ code, signal, error: null });
    });
  });
  return { ...entry, completion };
}

async function runChecked(label, command, args, cwd, env) {
  const launched = spawnCommand(label, command, args, cwd, env);
  const result = await launched.completion;
  if (result.error) {
    throw new Error(`${label} failed to start: ${result.error.message}`);
  }
  if (result.code !== 0) {
    throw new Error(
      `${label} exited with ${result.code === null ? `signal ${result.signal}` : `code ${result.code}`}`,
    );
  }
}

async function waitForHttp(label, url, child, timeoutMilliseconds = 60_000) {
  const deadline = Date.now() + timeoutMilliseconds;
  let lastError = "not attempted";
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`${label} exited before readiness with code ${child.exitCode}`);
    }
    const controller = new globalThis.AbortController();
    const timeout = globalThis.setTimeout(() => controller.abort(), 1_000);
    try {
      const response = await globalThis.fetch(url, { signal: controller.signal });
      if (response.ok) {
        await response.arrayBuffer();
        globalThis.clearTimeout(timeout);
        return;
      }
      lastError = `HTTP ${response.status}`;
      await response.body?.cancel();
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    } finally {
      globalThis.clearTimeout(timeout);
    }
    await delay(250);
  }
  throw new Error(`${label} readiness timed out: ${lastError}`);
}

function isProcessAlive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function stopProcess(entry) {
  if (entry.child.exitCode !== null || !isProcessAlive(entry.child.pid)) {
    managedProcesses.delete(entry);
    return;
  }
  const closed = new Promise((resolve) => entry.child.once("close", resolve));
  if (process.platform === "win32" && entry.child.pid) {
    const result = spawnSync(
      "taskkill.exe",
      ["/PID", String(entry.child.pid), "/T", "/F"],
      { stdio: "ignore", windowsHide: true },
    );
    if (result.error || result.status !== 0) {
      entry.child.kill();
    }
  } else {
    entry.child.kill("SIGTERM");
  }
  await Promise.race([closed, waitForProcessExit(entry.child.pid, 10_000)]);
  if (entry.child.exitCode === null && isProcessAlive(entry.child.pid)) {
    entry.child.kill(process.platform === "win32" ? undefined : "SIGKILL");
    await Promise.race([closed, waitForProcessExit(entry.child.pid, 2_000)]);
  }
  const stillAlive = entry.child.exitCode === null && isProcessAlive(entry.child.pid);
  managedProcesses.delete(entry);
  if (stillAlive) {
    throw new Error(`${entry.label} did not terminate cleanly`);
  }
}

async function waitForProcessExit(pid, timeoutMilliseconds) {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline && isProcessAlive(pid)) await delay(50);
}

async function terminateAll() {
  for (const entry of [...managedProcesses].reverse()) {
    try {
      await stopProcess(entry);
    } catch (error) {
      globalThis.console.error(`E2E cleanup warning: ${error instanceof Error ? error.message : error}`);
    }
  }
}

function requestTermination() {
  terminationPromise ??= terminateAll();
}

process.once("SIGINT", requestTermination);
process.once("SIGTERM", requestTermination);

async function main() {
  if (!existsSync(python)) throw new Error(`E2E Python executable not found: ${python}`);
  if (!existsSync(viteEntry)) throw new Error(`Vite entry not found: ${viteEntry}`);
  if (!existsSync(playwrightCli)) throw new Error(`Playwright CLI not found: ${playwrightCli}`);

  const e2eRoot = await mkdtemp(path.join(os.tmpdir(), "journey-agent-e2e-"));
  const databasePath = path.join(e2eRoot, "journey_e2e.db");
  const databaseUrl = `sqlite+pysqlite:///${databasePath.replaceAll("\\", "/")}`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    DEVELOPER_API_TOKEN: process.env.DEVELOPER_API_TOKEN ?? "ci-developer",
    E2E_API_ORIGIN: "http://127.0.0.1:8000",
    E2E_ARTIFACT_DIR: path.join(e2eRoot, "playwright-output"),
    E2E_DB_DIR: e2eRoot,
    E2E_FIXTURE_DB: databasePath,
    E2E_MANAGED_SERVERS: "1",
    E2E_PYTHON: python,
    MODEL_PROVIDER: "mock",
  };
  let exitCode = 1;
  let cleanupError = null;
  try {
    await runChecked("database migration", python, ["-m", "alembic", "upgrade", "head"], repositoryRoot, env);
    await runChecked("database seed", python, ["-m", "app.seed"], repositoryRoot, env);
    await runChecked(
      "platform player preparation",
      python,
      [
        "-c",
        "from app.infrastructure.db.session import SessionLocal; from app.services.game_lifecycle import GameLifecycleService; db = SessionLocal(); GameLifecycleService(db).platform_player(); db.commit(); db.close()",
      ],
      repositoryRoot,
      env,
    );

    const backend = spawnCommand(
      "backend",
      python,
      ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
      repositoryRoot,
      env,
    );
    await waitForHttp("backend", "http://127.0.0.1:8000/ready", backend.child);

    const frontend = spawnCommand(
      "frontend",
      process.execPath,
      [viteEntry, "--configLoader", "runner", "--host", "127.0.0.1", "--port", "4173"],
      frontendRoot,
      env,
    );
    await waitForHttp("frontend", "http://127.0.0.1:4173/", frontend.child);

    const playwrightArgs = process.argv.slice(2);
    if (!playwrightArgs.some((argument) => argument === "--workers" || argument.startsWith("--workers="))) {
      playwrightArgs.push("--workers", "1");
    }
    await runChecked(
      "Playwright",
      process.execPath,
      [playwrightCli, "test", ...playwrightArgs],
      frontendRoot,
      env,
    );
    exitCode = 0;
  } finally {
    terminationPromise ??= terminateAll();
    await terminationPromise;
    try {
      await rm(e2eRoot, { force: true, maxRetries: 5, recursive: true, retryDelay: 200 });
    } catch (error) {
      cleanupError = error;
      globalThis.console.error(`E2E temporary directory cleanup failed: ${error instanceof Error ? error.message : error}`);
    }
  }
  if (cleanupError) exitCode = 1;
  return exitCode;
}

try {
  process.exitCode = await main();
} catch (error) {
  globalThis.console.error(`E2E harness failed: ${error instanceof Error ? error.message : error}`);
  process.exitCode = 1;
}
