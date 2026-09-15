# Journey Agent

[English](README.md) · [中文](README.zh.md)

Journey Agent 是一个通用、数据驱动的 Scenario runtime，包含 LLM
Planner、确定性 Validator、明确的 Truth/Knowledge 分离，以及可审计的
Formal PLAY 执行。运行时由发布后的 ScenarioVersion 驱动：不可变的
Version 提供世界内容和声明式语义，可复用的源代码负责 Goal resolution、
planning、validation、Action execution、persistence 和浏览器产品。

它不同于普通 chatbot：Agent 在有版本约束的世界中行动，并经过确定性验证和
执行；它也不同于 scripted game：结果来自 Scenario 的声明式 contract 和不断
变化的公开世界状态，而不是固定的对话树。

## 包含内容

* Web-based Scenario authoring：作者通过 Web Editor 设计 world、actors/roles、
  actions、rules、resources、goal semantics 和 initial state，并经过 validation
  发布 immutable ScenarioVersions。
* Versioned gameplay：每个 Game 精确绑定一个发布后的 ScenarioVersion，保证
  世界内容、规则和运行历史基于确定版本。
* Natural-language Custom Goals：玩家可以自由输入自然语言 Goal；作者可提供
  optional preset/suggested Goals 作为 onboarding/example，意图不完整或有歧义时
  系统会请求 clarification。
* Agent planning and deterministic execution：Agent 根据当前公开世界状态自主规划
  HOW，系统确定性验证 proposal，通过后由 Runtime 执行 Action。
* Truth/Knowledge separation and replanning：Truth 与 Player/Agent 可见 Knowledge
  分离；执行可能揭示新 Knowledge，并在需要时驱动 REPLAN。
* Auditable game lifecycle：支持 Formal PLAY、执行历史、Archive、Checkpoint、
  Fork 和可审计 runtime 记录。

## 如何游玩

1. 在 Web Editor 中设计 Scenario：World、Actors、Actions、Rules、Resources、
   goal semantics 和初始状态。
2. 验证 Draft，并发布不可变的 ScenarioVersion。
3. 从某一个精确的已发布 Version 创建 Game。
4. 选择作者提供的 suggested Goal，或输入自然语言 Custom Goal。运行时会解析
   你想达成的结果；如果意图不完整或有歧义，会先请求 clarification。
5. Agent 根据当前已知世界规划，玩家逐步查看并确认 Action。
6. Runtime 执行 Action，改变世界并可能揭示新的 Knowledge；当可用信息改变时，
   Agent 可以重新规划。
7. 当 Goal 的确定性世界条件满足后，目标完成。

Scenario 作者可以提供 optional preset/suggested Goal，作为帮助玩家开始的
onboarding/example surface，但它们不是固定任务集合；玩家仍然可以直接输入自然语言
Custom Goal。详见 [Custom Goals and Task Compilation](docs/custom-goals.md)，了解产品
contract 和 WHAT/HOW 边界。

## 架构概览

~~~text
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
~~~

Formal PLAY 协调每个 planning cycle，只把通过验证的 Action 呈现给玩家。
被拒绝的 proposal 只保留在内部，不会改变世界，也不会成为 Player 可见的 plan。

详见 [docs/custom-goals.md](docs/custom-goals.md)，了解玩家 Goal 如何变成任务以及
WHAT/HOW 的职责边界；详见 [docs/architecture.md](docs/architecture.md) 了解 high-level runtime
boundaries，[docs/agent-planning-v2.md](docs/agent-planning-v2.md) 了解详细
planning contract，[docs/scenario-authoring.md](docs/scenario-authoring.md)
了解 Scenario publishing，以及 [docs/game-lifecycle.md](docs/game-lifecycle.md)
了解详细的 GameInstance lifecycle。

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

Goal 理解和 planning 使用分离的 Provider 配置 profile；运行时不会把 provider
payload 或内部诊断暴露给玩家。详细的 semantic/planning 边界和 retry 行为请见
[docs/architecture.md](docs/architecture.md) 与
[docs/agent-planning-v2.md](docs/agent-planning-v2.md)。

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
| app/domain | ScenarioDefinitionV2、world/runtime values 和 domain contracts |
| app/agent | Goal resolution、planning、provider、validation 和 Agent loop |
| app/services | Scenario/Game lifecycle、Formal PLAY、actions、projections |
| app/scenarios | V2 parsing、validation、persistence、built-in definitions |
| app/api | FastAPI adapters 和 Player/Developer DTOs |
| frontend/src | React/Vite browser product 和 Editor |
| tests | Unit、contract、integration、lifecycle、provider 和 E2E support |
| migrations | Alembic schema history |
| docs | 当前 custom-goals、architecture、planning、authoring、lifecycle 和 archive |

## 文档

当前权威文档：

* [docs/custom-goals.md](docs/custom-goals.md)：自然语言 Goal 如何变成可执行任务、何时需要澄清，以及玩家 WHAT 与 Planner HOW 如何分工。
* [docs/architecture.md](docs/architecture.md)：ScenarioVersion、GameInstance、Goal、Planning、Validation、Runtime、Truth/Knowledge 和 Formal PLAY 如何组合。
* [docs/agent-planning-v2.md](docs/agent-planning-v2.md)：Agent 如何获得受限的公开上下文、组合支援 Action、验证规划，并在新 Knowledge 出现后重新规划。
* [docs/scenario-authoring.md](docs/scenario-authoring.md)：作者可以在 Web Editor 中声明什么，以及 Draft → Validate → Publish 如何工作。
* [docs/game-lifecycle.md](docs/game-lifecycle.md)：Game 如何绑定一个精确 Version，以及 Archive、Checkpoint、Fork 和 history 如何工作。

[docs/archive](docs/archive/) 仅保存 historical material，不是 current
implementation authority。

Setup 和 run instructions 保持在本 README；architecture、planning、authoring
和 GameInstance lifecycle 的详细语义分别由上述 current docs 负责。
