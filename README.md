# Journey Agent Backend

An auditable strategic-officer Agent backend built with Python 3.12, FastAPI,
PostgreSQL 16, SQLAlchemy 2, and Alembic. The player acts as the lord and issues
high-level commands; appointed officers plan and execute only within their Role,
Tool, and Authority boundaries. A model never receives database access or a
generic state-patch capability.

The primary `starfire_command` demo uses a strategist, general, and steward to
restore Starfire Outpost and reopen the northern trade route. It demonstrates
Task -> Plan -> assigned Step -> Tool execution, per-Officer profiles, scoped
player approval, deterministic world-event settlement, safe replanning,
cross-session resume, and actor-level Trace. The original Journey to the West
and `starfire_outpost` NPC workflows remain as regression scenarios. Everything
runs without Unity or a real model key by default.

The default Mock provider produces a deterministic regression workflow. With a
real provider, the Plan structure, arguments, approval points, and number of
replans may vary, while the same backend validation, authority, Tool execution,
and deterministic world-settlement boundaries still apply.

## Quick start

Prerequisites: Docker with Compose.

```bash
read -rsp "PostgreSQL password: " POSTGRES_PASSWORD
export POSTGRES_PASSWORD
docker compose up --build
```

In PowerShell, use `$env:POSTGRES_PASSWORD = Read-Host "PostgreSQL password"`
before `docker compose up --build`, or inject it through your secret manager.
The repository contains no default password.

Then open:

- Agent Debug Console: <http://localhost:8000/debug>
- Swagger: <http://localhost:8000/docs>
- health: <http://localhost:8000/health>
- readiness (includes a database query): <http://localhost:8000/ready>

Compose waits for PostgreSQL, applies the Alembic migration, idempotently seeds
the demo world, and starts the API. The default provider is the deterministic
mock, so no API key is required.

To reset all local Compose data:

```bash
docker compose down -v
```

This deletes the named development database volume.

## Local development

Install `uv`, then:

```bash
uv sync --python 3.12 --extra dev
copy .env.example .env
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload
```

On macOS/Linux, use `cp` instead of `copy`. Adjust `DATABASE_URL` when
PostgreSQL is not on localhost.

Verification:

```bash
uv run ruff check .
uv run ruff format --check app evals tests
uv run mypy app evals
uv run pytest
uv run journey-agent eval run --output eval-results
```

The last command executes 51 isolated scenarios and writes JSON and Markdown
reports. CI uses the mock provider and PostgreSQL, so it has no external model
cost.

## Real native tool calling

Set only environment variables; never commit credentials:

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4.1-mini
MODEL_API_KEY=replace-at-runtime
```

The provider sends Chat Completions `tools` definitions and consumes native
`tool_calls`. It does not parse tool requests from prose. Planning calls expose
only `create_task_plan` or `replan_task`; a missing or malformed planning call
gets at most one structured correction before a safe stop.
The adapter also works with compatible providers that implement this contract.
The API key is represented as a Pydantic secret and is never written to logs or
traces.

To run an opt-in real Planner evaluation separately from the deterministic CI
suite:

```bash
uv run journey-agent eval real-planner \
  --attempts 3 \
  --output eval-results-real-planner
```

This command requires `MODEL_PROVIDER=openai_compatible` and a runtime API key.
It reports Plan validation success, latency, model rounds, token usage, and Step
count. It is intentionally excluded from CI.

For the current strategic officer scenario, use the smaller DeepSeek smoke test
first. It makes one real planning request per attempt against an isolated
in-memory database, so it does not mutate the development database or execute a
full command:

```powershell
$env:MODEL_PROVIDER = "openai_compatible"
$env:MODEL_BASE_URL = "https://api.deepseek.com"
$env:MODEL_NAME = "deepseek-v4-flash"
$env:MODEL_API_KEY = "your-key-at-runtime"
$env:MODEL_TIMEOUT_SECONDS = "60"
& .venv\Scripts\journey-agent.exe eval real-strategic `
  --attempts 1 `
  --output eval-results-real-strategic
```

The smoke test requires a native structured planning Tool Call, a passed
`PlanValidator` result, at least one valid Step, and assignments to at least two
officers. After it passes, start the web server with the same environment to
exercise the full strategic command flow through the console.

The DeepSeek-compatible path has also been exercised end to end for the
strategic scenario: natural-language planning, cross-Officer Step assignment,
Tool execution, a failed world operation, state-aware replanning, scoped player
approval, and final Task completion. These real-model runs validate the
integration path, but they are intentionally nondeterministic and excluded from
CI.

## Architecture and safety boundary

```text
FastAPI routes
  -> TaskRouter / AgentOrchestrator / TaskOrchestrator
    -> command Session + Task owner (strategist)
    -> Planner Provider + Officer Profiles -> structured Plan proposal
    -> PlanValidator -> assignee, tools, arguments, permissions, goal coverage
    -> AgentTask -> versioned AgentPlan -> assigned AgentStep
    -> ToolRegistry + ToolExecutor
      -> Pydantic schema validation
      -> command-session and actual Step actor validation
      -> Role / Tool permission / Authority policy
      -> exact player Decision when approval is required
      -> idempotency and database transaction
      -> before/after ToolExecution trace
        -> GameService (deterministic rules and WorldOperation settlement)
          -> SQLAlchemy / PostgreSQL
```

Routes do not mutate ORM state directly. The agent sees only tool definitions;
handlers call application services. Every tool result has
`ok/code/message/retryable/data`. A failed write rolls back, records a sanitized
trace, and causes the orchestrator to refuse any model-generated success claim.
There are no arbitrary SQL, JSON Patch, reward-definition, or cross-NPC
relationship tools.

Agent limits are five model rounds, three calls per round, and eight calls per
run. Plans are limited to ten Steps, four waits, two replans, and two generation
attempts by default. Invalid Plan output may be corrected once; authorization
failures stop immediately. Provider timeout/error paths do not create an
executable Plan or execute state-changing tools.

`GameService` is not an Agent: it has no prompt, personality, memory, Plan, or
autonomous goal. It deterministically validates resources, settles military,
construction, and trade operations, writes world facts, and unlocks nodes.
Officers can request an action but cannot choose its outcome.

An Officer's effective Authority is the profile default merged with the
player-specific `OfficerAppointment.authority_overrides`. The policy is
validated before planning and again from locked, freshly loaded rows immediately
before execution. Invalid policies fail closed; the appointment policy version
is recorded on the Run and Tool trace.

The complete positioning, role boundaries, wait-state semantics, and V1 limits
are documented in
[docs/strategic-officer-architecture.md](docs/strategic-officer-architecture.md).

Short-term context contains at most 12 recent messages plus the conversation
summary, relationship, and five high-importance NPC memories. Long-term memory
is structured PostgreSQL data with a source session or event; no vector store or
RAG system is used.

## Demo flow

The deterministic domain walkthrough can be run after migration and seeding:

```bash
uv run python scripts/demo.py
```

It creates a player, issues and accepts the foothills quest, enters the encounter
node, resolves the encounter from level/strategy/item rules, advances the quest,
claims the reward exactly once, and unlocks Red Boy's cave.

The equivalent HTTP flow starts with:

```bash
curl -X POST http://localhost:8000/api/v1/players \
  -H "content-type: application/json" -d '{"name":"Wukong"}'
```

Use `GET /api/v1/players/{id}/nodes`, create a session with the seeded NPC UUID
shown in the world data, and send a message to
`POST /api/v1/sessions/{id}/messages`. Player choices such as entering a node,
accepting a quest, choosing encounter strategy, and claiming a reward remain
ordinary APIs rather than autonomous agent actions.

### Strategic Command Console

Open <http://localhost:8000/debug> for the light-themed
`starfire_command` operator view. The player can only issue a high-level command
through Shen Ce. The page shows verified domain resources, player-known
intelligence, the server-derived command timeline, versioned Plans, assigned
Officers, exact Decision arguments, pending WorldOperations, and an optional
developer Trace.

The browser reads one coherent server snapshot after every write:

```text
GET /api/v1/debug/strategic/snapshot?session_id=...
```

The strategic Debug facade wraps the existing TaskOrchestrator rather than
adding an alternate mutation path. After a command, decision, or world-event
settlement, it repeatedly calls the audited one-step orchestrator until the Task
reaches a real pause or terminal state. The UI therefore has no manual NPC,
Plan, Step, Tool, planner-mode, or `Advance one step` controls.

Hidden world truth and AgentRun/Tool Trace are excluded from the default
snapshot and fetched only when their developer toggles are enabled. Browser
storage retains only the current strategic Session identifier. All strategic
fixture and resolver routes return 404 in production.

### Strategic officer command demonstration

The console now serves only the strategic flow. With the default deterministic
Mock provider, the reference path is:

1. Open `/debug`; the console restores its previous Shen Ce Session or creates
   a fresh isolated `starfire_command` fixture.
2. Issue `Restore Starfire Outpost and reopen the northern trade route.` through
   Shen Ce's fixed command Session.
3. Shen Ce creates Plan v1 and assigns reconnaissance and military Steps to Han
   Lie, infrastructure and trade Steps to Lu Ning, and final verification to
   himself.
4. Use the visually separated Developer control to resolve each pending
   WorldOperation.
   The first valley-clearance attempt fails and reveals the enemy supply route.
5. Shen Ce creates Plan v2. Lu Ning's proposed 35-food village agreement exceeds
   his autonomous 30-food limit, so the Task enters
   `REQUIRES_PLAYER_DECISION` before any food is spent.
6. Approve the exact action, then continue. Han Lie disrupts supply and secures
   the valley; Lu Ning restores the outpost and tests the trade route.
7. Shen Ce verifies the final facts and reports the command complete.

The same provider is reused for all three officers in V1. The actual Officer is
selected from `AgentStep.assigned_npc_id`; that Officer's profile, memories,
permissions, and authority limits are loaded into the run. The external Session
remains owned by Shen Ce throughout.

### Legacy structured Task demonstration

The old NPC-workflow flow remains available through its APIs and automated
regression tests, but it is intentionally absent from `/debug`:

1. Create an `underpowered` Starfire fixture.
2. Select `Provider-generated plan` to exercise the Mock or real Planner, or
   `Deterministic baseline` to isolate the execution engine. Start the default
   goal to create a persisted Task and validated Plan v1.
3. Advance one step at a time through state inspection, quest issuance, and
   route preparation.
4. At `WAITING_FOR_USER`, play the encounter turn. The first underpowered
   attempt deterministically fails.
5. Advance again. The failed plan remains in the trace and Plan v2 is created.
6. Advance through state inspection and approved NPC assistance, then play the
   encounter again.
7. The second attempt succeeds. Advance through verified road safety, outpost
   restoration, access grant, and relationship update.

Every model-selected execution tool remains bound to the selected plan step and
its audited arguments. A different tool or different argument object is rejected
as a security failure. Tool success alone does not complete a step: the
orchestrator compares the structured tool result with the step's expected
outcome.

A player may also express the supported long-running goal naturally, for
example, `Help me restore Starfire Outpost and obtain safe access.` The
deterministic TaskRouter separates simple queries from this known multi-step
goal; the Planner then generates the internal Plan. The player does not need to
ask for a Plan explicitly.

Important endpoints:

- strategic snapshot: `GET /api/v1/debug/strategic/snapshot`
- strategic reset and command:
  `POST /api/v1/debug/strategic/reset`,
  `POST /api/v1/debug/strategic/commands`
- strategic decision and world-event facades:
  `/api/v1/debug/strategic/tasks/{task_id}/...`
- players/state/inventory/quests: `/api/v1/players/...`
- nodes and worlds: `/api/v1/worlds/...`, `/api/v1/players/{id}/nodes`
- sessions/messages: `/api/v1/sessions/...`
- encounters: `/api/v1/encounter-runs/...`
- audit: `/api/v1/agent-runs/{id}` and `/tool-executions`
- session trace: `/api/v1/sessions/{id}/trace`
- tasks: `POST /api/v1/tasks`, `POST /api/v1/tasks/{id}/advance`
- scoped decisions:
  `POST /api/v1/tasks/{task_id}/decisions/{decision_id}/resolve`
- deterministic Debug settlement:
  `POST /api/v1/debug/world-events/{operation_id}/resolve`
- task hierarchy and trace: `GET /api/v1/tasks/{id}` and `/trace`
- evals: `POST /api/v1/evals/runs`, then `/results`

All API errors use:

```json
{
  "error": {
    "code": "NODE_LOCKED",
    "message": "Target node is locked",
    "details": {},
    "request_id": "uuid"
  }
}
```

## Persistence and idempotency

PostgreSQL check/unique constraints enforce nonnegative gold/inventory, bounded
relationships, unique tool-call traces, quest idempotency, and encounter
idempotency. Services lock mutable rows where settlement matters. Reward
issuance updates player, inventory, quest, and node state in one transaction;
the quest reward status is the replay guard. Encounter settlement similarly
locks its run and rejects a second, different settlement key.

Migrations `0001` through `0004` describe the domain, conversation, memory,
trace, Task hierarchy, model-planning metadata, Officer appointments, strategic
resources, scoped decisions, and world operations. Migration `0004` backfills
historical Plan authors, Step assignees, and Run actors. The seed operation uses
stable UUIDv5 IDs and can be rerun.

## Tests and evaluations

Tests are grouped into unit, API/integration, contract, and agent suites. They
cover node rules, relationship boundaries, deterministic encounter behavior,
reward replay protection, transaction paths, error shape, strict tool schemas,
native provider structures, tool traces, maximum-call termination, and the rule
that a failed tool cannot become a success statement. The current suite contains
73 automated tests.

The 51 evaluation scenarios are executable cases, not prompt-only fixtures.
Each gets a fresh schema and seeded world, runs a real registered tool through
the executor or the constrained Planner pipeline, and scores expected versus
actual result codes. They include deterministic and Mock-generated structured
workflows plus three strategic-command cases covering the full Officer flow,
provider planning, and Chinese command routing. Categories cover tool selection,
guardrails, state consistency, quest flow, memory, task hierarchy, planning,
replanning, and strategic coordination. Reports include pass rate, category
counts, and P95 latency.

## Scope

This repository intentionally uses one orchestrator and one shared model
provider with multiple Officer profiles; it does not run independent,
concurrent model agents. V1 also requires commands to enter through an appointed
strategist Session rather than a generic dispatch center. The repository does
not implement a runtime Skill layer, a card-combat engine, vector search,
complex RAG, Unity, or a production player-facing game frontend.
