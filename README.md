# Journey Agent

[English](README.md) · [中文](README.zh.md)

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
* Generic Goal Resolver, frozen `FormalGoalContractV1`, canonical PlannerInput
  V2, deterministic Validator, bounded internal REPAIR, and Knowledge-aware
  REPLAN.
* Declarative Action/Rule execution, Truth mutation, public Knowledge
  projection, Player-safe Formal PLAY, approvals, immutable archived runtime
  sources, Fork, and plan history.
* Generic built-in scenarios running through the same runtime and browser
  product. Scenario-specific gameplay branches are not part of the engine.

## Architecture at a glance

    ScenarioVersion
      -> GameInstance
           -> Goal
                -> exact authored Objective routing, or Dynamic Goal
                     -> public entity grounding -> focused ontology
                          -> typed Goal interpretation -> exact-Version validation
                               -> frozen FormalGoalContractV1
                                    -> Closure -> PlannerInput V2
                                         -> Provider PlanSegment -> Validator
                                              -> AgentPlan -> Runtime -> Truth / Knowledge
                                                   -> REPLAN or Complete

Formal PLAY sends one planning HTTP request. The backend performs any bounded
REPAIR attempts internally and returns one final state. Rejected proposals
remain internal PlanningAttempt audit rows; they are not player-visible plans
and do not create runtime operations.

See [docs/architecture.md](docs/architecture.md) for high-level runtime
boundaries, [docs/agent-planning-v2.md](docs/agent-planning-v2.md) for the
detailed planning contract, [docs/scenario-authoring.md](docs/scenario-authoring.md)
for Scenario publishing, and [docs/game-lifecycle.md](docs/game-lifecycle.md)
for the detailed GameInstance lifecycle.

An ACTIVE GameInstance can be archived only when it has no non-terminal Task,
pending WorldOperation, pending ActionDecisionRequest, or reserved resource
value. ARCHIVED instances are immutable, read-only runtime sources. A
Checkpoint creates an independent ARCHIVED snapshot while its ACTIVE source
remains unchanged. Fork materializes a new independent ACTIVE instance from an
ARCHIVED source with the same exact ScenarioVersion, runtime state, inherited
formal history, and an auditable source link. See
[GameInstance lifecycle](docs/game-lifecycle.md) for the complete contract.

## Provider configuration

Mock mode is the safe default and makes no network request.
No API key is required for the default local or Docker setup.

    MODEL_PROVIDER=mock

To use a real OpenAI-compatible provider, configure the provider settings
in your local `.env`.

Journey Agent supports OpenAI-compatible endpoints, including OpenAI and
compatible providers such as DeepSeek.

See [`.env.example`](.env.example) for the available settings and example
configuration.

Dynamic Goal Entity Grounding and Goal Interpretation use the
`FAST_SEMANTIC` profile. It uses `SEMANTIC_MODEL` (falling back to
`MODEL_NAME` when unset), a bounded semantic output budget, and
`thinking=disabled` enforced by code. `MODEL_THINKING_MODE` and
`MODEL_REASONING_EFFORT` do not affect this profile.

`INITIAL`, `REPAIR`, and `REPLAN` use `PLANNING_REASONING`. This profile uses
`MODEL_NAME`, `MODEL_THINKING_MODE`, `MODEL_REASONING_EFFORT`, and
`MODEL_MAX_OUTPUT_TOKENS`. Semantic and planning calls may use different
models; changing either model is a configuration change and does not require
changing Goal Resolver or Planner business logic.

Dynamic Goal resolution grounds public entities before building a focused
ontology and interpreting terminal `FACT` or `RESOURCE_AT_LEAST` requirements.
Transient/provider-format failures use bounded retry, while
`NEEDS_CLARIFICATION` is not retried into a random resolution. Internal
provider or validation codes remain developer diagnostics; Goal submission
feedback is shown below the Goal input rather than as a page-level error.

Never commit API keys.

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

The browser lifecycle surface is intentionally small: an active game can be
archived from its detail or list card, and an archived game can be Forked from
either location. A Fork request uses one creation key for retry-safe
idempotency and navigates to the new target on success.

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
    uv run ruff format --check app tests frontend/e2e/prepare_history_fixture.py
    uv run mypy app
    uv run alembic upgrade head

Frontend, from frontend:

    npm run lint
    npm run typecheck
    npm test
    npm run build
    npm run e2e

Real provider calls are not part of CI. Provider tests use deterministic fakes
or mocked HTTP responses. Real-model runs are bounded manual evaluations.

The browser E2E suite contains three deterministic smokes: Basic Product,
PLAY Presentation, and Checkpoint/Fork. These browser tests use the mock
provider and do not call a real Provider.

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
| docs | Current architecture, planning, authoring, lifecycle, and archive |

## Documentation

Current authority:

* [docs/architecture.md](docs/architecture.md)
* [docs/agent-planning-v2.md](docs/agent-planning-v2.md)
* [docs/scenario-authoring.md](docs/scenario-authoring.md)
* [docs/game-lifecycle.md](docs/game-lifecycle.md)

Historical notes:

* [docs/archive](docs/archive/) is for archaeology only and is not current
  implementation authority.

Setup and run instructions are intentionally kept here; architecture and
planning semantics belong in the canonical docs above.
