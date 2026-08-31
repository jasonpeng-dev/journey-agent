# Journey Agent

[English](README.md) · [中文](README.zh.md)

Journey Agent 是一个通用、数据驱动的 Scenario runtime，包含 LLM
Planner、确定性 Validator、明确的 Truth/Knowledge 分离，以及可审计的
Formal PLAY 执行。运行时由发布后的 ScenarioVersion 驱动：不可变的
Version 提供世界内容和声明式语义，可复用的源代码负责 Goal resolution、
planning、validation、Action execution、persistence 和浏览器产品。

## 包含内容

* ScenarioDefinitionV2 Draft、validation、publication、不可变 Version，以及
  精确绑定 Version 的 GameInstance。
* Scenario Library 和结构化 Editor，覆盖 world、Actors、Actions、Rules、
  Objectives、planning metadata、initialization、references 和 Version history。
* Generic Goal Resolver、冻结的 ObjectiveScope、canonical PlannerInput V2、
  deterministic Validator、bounded 内部 REPAIR，以及 Knowledge-aware REPLAN。
* Declarative Action/Rule execution、Truth mutation、public Knowledge projection、
  Player-safe Formal PLAY、审批、不可变 archived runtime source、Fork 和 plan
  history。
* Generic built-in Scenario 通过同一套 runtime 和浏览器产品运行；engine 不包含
  scenario-specific gameplay 分支。

## 架构概览

~~~text
ScenarioVersion
  -> GameInstance
       -> Goal -> frozen ObjectiveScope
            -> Dependency Closure
                 -> PlannerInput V2 -> Provider PlanSegment
                      -> Validator -> formal AgentPlan
                           -> Runtime -> Truth / Knowledge
                                -> REPLAN or Complete
~~~

Formal PLAY 用一次 planning HTTP request 完成一个 planning cycle。Backend
在内部执行 bounded REPAIR，并返回最终状态。被拒绝的 proposal 只保存在
PlanningAttempt audit rows 中，不会成为 Player 可见的 plan，也不会创建
runtime operation。

详见 [docs/architecture.md](docs/architecture.md) 了解 high-level runtime
boundaries，[docs/agent-planning-v2.md](docs/agent-planning-v2.md) 了解详细
planning contract，[docs/scenario-authoring.md](docs/scenario-authoring.md)
了解 Scenario publishing，以及 [docs/game-lifecycle.md](docs/game-lifecycle.md)
了解详细的 GameInstance lifecycle。

ACTIVE GameInstance 只有在没有 non-terminal Task、pending WorldOperation、
pending ActionDecisionRequest 或 reserved resource value 时才能 Archive。
ARCHIVED instance 是不可变的只读 runtime source。Checkpoint 在 source 保持
不变的情况下创建独立的 ARCHIVED snapshot；Fork 从 ARCHIVED source 创建新的
独立 ACTIVE instance，保留相同的 exact ScenarioVersion、runtime state 和
inherited formal history。详见 [GameInstance lifecycle](docs/game-lifecycle.md)。

## Provider 配置

Mock mode 是安全默认值，不会发起网络模型请求。
默认本地开发和 Docker 启动都不需要 API key。

~~~text
MODEL_PROVIDER=mock
~~~

如需使用真实 OpenAI-compatible Provider，请只在本地 `.env` 中配置。
Journey Agent 支持 OpenAI-compatible endpoint，包括 OpenAI 以及 DeepSeek
等兼容服务。

完整配置项和示例请参见 [`.env.example`](.env.example)。

不要提交 API key。

## Docker 快速启动

从干净 checkout 开始：

~~~text
git clone https://github.com/jasonpeng-dev/journey-agent.git
cd journey-agent
Copy-Item .env.example .env
docker compose up --build -d
~~~

macOS/Linux 使用 cp .env.example .env。打开
[http://localhost:8000](http://localhost:8000)
Mock mode 不需要 API key。

常用 lifecycle 命令：

~~~text
docker compose logs -f
docker compose stop
docker compose start
docker compose down
docker compose down -v
~~~

命名的 Compose volume 会在普通 stop/start 和 down/up 之间保留本地
Journey Agent 数据；down -v 只删除本 Compose project 的数据。

## 本地开发

支持的工具链是 Python 3.12、Node 22 和 uv。

Backend 命令从 repository root 执行：

~~~text
uv sync --python 3.12 --extra dev
Copy-Item .env.example .env
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
~~~

本地开发使用 ./journey_dev.db 中的 SQLite。请在 .env 中让 backend、
Editor 和 Player UI 使用同一个数据库目标，不要把本地命令指向历史数据库。

Frontend 在第二个 terminal 中运行：

~~~text
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 4173
~~~

打开 [http://127.0.0.1:4173](http://127.0.0.1:4173)
Vite 会把 API request proxy 到 backend。

浏览器 lifecycle surface 保持精简：active game 可以从 detail 或 list card
Archive，archived game 可以从任一位置 Fork。Fork request 使用一个
creation key 提供 retry-safe idempotency，成功后导航到新的 target。

## 主要 HTTP surfaces

| Area | Routes |
| --- | --- |
| Health | /health, /ready |
| Scenario Library | /scenarios and /api/v1/scenarios |
| Editor | /scenarios/:id/edit/:section |
| Games | /games and /api/v1/games |
| Developer | /api/v1/developer/games/:id/snapshot and history |

## 验证

Backend：

~~~text
uv run pytest --cov=app --cov-report=term-missing
uv run ruff check .
uv run ruff format --check app tests frontend/e2e/prepare_history_fixture.py
uv run mypy app
uv run alembic upgrade head
~~~

Frontend，从 frontend 目录运行：

~~~text
npm run lint
npm run typecheck
npm test
npm run build
npm run e2e
~~~

Real Provider calls 不属于 CI。Provider tests 使用 deterministic fakes 或
mocked HTTP responses；real-model runs 只作为有边界的手动 evaluation。
Browser E2E 包含三个 deterministic smoke：Basic Product、PLAY Presentation
和 Checkpoint/Fork；这些测试使用 mock Provider，不调用真实 Provider。

## 仓库结构

| Path | Responsibility |
| --- | --- |
| app/domain | ScenarioDefinitionV2、ObjectiveScope、world/runtime values |
| app/agent | Goal resolver、PlannerInput、provider、Validator、Agent loop |
| app/services | Scenario/Game lifecycle、Formal PLAY、actions、projections |
| app/scenarios | V2 parsing、validation、persistence、built-in definitions |
| app/api | FastAPI adapters 和 Player/Developer DTOs |
| frontend/src | React/Vite browser product 和 Editor |
| tests | Unit、contract、integration、lifecycle、provider 和 E2E support |
| migrations | Alembic schema history |
| docs | 当前 architecture、planning、authoring、lifecycle 和 archive |

## 文档

Current authority：

* [docs/architecture.md](docs/architecture.md)：high-level system architecture。
* [docs/agent-planning-v2.md](docs/agent-planning-v2.md)：Agent Harness 和
  planning contract。
* [docs/scenario-authoring.md](docs/scenario-authoring.md)：Scenario authoring
  和 publishing contract。
* [docs/game-lifecycle.md](docs/game-lifecycle.md)：GameInstance Archive、
  Checkpoint 和 Fork lifecycle。

[docs/archive](docs/archive/) 仅保存 historical material，不是 current
implementation authority。

Setup 和 run instructions 保持在本 README；architecture、planning、authoring
和 GameInstance lifecycle 的详细语义分别由上述 current docs 负责。
