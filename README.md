# Journey Agent — Generic Scenario Browser Product

Journey Agent is a versioned, auditable game-agent platform built around one rule:

> Source code defines the generic engine; ScenarioVersion defines the game.

Phase D adds a React browser product to the Phase R Generic Engine. Authors edit one mutable
Current Draft, validate it, and publish immutable versions. Players start isolated GameInstances
from an exact published version and submit natural-language goals. The same generic Agent, Action,
Rule, approval, and persistence services run both Starfire and Medical scenarios.

## Product flow

```text
Scenario Library → Current Draft → Validate → Publish immutable Version
                 → New Game (exact Version) → Goal → Briefing → Execute one action cycle → Debrief
                 → Approval / Replan → Completed or Blocked → Archive
```

- Published versions and GameInstance version bindings are immutable.
- A Scenario has exactly one mutable Current Draft; incomplete drafts may be saved.
- Formal Play pauses before each player-visible Action and after each meaningful Action cycle. A
  normal acknowledgement controls presentation pacing only; it is not gameplay Approval.
- Formal Play separates immutable ObjectiveScope from player presentation: the left column shows
  Knowledge-only world state, the middle shows an append-only safe Mission Log and a physically
  separate current checkpoint, and the right shows every persisted AgentPlan as Plan History.
- Replan never overwrites player history: superseded Plans remain frozen, failed actions stay red,
  and their unexecuted actions appear cancelled while the new persisted Plan is added below.
- The formal Game page uses the three-column command-console layout established by the Phase R
  Debug UI while continuing to consume only Player-safe Phase D projections.
- Goal completion does not end the GameInstance; another Goal may be submitted.
- Player APIs expose Knowledge only. Hidden Truth exists only on credential-gated Developer APIs.
- Draft Preview/Test runs Generic Play in a disposable in-memory sandbox and never creates a formal
  GameInstance or binds Runtime state to a mutable Draft.
- Archive and abandon cancel unsettled work but never roll back applied world mutations.
- ACTIVE and archived Games can be permanently deleted in one authoritative backend transaction;
  instance runtime rows are removed without deleting the ScenarioVersion or any sibling Game.
- Rejected proposals become Task-scoped backend constraints; hard limits prevent approval/replan
  loops.
- Replanning does not repeat an already successful exact supporting proposal unless its declared
  effects are required again by the still-unsatisfied Objective scope.
- Model-backed planning shows a complete current strategy: known future steps may appear before they
  are executable, while the backend rechecks full runtime legality immediately before each Action.
- Model-backed planning receives the generic, entity-once PlanningContext V1 (Goal, Knowledge,
  Actions, Actors, Targets, previous execution, and authored planning hints). The provider chooses
  bindings and order; the former Actor x Action x Target candidate catalog is compatibility-only and
  is not sent in the canonical provider payload.

## Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL/SQLite
- Browser: React 18, TypeScript, Vite, TanStack Query, React Router
- Verification: pytest/coverage, Ruff, mypy strict, Vitest, Playwright Chromium, GitHub Actions
- Required frontend runtime: Node 22

## Local setup

```powershell
uv sync --python 3.12 --extra dev
Copy-Item .env.example .env
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Set `DEVELOPER_API_TOKEN` in `.env` to enable Developer View; the
credential is checked server-side and hidden Truth is never fetched by normal Play.

## Verification

```powershell
uv run pytest --cov=app --cov-report=term-missing
uv run ruff check app evals tests
uv run ruff format --check app evals tests
uv run mypy app evals
uv run alembic upgrade head

Set-Location frontend
npm run lint
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run e2e
```

Browser E2E expects the backend at `127.0.0.1:8000` with an upgraded, seeded database.

## Documentation

- [Phase D browser contract](docs/phase-d-browser-product-contract.md)
- [Phase D implementation and API/page inventory](docs/phase-d-implementation.md)
- [PlanningContext V1 provider contract](docs/planning-context-v1.md)
- [Runtime scope and exact-version contract](docs/phase-c-runtime-scope-contract.md)
- [Architecture v2](docs/Journey_Agent_Architecture_V2_R0.md)

Legacy v1 runtime, the old GameService/tools/planning fallback, and scenario-specific gameplay
Python are intentionally unsupported and must not be restored.
