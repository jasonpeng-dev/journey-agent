# Journey Agent

[English](README.md) · [中文](README.zh.md)

Journey Agent is a generic, data-driven scenario runtime with an LLM Planner,
deterministic validation, explicit Truth/Knowledge separation, and auditable
Formal PLAY execution.

The runtime is ScenarioVersion-driven: a published immutable Version supplies
world content and declarative semantics, while reusable source code supplies
Goal resolution, planning, validation, Action execution, persistence, and
the browser product.

Unlike a chatbot, the Agent acts inside a versioned world with deterministic
validation and execution. Unlike a scripted game, outcomes emerge from the
Scenario's declarative contracts and the world's changing public state rather
than from a fixed dialogue tree.

## What is included

* Web-based Scenario authoring: authors design worlds, actors and roles,
  actions, rules, resources, goal semantics, and initial state in the Web
  Editor, then validate and publish immutable ScenarioVersions.
* Versioned gameplay: each Game binds to one exact published ScenarioVersion,
  so its world content, rules, and runtime history remain deterministic.
* Natural-language Custom Goals: players describe desired outcomes in their own
  words; authors may provide optional preset or suggested Goals as onboarding
  examples, and incomplete or ambiguous intent can be clarified.
* Agent planning and deterministic execution: the Agent plans HOW from the
  public world state, proposals are checked deterministically, and Runtime
  executes accepted Actions.
* Truth/Knowledge separation and replanning: authoritative Truth is distinct
  from Player/Agent-visible Knowledge; execution may reveal Knowledge and
  trigger replanning.
* Auditable game lifecycle: Formal PLAY, execution history, Archive,
  Checkpoint, Fork, and auditable runtime records.

## How it plays

1. Design a Scenario in the Web Editor: its World, Actors, Actions, Rules,
   Resources, goal semantics, and initial state.
2. Validate the Draft and publish an immutable ScenarioVersion.
3. Create a Game from one exact published Version.
4. Choose an author-provided suggested Goal or enter a natural-language Custom
   Goal. The runtime resolves the requested outcome and asks for clarification
   when the player's intent is incomplete or ambiguous.
5. The Agent plans from the currently known world. The player reviews and
   confirms Actions one step at a time.
6. Runtime execution changes the world and may reveal new Knowledge. When that
   changes what is legally known or planned, the Agent can replan.
7. The Goal completes when its deterministic world requirements are satisfied.

Scenario authors may provide optional preset or suggested Goals to help a
player start quickly, but those entries are an onboarding/example surface
rather than a fixed task set. Players can always enter a natural-language
Custom Goal. See [Custom Goals and Task Compilation](docs/custom-goals.md) for
the product contract and the WHAT-versus-HOW boundary.

## Architecture at a glance

    ScenarioVersion
      -> GameInstance
           -> Natural-language Goal
                -> Goal Resolution
                     -> Frozen Goal Contract
                          -> Planning
                               -> Validation
                                    -> Execution
                                         -> Truth / Knowledge update
                                              -> Replan or Completion

Formal PLAY coordinates each planning cycle and presents only validated
Actions to the player. Rejected proposals stay internal and do not mutate the
world or become player-facing plans.

See [docs/custom-goals.md](docs/custom-goals.md) for how a player's Goal
becomes a task and how WHAT/HOW responsibilities are divided.
See [docs/architecture.md](docs/architecture.md) for high-level runtime
boundaries, [docs/agent-planning-v2.md](docs/agent-planning-v2.md) for the
detailed planning contract, [docs/scenario-authoring.md](docs/scenario-authoring.md)
for Scenario publishing, and [docs/game-lifecycle.md](docs/game-lifecycle.md)
for the detailed GameInstance lifecycle.

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

Goal understanding and planning use separate provider configuration profiles;
the runtime keeps provider payloads and internal diagnostics out of the player
surface. See [docs/architecture.md](docs/architecture.md) and
[docs/agent-planning-v2.md](docs/agent-planning-v2.md) for the detailed
semantic/planning boundary and retry behavior.

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
| app/domain | ScenarioDefinitionV2, world/runtime values, and domain contracts |
| app/agent | Goal resolution, planning, provider, validation, and Agent loop |
| app/services | Scenario/Game lifecycle, Formal PLAY, actions, projections |
| app/scenarios | V2 parsing, validation, persistence, built-in definitions |
| app/api | FastAPI adapters and Player/Developer DTOs |
| frontend/src | React/Vite browser product and Editor |
| tests | Unit, contract, integration, lifecycle, provider, and E2E support |
| migrations | Alembic schema history |
| docs | Current custom-goals, architecture, planning, authoring, lifecycle, and archive |

## Documentation

Current authority:

* [docs/custom-goals.md](docs/custom-goals.md) — how a natural-language Goal
  becomes an executable task, when clarification is needed, and how the
  player's WHAT is separated from the Planner's HOW.
* [docs/architecture.md](docs/architecture.md) — how ScenarioVersion,
  GameInstance, Goal, Planning, Validation, Runtime, Truth/Knowledge, and
  Formal PLAY fit together.
* [docs/agent-planning-v2.md](docs/agent-planning-v2.md) — how the Agent gets
  bounded public context, composes supporting Actions, validates proposals,
  and replans when new Knowledge appears.
* [docs/scenario-authoring.md](docs/scenario-authoring.md) — what authors can
  declare in the Web Editor and how Draft → Validate → Publish works.
* [docs/game-lifecycle.md](docs/game-lifecycle.md) — how a Game stays bound to
  one exact Version and how Archive, Checkpoint, Fork, and history work.

Historical notes:

* [docs/archive](docs/archive/) is for archaeology only and is not current
  implementation authority.

Setup and run instructions are intentionally kept here; architecture and
planning semantics belong in the canonical docs above.
