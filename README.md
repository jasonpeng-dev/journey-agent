[English](README.md) · [中文](README.zh.md)

# Journey Agent

Journey Agent is a data-driven, generic LLM agent runtime for goal-directed interactive
scenarios. A ScenarioVersion defines the world; the source code provides the reusable Agent,
Action, Rule, persistence, and browser-product runtime. The project is intended to make model
proposals auditable and safely executable, not to turn a chatbot into a collection of
scenario-specific scripts.

## What is implemented

- `ScenarioDefinitionV2`, one mutable Current Draft per Scenario, immutable published Versions,
  and exact-Version GameInstance binding.
- A React/TypeScript Scenario Library and structured Editor for world, actors, actions, rules,
  objectives, planning metadata, initialization, validation, references, and version history.
- Blank, Example, and Clone authoring flows; optimistic Draft revisions; publish gates; and an
  isolated Draft Preview/Test sandbox that never creates a formal GameInstance row.
- Formal Play with a natural-language Goal, persistent AgentTask, full Plan/Plan History,
  player-safe Knowledge projection, action/debrief checkpoints, approval, Repair/Replan, blocked
  states, archive, and recovery.
- Generic Starfire and Medical V2 definitions running through the same engine and browser UI.
- Separate Player and credential-gated Developer projections. Normal Play does not return hidden
  Truth, Rule ASTs, or provider internals.

## Architecture at a glance

```text
Goal
  -> Goal Resolver (exact Objective candidates)
  -> frozen ObjectiveScope on AgentTask
  -> PlanningContext V1
  -> deterministic planner or structured provider proposal
  -> deterministic backend validation / bounded Repair
  -> Generic Action + declarative Rule execution
  -> Truth mutation and Knowledge update
  -> Objective verification, completion, approval, or bounded Replan
```

The application composition boundary constructs the configured provider and injects it into the
generic Goal Resolver and Agent service. HTTP routers and React pages are adapters; they do not
duplicate Rule evaluation, Action effects, Objective completion, authority, or Version semantics.

See [`docs/architecture.md`](docs/architecture.md) for the runtime boundaries and
[`docs/planning-context-v1.md`](docs/planning-context-v1.md) for the provider contract.

## Core concepts

### Scenarios and Versions

`ScenarioDefinitionV2` is a closed, declarative schema containing metadata, World Nodes/Facts/
Relations/Resources, Actors/Roles, Interactions, Actions, Rules, Objectives, Goal Resolution,
Planning, and Initialization. Authors supply content and supported structured primitives; they do
not write arbitrary gameplay code or select a model provider in a Scenario.

A Draft may be incomplete while it is edited. Validation and publication construct the strict v2
definition. Publishing creates an immutable snapshot. A formal Game can only start from a
published `scenario_version_id`, and its runtime state remains bound to that exact snapshot.

### Goal Resolver and ObjectiveScope

The resolver first normalizes exact Version Objective keys, names, aliases, and examples. If the
Scenario allows LLM fallback and a provider is configured, the provider receives only the exact
Objective candidates and can select only those keys. A successful Goal creates a persistent
`AgentTask` with a non-empty frozen `ObjectiveScope`; the scope is not expanded by later Replans.

### Planning, Repair, and Replan

`PlanningContext V1` is the canonical model input: goal, current Knowledge, relevant Actions,
Actors, Targets, previous execution context, and Scenario planning hints. The provider chooses
the Action, Actor, Target, parameters, and order from this context. The former Actor × Action ×
Target Candidate Catalog is compatibility-only and is not the canonical OpenAI-compatible
payload.

Initial Planning and Replan return proposals. The deterministic backend validates the proposal's
bindings, parameter shapes, exact-Version references, objective relevance/coverage/order,
authority, and rejected-proposal constraints before persisting executable steps. Provider mode
may make a bounded Repair request with structured diagnostics. Provider failures and plans that
remain invalid become explicit application errors or blocked states; they do not silently fall
back to a different planner.

### Truth, Knowledge, and execution

Truth is authoritative instance state used by Rule evaluation and objective verification.
Knowledge is the visibility-filtered projection used by planning and normal Player responses.
`GenericActionService` performs generic Action checks, creates instance-scoped WorldOperations,
and applies deterministic Rule outcomes/effects. Supported Conditions and Effects are structured
V2 primitives; there is no Starfire-specific gameplay branch in the generic engine.

### Formal Play

Formal Play is an application orchestrator over the generic services. It keeps a persistent
checkpoint for plan start, action acknowledgement, debrief, Replan, approval, completion,
blocked, and aborted phases. The browser shows a selected Task's Mission Log and Plan History;
one GameInstance may continue after a Goal completes, while only one Task is active at a time.
Pending approvals and unsettled operations are cancelled on Abandon/Archive without rolling back
mutations that already happened.

## Provider support

The provider is selected by `MODEL_PROVIDER` in the server environment:

| Mode | Behavior |
| --- | --- |
| `mock` | No HTTP request. Uses deterministic exact matching and generic planning for tests/offline runs. |
| `openai_compatible` | Uses the generic OpenAI-compatible JSON adapter for Goal selection and Plan/Repair/Replan proposals. |

The adapter uses these settings from `.env` (keep the key local and never commit it):

```dotenv
MODEL_PROVIDER=mock
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4.1-mini
MODEL_API_KEY=
MODEL_TIMEOUT_SECONDS=20
```

For a DeepSeek-compatible run, set the same adapter to the provider endpoint and a model that is
available to your account, for example:

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-v4-flash
MODEL_API_KEY=<local secret>
MODEL_TIMEOUT_SECONDS=60
```

The ScenarioVersion remains provider-agnostic. The backend records secret-safe call metadata
(call type, latency, context bytes, and usage fields when supplied) and never logs the API key.

## Docker Quick Start

Docker is the shortest path for a first-time user. Docker Compose builds the React bundle, serves
it from the existing FastAPI application, runs Alembic and the idempotent built-in seed on
startup, and stores SQLite data in a named volume.

### Stable Release (recommended)

The stable release is fixed and reproducible. It is the recommended path for a first
experience, and the same source is also available from the matching GitHub Release archive.

```powershell
git clone --branch v0.1.0 --depth 1 https://github.com/jasonpeng-dev/journey-agent.git
cd journey-agent
Copy-Item .env.example .env
docker compose up --build -d
```

On macOS/Linux, use `cp .env.example .env` for the third line.

### Latest Main

Use this path when you want the newest development state. `main` changes as development
continues, so it can differ from the stable release.

```powershell
git clone https://github.com/jasonpeng-dev/journey-agent.git
cd journey-agent
Copy-Item .env.example .env
docker compose up --build -d
```

On macOS/Linux, use `cp .env.example .env` for the third line.

Open `http://localhost:8000`. Mock mode is the default and does not need an API key. To use a
real OpenAI-compatible provider, edit `.env` before starting and set `MODEL_PROVIDER`,
`MODEL_BASE_URL`, `MODEL_NAME`, and your local `MODEL_API_KEY`; DeepSeek configuration is shown
in the Provider section above. The key is read at runtime and is not copied into the image.

Use `docker compose logs -f` when you need live logs, and `docker compose down` for a normal shutdown.

The API container exposes:

- `http://localhost:8000/` — the browser product
- `http://localhost:8000/health` — process health
- `http://localhost:8000/ready` — database readiness (also used by the Compose healthcheck)

Normal lifecycle commands are:

```powershell
docker compose stop
docker compose start
docker compose down
docker compose up
docker compose down -v
```

The named `journey-data` volume preserves `GameInstance`, `AgentTask`, and Scenario data across
stop/start and normal `down`/`up`. The lifecycle commands mean:

- `stop` / `start`: stop and resume the existing containers.
- normal `down` / `up`: recreate containers while preserving Journey Agent data in the named volume.
- `down -v`: remove the current Journey Agent Compose volume for a complete local Journey Agent
  data reset; run `docker compose up --build` again to initialize it.

The `down -v` command only removes data owned by this Journey Agent Compose project.

## Manual local development

The supported development toolchain is Python 3.12, Node 22, and `uv` for the Python environment.

### Backend

From the repository root:

```powershell
uv sync --python 3.12 --extra dev
Copy-Item .env.example .env
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Local development uses `sqlite+pysqlite:///./journey_dev.db`. Keep that `DATABASE_URL` in `.env`
for the backend, editor, and player UI; do not point local commands at historical `journey_manual`
databases. The backend startup log prints the resolved database target. Edit the other `.env`
settings before starting if you need an OpenAI-compatible provider. The seed command publishes the
built-in Starfire, Medical, and Linjiang V1 definitions through the normal Scenario lifecycle.
`/health` and `/ready` are available once the API is running.

### Frontend

In a second terminal:

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 4173
```

Open `http://127.0.0.1:4173`. Vite proxies `/api` to the backend on port 8000. For a production
bundle, run `npm run build`; when `frontend/dist` exists, the FastAPI app can serve that bundle.

## HTTP/API and pages

| Area | Main routes/pages |
| --- | --- |
| Health | `/health`, `/ready` |
| Scenario Library | `/scenarios`, `/scenarios/new`, `/scenarios/:id` |
| Editor | `/scenarios/:id/edit/:section` and object inspectors |
| Games | `/games`, `/games/new`, `/games/:id` |
| Player API | `/api/v1/scenarios/*`, `/api/v1/games/*` |
| Developer API | `/api/v1/developer/games/:id/snapshot` and `/history` |

The Scenario authoring endpoints cover Draft revisions, validation, sandbox, publish, restore,
references, stable-key renames, safe deletion, and immutable Version snapshots. The Game
endpoints cover exact-Version creation, Knowledge-safe Play, Goal submission, pacing
acknowledgements, approvals, Replan, history, abandon, archive, and deletion. Detailed current
contracts are in [`docs/scenario-authoring.md`](docs/scenario-authoring.md) and
[`docs/architecture.md`](docs/architecture.md).

## Verification

Backend checks:

```powershell
uv run pytest --cov=app --cov-report=term-missing
uv run ruff check .
uv run ruff format --check app evals tests
uv run mypy app evals
uv run alembic upgrade head
```

Frontend checks (from `frontend`):

```powershell
npm run lint
npm run typecheck
npm test
npm run build
npx playwright install --with-deps chromium
npm run e2e
```

CI runs the frontend checks, mock-provider browser E2E, backend lint/format/type checks,
migrations, full pytest coverage, and a JavaScript syntax check. Real DeepSeek credentials are
not used in CI; provider tests use deterministic fakes or mocked HTTP responses.

## Repository map

```text
app/domain/       frozen V2 definitions, ObjectiveScope, world/runtime value objects
app/agent/        generic resolver, PlanningContext, provider, planner, validator, agent loop
app/services/     Scenario/Game lifecycle, Formal Play, projections, actions, sandbox
app/scenarios/    V2 parsing/validation/persistence and built-in definitions
app/api/          FastAPI adapters and Player/Developer DTOs
frontend/src/     React/Vite browser product and Editor
tests/            unit, contract, integration, lifecycle, provider, and E2E-support tests
migrations/       Alembic schema history
docs/             current architecture, authoring, and PlanningContext contracts
```

## Reliability and evaluation

Reliability is enforced in layers: strict Pydantic contracts, exact-Version lookup, frozen
ObjectiveScope, deterministic Plan validation, generic Action/Rule checks, persistence guards,
bounded Repair/Replan, and separate Player/Developer projections. Provider audit metadata makes
latency, context size, token usage, proposals, and rejection diagnostics inspectable without
exposing secrets or hidden Truth to players.

The repository includes deterministic unit/contract tests, integration tests for lifecycle,
isolation, approvals, sandbox, provider wiring, and both built-in scenarios, plus Playwright
browser tests. Real-model runs are bounded evaluations, not a claim of a large benchmark.

## Roadmap

Reasonable next steps are deeper Scenario Editor builders and authoring diagnostics, continued
dual-scenario validation, provider/evaluation dashboards, richer observability, and a fuller
human-in-the-loop product boundary. These are follow-ups, not promises that are already in the
runtime.

## Current documentation

- [`docs/architecture.md`](docs/architecture.md) — current runtime, API boundary, persistence,
  Formal Play, and provider architecture.
- [`docs/planning-context-v1.md`](docs/planning-context-v1.md) — canonical planning payload and
  validation contract.
- [`docs/scenario-authoring.md`](docs/scenario-authoring.md) — Draft, Editor, validation,
  sandbox, publication, and Version lifecycle.
- [`docs/archive/`](docs/archive/) — historical design and migration notes, not current source
  of truth.

## License

See [`LICENSE`](LICENSE).
