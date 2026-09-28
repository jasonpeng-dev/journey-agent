# Journey Agent

[中文说明](README.zh.md)

Journey Agent is a generic, data-driven scenario runtime with a Web Scenario
Editor, natural-language Goals, LLM planning, deterministic validation and
execution, explicit Truth/Knowledge separation, and auditable Formal PLAY.

Scenario content is data. Reusable runtime code interprets the Scenario instead
of hard-coding scenario-specific gameplay.

## What it does

- Author and validate Scenario content in the Web Scenario Editor.
- Publish immutable ScenarioVersions and bind Games to an exact Version.
- Let players describe what they want in natural language.
- Resolve a Goal, plan how to achieve it, validate each step, and execute it.
- Keep true world state separate from what the player currently knows.
- Replan when newly revealed Knowledge changes the available information.

The main product entry points are the Scenario Library, the Editor, and Games.

## How it works

~~~text
Scenario
  -> Draft / Publish
  -> immutable ScenarioVersion
  -> Game
  -> Natural-language Goal
  -> Agent Planning
  -> Deterministic Validation
  -> Runtime
  -> Truth / Knowledge
  -> Replan or Complete
~~~

Authors work with Scenario data and publish an immutable Version. Each Game
binds to one exact published Version. The player supplies WHAT to achieve;
the Planner chooses HOW, while validation and runtime enforce the Scenario
contract.

Scenarios can be imported and exported as portable .scenario.json artifacts.
Import creates a new Scenario identity rather than overwriting an existing one.

## Quick start

From a clean checkout:

~~~text
git clone https://github.com/jasonpeng-dev/journey-agent.git
cd journey-agent
docker compose up --build -d
~~~

No .env is required for the default Mock mode. Open:

http://localhost:8000

A fresh Docker installation runs migrations automatically. The Scenario and
Game libraries start empty; startup does not seed or import Scenario content.

Useful commands:

~~~text
docker compose logs -f
docker compose down
~~~

## Use OpenAI

The default profile is Mock. It needs no .env, no API key, and makes no model
network request.

To enable OpenAI:

1. Create an untracked .env at the repository root.
2. Copy only the OPENAI block from [.env.example](.env.example).
3. Set MODEL_API_KEY in .env.
4. Start the backend or Docker Compose as usual.

The public example uses GPT-5.6 Luna for Goal semantic work and GPT-5.6 Terra
for planning. Users may change the configured model names in .env.

## Official example

The tracked official artifact is:

~~~text
scenarios/examples/linjiang_infrastructure_recovery.scenario.json
~~~

Import it explicitly with the CLI:

~~~text
docker compose exec api uv run journey scenario import /app/scenarios/examples/linjiang_infrastructure_recovery.scenario.json
~~~

The same artifact can also be imported from the Web Scenario Library.

## Local development

Supported toolchain: Python 3.12, Node 22, and uv.

Backend, from the repository root:

~~~text
uv sync --python 3.12 --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
~~~

Without .env, built-in defaults use Mock and ./journey_dev.db.

Frontend, in a second terminal:

~~~text
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 4173
~~~

Open http://127.0.0.1:4173. Vite proxies API requests to the backend.

## Verification

Backend:

~~~text
uv run pytest
uv run ruff check .
uv run mypy app
~~~

Frontend, from frontend:

~~~text
npm run lint
npm run typecheck
npm test
npm run build
npm run e2e
~~~

CI and provider tests use deterministic mocks or mocked HTTP responses; they do
not require real OpenAI calls.

## Documentation

- [Architecture](docs/architecture.md) — system boundaries and topology.
- [Scenario authoring](docs/scenario-authoring.md) — Editor, Draft, Version,
  portability, validation, and deletion.
- [Custom Goals](docs/custom-goals.md) — Goal semantics, WHAT/HOW, and required
  Goal slots.
- [Agent planning](docs/agent-planning-v2.md) — planning, validation, REPAIR,
  REPLAN, and provider policy.
- [Game lifecycle](docs/game-lifecycle.md) — Version binding, Archive,
  Checkpoint, Fork, and history.

docs/archive/ contains historical material only and is not current authority.
