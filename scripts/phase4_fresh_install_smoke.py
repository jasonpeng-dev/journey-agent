"""Disposable fresh-install and explicit portability-import smoke.

This script intentionally never points a command at ``journey_dev.db``.  It
creates a temporary SQLite database, runs Alembic, starts the application to
prove startup is migration-only, imports the tracked Release artifact through
the real CLI, and then creates/plays one explicitly pinned Game through HTTP.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = REPO_ROOT / "scenarios" / "examples" / "linjiang_infrastructure_recovery.scenario.json"
JOURNEY = Path(sys.executable).with_name("journey.exe" if os.name == "nt" else "journey")
COUNT_TABLES = {
    "scenarios": "scenarios",
    "drafts": "scenario_drafts",
    "versions": "scenario_versions",
    "games": "game_instances",
}


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(database)
    try:
        return {
            label: int(connection.execute(f"select count(*) from {table}").fetchone()[0])
            for label, table in COUNT_TABLES.items()
        }
    finally:
        connection.close()


def run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def wait_ready(origin: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    last_error = "not attempted"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"backend exited with code {process.returncode}")
        try:
            payload = request_json(f"{origin}/ready")
            if payload.get("status") == "ready":
                return
        except (OSError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f"backend readiness timed out: {last_error}")


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    expected_status: int | None = None,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if expected_status is not None and response.status != expected_status:
            raise RuntimeError(
                f"{method} {url} returned {response.status}, expected {expected_status}"
            )
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"{method} {url} returned {response.status}")
        return json.loads(response.read().decode("utf-8"))


def stop(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        process.kill()
        process.wait(timeout=10)
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    if not ARTIFACT.is_file():
        raise RuntimeError(f"tracked artifact is missing: {ARTIFACT}")
    if not JOURNEY.is_file():
        raise RuntimeError(f"journey console entrypoint is missing: {JOURNEY}")
    with tempfile.TemporaryDirectory(prefix="journey-phase4-fresh-") as temporary:
        root = Path(temporary)
        database = root / "fresh.db"
        database_url = f"sqlite+pysqlite:///{database.as_posix()}"
        env = {
            **os.environ,
            "APP_ENV": "test",
            "DATABASE_URL": database_url,
            "MODEL_PROVIDER": "mock",
        }
        run([sys.executable, "-m", "alembic", "upgrade", "head"], env)
        before_start = counts(database)
        if before_start != {"scenarios": 0, "drafts": 0, "versions": 0, "games": 0}:
            raise RuntimeError(f"fresh migration was not empty: {before_start}")

        origin = f"http://127.0.0.1:{free_port()}"
        port = int(origin.rsplit(":", 1)[1])
        backend = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            wait_ready(origin, backend)
            after_start = counts(database)
        finally:
            stop(backend)
        if after_start != before_start:
            raise RuntimeError(f"application startup mutated fresh counts: {after_start}")

        imported = run([str(JOURNEY), "scenario", "import", str(ARTIFACT)], env)
        imported_payload = json.loads(imported.stdout)
        published = imported_payload["published_version"]
        if published["version_number"] != 1 or published["schema_version"] != 3:
            raise RuntimeError(f"unexpected imported Published lifecycle: {published}")
        after_import = counts(database)
        expected_import = {"scenarios": 1, "drafts": 1, "versions": 1, "games": 0}
        if after_import != expected_import:
            raise RuntimeError(f"unexpected explicit import counts: {after_import}")

        origin = f"http://127.0.0.1:{free_port()}"
        port = int(origin.rsplit(":", 1)[1])
        backend = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            wait_ready(origin, backend)
            game = request_json(
                f"{origin}/api/v1/games",
                method="POST",
                expected_status=201,
                payload={
                    "scenario_id": imported_payload["scenario"]["id"],
                    "scenario_version_id": published["id"],
                    "idempotency_key": "phase4-fresh-install-game",
                },
            )
            play = request_json(
                f"{origin}/api/v1/games/{game['id']}/play",
                expected_status=200,
            )
        finally:
            stop(backend)
        quick_inputs = play["scenario_metadata"]["quick_inputs"]
        if len(quick_inputs) != 6:
            raise RuntimeError(f"official quick inputs were not preserved: {quick_inputs!r}")
        after_game = counts(database)
        if after_game != {"scenarios": 1, "drafts": 1, "versions": 1, "games": 1}:
            raise RuntimeError(f"unexpected Game lifecycle counts: {after_game}")
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "before_start": before_start,
                    "after_start": after_start,
                    "after_import": after_import,
                    "after_game": after_game,
                    "scenario_key": imported_payload["scenario"]["key"],
                    "published_version": published["version_number"],
                    "game_id": game["id"],
                    "game_create_status": 201,
                    "play_status": 200,
                    "quick_input_count": len(quick_inputs),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        # Windows may release SQLite handles just after a terminated Uvicorn
        # process exits; give the OS a short interval before deleting the
        # disposable directory.
        time.sleep(1)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, urllib.error.URLError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from exc
