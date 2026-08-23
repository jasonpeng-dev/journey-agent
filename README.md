# Journey Agent

Journey Agent is a generic, data-driven scenario runtime with an LLM Planner,
deterministic validation, explicit Truth/Knowledge separation, and auditable
Formal PLAY execution.

The runtime is ScenarioVersion-driven: a published immutable Version supplies
world content and declarative semantics, while reusable source code supplies
Goal resolution, planning, validation, Action execution, persistence, and
the browser product.

## What is included

* ScenarioDefinitionV2 Draft, validation, publication, immutable Versions, and
  exact-Version GameInstances.
* Scenario Library and structured Editor for world, Actors, Actions, Rules,
  Objectives, planning metadata, initialization, references, and Version
  history.
* Generic Goal Resolver, frozen ObjectiveScope, canonical PlannerInput V2,
  deterministic Validator, bounded internal REPAIR, and Knowledge-aware
  REPLAN.
* Declarative Action/Rule execution, Truth mutation, public Knowledge
  projection, Player-safe Formal PLAY, approvals, checkpoints, and plan
  history.
* Generic built-in scenarios running through the same runtime and browser
  product. Scenario-specific gameplay branches are not part of the engine.

## Architecture at a glance

    ScenarioVersion
      -> GameInstance
           -> Goal -> frozen ObjectiveScope
                -> Dependency Closure
                     -> PlannerInput V2 -> Provider PlanSegment
                          -> Validator -> formal AgentPlan
                               -> Runtime -> Truth / Knowledge
                                    -> REPLAN or Complete

Formal PLAY sends one planning HTTP request. The backend performs any bounded
REPAIR attempts internally and returns one final state. Rejected proposals
remain internal PlanningAttempt audit rows; they are not player-visible plans
and do not create runtime operations.

See [docs/README.md](docs/README.md) for the documentation authority map,
[docs/architecture.md](docs/architecture.md) for high-level runtime
boundaries, and [docs/agent-planning-v2.md](docs/agent-planning-v2.md) for
the detailed planning contract.

## Provider configuration

Mock mode is the safe default and makes no network request. Configure an
OpenAI-compatible provider only in a local environment; never commit the
secret key.

    MODEL_PROVIDER=mock
    MODEL_BASE_URL=https://api.openai.com/v1
    MODEL_NAME=gpt-4.1-mini
    MODEL_API_KEY=
    MODEL_THINKING_MODE=disabled
    MODEL_REASONING_EFFORT=low
    MODEL_TIMEOUT_SECONDS=20
    MODEL_TOTAL_TIMEOUT_SECONDS=60
    MODEL_MAX_OUTPUT_TOKENS=8192

For a compatible endpoint such as DeepSeek, set the provider endpoint,
available model, local key, thinking/reasoning settings, and desired timeout
or output limits. The ScenarioVersion remains provider-agnostic.

## Docker quick start

From a clean checkout:

    git clone https://github.com/jasonpeng-dev/journey-agent.git
    cd journey-agent
    Copy-Item .env.example .env
    docker compose up --build -d

On macOS/Linux, use cp .env.example .env. Open
http://localhost:8000. Mock mode needs no API key.

Useful lifecycle commands:

    docker compose logs -f
    docker compose stop
    docker compose start
    docker compose down
    docker compose down -v

The named Compose volume preserves local Journey Agent data across normal
stop/start and down/up. down -v removes only this Compose project's data.

## Manual local development

Supported toolchain: Python 3.12, Node 22, and uv.

Backend, from the repository root:

    uv sync --python 3.12 --extra dev
    Copy-Item .env.example .env
    uv run alembic upgrade head
    uv run python -m app.seed
    uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

Local development uses SQLite at ./journey_dev.db. Keep that database target
in .env for backend, Editor, and Player UI. Do not point local commands at
historical databases.

Frontend, in a second terminal:

    cd frontend
    npm ci
    npm run dev -- --host 127.0.0.1 --port 4173

Open http://127.0.0.1:4173. Vite proxies API requests to the backend.

## Main HTTP surfaces

| Area | Routes |
| --- | --- |
| Health | /health, /ready |
| Scenario Library | /scenarios and /api/v1/scenarios |
| Editor | /scenarios/:id/edit/:section |
| Games | /games and /api/v1/games |
| Developer | /api/v1/developer/games/:id/snapshot and history |

## Verification

Backend:

    uv run pytest --cov=app --cov-report=term-missing
    uv run ruff check .
    uv run ruff format --check app evals tests
    uv run mypy app evals
    uv run alembic upgrade head

Frontend, from frontend:

    npm run lint
    npm run typecheck
    npm test
    npm run build

Real provider calls are not part of CI. Provider tests use deterministic fakes
or mocked HTTP responses. Real-model runs are bounded manual evaluations.

## Repository map

| Path | Responsibility |
| --- | --- |
| app/domain | ScenarioDefinitionV2, ObjectiveScope, world/runtime values |
| app/agent | Goal resolver, PlannerInput, provider, Validator, Agent loop |
| app/services | Scenario/Game lifecycle, Formal PLAY, actions, projections |
| app/scenarios | V2 parsing, validation, persistence, built-in definitions |
| app/api | FastAPI adapters and Player/Developer DTOs |
| frontend/src | React/Vite browser product and Editor |
| tests | Unit, contract, integration, lifecycle, provider, and E2E support |
| migrations | Alembic schema history |
| docs | Current architecture, planning, authoring, and archive |

## Documentation

Current authority:

* [docs/README.md](docs/README.md)
* [docs/architecture.md](docs/architecture.md)
* [docs/agent-planning-v2.md](docs/agent-planning-v2.md)
* [docs/scenario-authoring.md](docs/scenario-authoring.md)

Historical notes:

* [docs/archive](docs/archive/) is for archaeology only and is not current
  implementation authority.

Setup and run instructions are intentionally kept here; architecture and
planning semantics belong in the canonical docs above.
