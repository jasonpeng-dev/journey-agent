"""Disposable Docker/Compose smoke for the Phase 4 bootstrap contract.

The script is deliberately explicit about a missing Docker daemon: it prints a
BLOCKED result instead of treating a Dockerfile inspection as a passing smoke.
All Compose resources use a unique project name and are removed with ``down
-v`` in the finally block.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_IN_IMAGE = "/app/scenarios/examples/linjiang_infrastructure_recovery.scenario.json"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def compose(
    project: str, files: list[Path], args: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "-p", project]
    for file in files:
        command.extend(["-f", str(file)])
    command.extend(args)
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )


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
        return json.loads(response.read().decode("utf-8"))


def wait_ready(origin: str) -> None:
    deadline = time.monotonic() + 90
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            payload = request_json(f"{origin}/ready")
            if payload.get("status") == "ready":
                return
        except OSError as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"Docker backend readiness timed out: {last_error}")


def main() -> int:
    docker = shutil.which("docker")
    if docker is None:
        print(json.dumps({"status": "BLOCKED", "reason": "docker executable is unavailable"}))
        return 2
    daemon = subprocess.run(
        [docker, "info"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if daemon.returncode != 0:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": "Docker daemon is unavailable",
                    "detail": (daemon.stderr or daemon.stdout).strip(),
                },
                ensure_ascii=False,
            )
        )
        return 2

    project = f"journey-phase4-{uuid4().hex[:12]}"
    port = int(os.environ.get("PHASE4_DOCKER_PORT", free_port()))
    with tempfile.TemporaryDirectory(prefix="journey-phase4-docker-") as temporary:
        override = Path(temporary) / "compose.override.yml"
        override.write_text(
            f'services:\n  api:\n    ports:\n      - "{port}:8000"\n',
            encoding="utf-8",
        )
        env = {**os.environ, "MODEL_PROVIDER": "mock"}
        files = [REPO_ROOT / "docker-compose.yml", override]
        origin = f"http://127.0.0.1:{port}"
        try:
            compose(project, files, ["up", "--build", "-d"], env)
            wait_ready(origin)
            before = {
                "scenarios": len(request_json(f"{origin}/api/v1/scenarios")),
                "games": len(request_json(f"{origin}/api/v1/games")),
            }
            if before != {"scenarios": 0, "games": 0}:
                raise RuntimeError(f"Docker startup was not empty: {before}")
            presence = compose(
                project,
                files,
                ["exec", "-T", "api", "test", "-f", ARTIFACT_IN_IMAGE],
                env,
            )
            if presence.returncode != 0:
                raise RuntimeError("tracked official artifact is missing from the image")
            imported = compose(
                project,
                files,
                [
                    "exec",
                    "-T",
                    "api",
                    "uv",
                    "run",
                    "journey",
                    "scenario",
                    "import",
                    ARTIFACT_IN_IMAGE,
                ],
                env,
            )
            imported_payload = json.loads(imported.stdout)
            after_import = {
                "scenarios": len(request_json(f"{origin}/api/v1/scenarios")),
                "games": len(request_json(f"{origin}/api/v1/games")),
            }
            if after_import != {"scenarios": 1, "games": 0}:
                raise RuntimeError(f"unexpected Docker import counts: {after_import}")
            published = imported_payload["published_version"]
            game = request_json(
                f"{origin}/api/v1/games",
                method="POST",
                expected_status=201,
                payload={
                    "scenario_id": imported_payload["scenario"]["id"],
                    "scenario_version_id": published["id"],
                    "idempotency_key": "phase4-docker-game",
                },
            )
            play = request_json(
                f"{origin}/api/v1/games/{game['id']}/play",
                expected_status=200,
            )
            compose(project, files, ["restart", "api"], env)
            wait_ready(origin)
            after_restart = {
                "scenarios": len(request_json(f"{origin}/api/v1/scenarios")),
                "games": len(request_json(f"{origin}/api/v1/games")),
            }
            if after_restart != {"scenarios": 1, "games": 1}:
                raise RuntimeError(f"Docker restart changed lifecycle counts: {after_restart}")
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "project": project,
                        "before": before,
                        "after_import": after_import,
                        "after_restart": after_restart,
                        "published_version": published["version_number"],
                        "game_create_status": 201,
                        "play_status": 200,
                        "quick_input_count": len(play["scenario_metadata"]["quick_inputs"]),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        finally:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    project,
                    "-f",
                    str(files[0]),
                    "-f",
                    str(files[1]),
                    "down",
                    "-v",
                    "--remove-orphans",
                ],
                cwd=REPO_ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}), file=os.sys.stderr)
        raise SystemExit(1) from exc
