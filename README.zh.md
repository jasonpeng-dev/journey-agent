[English](README.md) · [中文](README.zh.md)

# Journey Agent

Journey Agent 是一个数据驱动、通用的 LLM Agent 运行时，用于执行面向目标的交互式场景。
ScenarioVersion 定义世界，源代码提供可复用的 Agent、Action、Rule、持久化和浏览器产品
运行时。项目的重点是让模型提案可审计、可验证、可安全执行，而不是把聊天机器人包装成
一组场景专用脚本。

## 当前已实现

- `ScenarioDefinitionV2`、每个 Scenario 唯一的 mutable Current Draft、immutable Published
  Version，以及绑定 exact Version 的 GameInstance。
- React/TypeScript Scenario Library 和结构化 Editor，可编辑 World、Actors、Actions、Rules、
  Objectives、Planning metadata、Initialization、Validation、引用关系和 Version History。
- Blank、Example、Clone 创建流程；带 revision 的 Draft 乐观并发；Publish Gate；以及不创建
  正式 GameInstance 行的隔离 Draft Preview/Test sandbox。
- Formal Play：自然语言 Goal、持久化 AgentTask、完整 Plan/Plan History、仅暴露 Knowledge
  的玩家投影、Action/debrief checkpoint、approval、Repair/Replan、blocked 状态、archive 和
  recovery。
- Starfire 与 Medical 两个 V2 定义通过同一套 Engine 和浏览器 UI 运行。
- Player 与带凭证的 Developer 投影分离。Normal Play 不返回 hidden Truth、Rule AST 或
  provider 内部信息。

## 架构概览

```text
Goal
  -> Goal Resolver（只从当前 Version 的 Objective 候选中解析）
  -> AgentTask 上冻结 ObjectiveScope
  -> PlanningContext V1
  -> deterministic planner 或结构化 provider proposal
  -> deterministic backend validation / bounded Repair
  -> Generic Action + declarative Rule 执行
  -> Truth mutation 与 Knowledge update
  -> Objective verification、完成、approval 或 bounded Replan
```

应用组合边界负责构造 configured provider，并将它注入 generic Goal Resolver 和 Agent
service。HTTP router 与 React 页面只是 adapter，不复制 Rule evaluation、Action effect、
Objective completion、authority 或 Version 语义。

详见 [`docs/architecture.md`](docs/architecture.md) 和
[`docs/planning-context-v1.md`](docs/planning-context-v1.md)。

## 核心概念

### Scenario 与 Version

`ScenarioDefinitionV2` 是一个封闭的 declarative schema，包含 metadata、World 的
Node/Fact/Relation/Resource、Actors/Roles、Interactions、Actions、Rules、Objectives、Goal
Resolution、Planning 和 Initialization。作者可以定义内容与受支持的 structured primitive，
但不能写任意 gameplay code，也不能在 Scenario 中绑定某个 model provider。

Draft 在编辑过程中可以是不完整的。Validation 与 Publish 时才构造严格的 v2 definition。
发布会创建 immutable snapshot。正式 Game 只能从已发布的 `scenario_version_id` 创建，并在
整个生命周期内绑定这个 exact snapshot，不会跟随 latest pointer。

### Goal Resolver 与 ObjectiveScope

Resolver 先对 exact Version 的 Objective key、name、alias 和 example 做标准化匹配。如果
Scenario 允许 LLM fallback 且 provider 已配置，provider 只会收到当前 Version 的 Objective
候选，也只能选择这些 key。Goal 成功后创建持久化 `AgentTask`，其非空、冻结的
`ObjectiveScope` 不会被后续 Replan 扩大。

### Planning、Repair 与 Replan

`PlanningContext V1` 是 canonical model input：goal、current Knowledge、relevant Actions、
Actors、Targets、previous execution context 和 Scenario planning hints。Provider 根据这些
信息自己选择 Action、Actor、Target、parameters 和 order。旧的 Actor × Action × Target
Candidate Catalog 只保留为 compatibility view，不是 OpenAI-compatible 的 canonical payload。

Initial Planning 与 Replan 都只产生 proposal。Backend 会在持久化 executable steps 前，确定性
检查 binding、parameter、exact-Version reference、objective relevance/coverage/order、
authority 和 rejected-proposal constraint。Provider 模式下可以带着结构化 diagnostics 进行
有限次数 Repair。Provider 失败或最终仍不合法时会变成明确的 application error 或 blocked
状态，不会静默切换成另一套 planner。

### Truth、Knowledge 与执行

Truth 是用于 Rule evaluation 和 Objective verification 的 authoritative instance state。
Knowledge 是用于 planning 和正常 Player response 的 visibility-filtered projection。
`GenericActionService` 执行通用 Action 检查，创建 instance-scoped WorldOperation，并应用
确定性的 Rule outcome/effect。Condition 与 Effect 使用 V2 支持的结构化 primitive；generic
engine 中没有 Starfire-specific gameplay 分支。

### Formal Play

Formal Play 是建立在 generic services 之上的 application orchestrator。它持久化 plan start、
action acknowledgement、debrief、Replan、approval、completed、blocked、aborted 等阶段的
checkpoint。浏览器展示选中 Task 的 Mission Log 与 Plan History；一个 GameInstance 可以在
Goal 完成后继续运行，但同一时间只允许一个 Active Task。Abandon/Archive 会取消尚未产生
world mutation 的 pending approval 与 unsettled operation，已经发生的 mutation 不回滚。

## Provider 支持

服务端通过 `.env` 中的 `MODEL_PROVIDER` 选择 provider：

| 模式 | 行为 |
| --- | --- |
| `mock` | 不发送 HTTP 请求，使用 deterministic exact match 和 generic planning，适合测试与离线运行。 |
| `openai_compatible` | 使用通用 OpenAI-compatible JSON adapter 处理 Goal selection 以及 Plan/Repair/Replan proposal。 |

Adapter 使用以下环境变量（API key 只放在本地，绝不要提交）：

```dotenv
MODEL_PROVIDER=mock
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4.1-mini
MODEL_API_KEY=
MODEL_TIMEOUT_SECONDS=20
```

如果要连接 DeepSeek-compatible endpoint，可以这样配置，再填入账户可用的模型名：

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-v4-flash
MODEL_API_KEY=<local secret>
MODEL_TIMEOUT_SECONDS=60
```

ScenarioVersion 与 provider 无关。Backend 会记录不含 secret 的 call metadata（call type、
latency、context bytes，以及 provider 返回时的 usage 字段），不会记录 API key。

## Docker 快速启动

第一次使用时，Docker 是最简单的路径。Docker Compose 会构建 React 前端，由现有 FastAPI
应用提供浏览器页面，启动时自动执行 Alembic migration 和幂等的内置 seed，并把 SQLite
数据保存在 named volume 中。

### Stable Release（推荐）

稳定版本是固定且可复现的，首次体验优先推荐使用它；相同版本也可以从对应的 GitHub
Release source archive 下载。

```powershell
git clone --branch v0.1.0 --depth 1 https://github.com/jasonpeng-dev/journey-agent.git
cd journey-agent
Copy-Item .env.example .env
docker compose up --build -d
```

在 macOS/Linux 上，第三行使用 `cp .env.example .env`。

### Latest Main

如果希望体验最新开发状态，可以使用这条路径。`main` 会随着开发继续变化，内容可能
与稳定版本不同。

```powershell
git clone https://github.com/jasonpeng-dev/journey-agent.git
cd journey-agent
Copy-Item .env.example .env
docker compose up --build -d
```

在 macOS/Linux 上，第三行使用 `cp .env.example .env`。

打开 `http://localhost:8000`。默认是 mock 模式，不需要 API key。如果要使用真实的
OpenAI-compatible provider，请在启动前编辑 `.env`，填写 `MODEL_PROVIDER`、
`MODEL_BASE_URL`、`MODEL_NAME` 和本地 `MODEL_API_KEY`；上面的 Provider 章节包含 DeepSeek
示例。API key 只在运行时读取，不会复制进 Docker image。

需要查看实时日志时使用 `docker compose logs -f`，正常关闭时使用 `docker compose down`。

容器提供以下地址：

- `http://localhost:8000/` — 浏览器产品入口
- `http://localhost:8000/health` — 进程健康状态
- `http://localhost:8000/ready` — 数据库 readiness，也用于 Compose healthcheck

正常停止、重启和启动：

```powershell
docker compose stop
docker compose start
docker compose down
docker compose up
docker compose down -v
```

named `journey-data` volume 会在 stop/start 以及正常 `down`/`up` 后保留 GameInstance、
AgentTask 和 Scenario 数据。生命周期命令的含义是：

- `stop` / `start`：停止或恢复已有 containers。
- 普通 `down` / `up`：重新创建 containers，但保留 named volume 中的 Journey Agent 数据。
- `down -v`：删除当前 Journey Agent Compose volume，完全重置本地 Journey Agent 数据；
  然后再次执行 `docker compose up --build` 重新初始化。

`down -v` 只删除当前 Journey Agent Compose project 所拥有的数据，不会清空机器上的其他 Docker 数据。

## 手工本地开发

当前支持的工具链是 Python 3.12、Node 22 和 Python 环境管理工具 `uv`。

### Backend

在仓库根目录运行：

```powershell
uv sync --python 3.12 --extra dev
Copy-Item .env.example .env
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

如果需要 OpenAI-compatible provider，请在启动前编辑 `.env`。seed 命令会通过正常 Scenario
lifecycle 发布内置的 Starfire 与 Medical V2 definition。服务启动后可访问 `/health` 和
`/ready`。

### Frontend

另开一个终端：

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 4173
```

打开 `http://127.0.0.1:4173`。Vite 会把 `/api` 代理到 8000 端口的 Backend。构建生产包时
运行 `npm run build`；存在 `frontend/dist` 后，FastAPI 也可以直接提供该静态包。

## HTTP/API 与页面

| 范围 | 主要路由/页面 |
| --- | --- |
| Health | `/health`、`/ready` |
| Scenario Library | `/scenarios`、`/scenarios/new`、`/scenarios/:id` |
| Editor | `/scenarios/:id/edit/:section` 以及对象 Inspector |
| Games | `/games`、`/games/new`、`/games/:id` |
| Player API | `/api/v1/scenarios/*`、`/api/v1/games/*` |
| Developer API | `/api/v1/developer/games/:id/snapshot`、`/history` |

Scenario API 覆盖 Draft revision、validation、sandbox、publish、restore、references、stable-key
rename、safe delete 和 immutable Version snapshot。Game API 覆盖 exact-Version 创建、
Knowledge-safe Play、Goal submission、pacing acknowledgement、approval、Replan、history、
abandon、archive 和 delete。完整的当前契约见
[`docs/scenario-authoring.md`](docs/scenario-authoring.md) 与
[`docs/architecture.md`](docs/architecture.md)。

## 验证命令

Backend：

```powershell
uv run pytest --cov=app --cov-report=term-missing
uv run ruff check .
uv run ruff format --check app evals tests
uv run mypy app evals
uv run alembic upgrade head
```

Frontend（在 `frontend` 目录）：

```powershell
npm run lint
npm run typecheck
npm test
npm run build
npx playwright install --with-deps chromium
npm run e2e
```

CI 会运行 frontend 检查、使用 mock provider 的浏览器 E2E、backend lint/format/type 检查、
migration、完整 pytest coverage 和 JavaScript syntax check。CI 不使用真实 DeepSeek credential；
provider 测试使用 deterministic fake 或 mocked HTTP。

## 仓库结构

```text
app/domain/       冻结的 V2 definition、ObjectiveScope、world/runtime value object
app/agent/        generic resolver、PlanningContext、provider、planner、validator、agent loop
app/services/     Scenario/Game lifecycle、Formal Play、projection、Action、sandbox
app/scenarios/    V2 parsing/validation/persistence 与内置 definition
app/api/          FastAPI adapter 与 Player/Developer DTO
frontend/src/     React/Vite browser product 与 Editor
tests/            unit、contract、integration、lifecycle、provider 与 E2E 支持测试
migrations/       Alembic schema history
docs/             当前 architecture、authoring、PlanningContext 契约
```

## Reliability 与评估

系统通过多层约束保证可靠性：严格 Pydantic contract、exact-Version lookup、冻结的
ObjectiveScope、确定性的 Plan validation、generic Action/Rule 检查、持久化 guard、bounded
Repair/Replan，以及 Player/Developer projection 分离。Provider audit metadata 可以查看
latency、context size、token usage、proposal 和 rejection diagnostics，但不会把 secret 或
hidden Truth 暴露给玩家。

仓库包含 deterministic unit/contract tests、lifecycle/isolation/approval/sandbox/provider
wiring/双场景 integration tests，以及 Playwright browser tests。真实模型测试是有边界的
evaluation run，并不宣称是大规模 benchmark。

## 后续方向

合理的下一步包括：更完整的 Scenario Editor builder 与 authoring diagnostics、持续的双场景
验证、provider/evaluation dashboard、更丰富的 observability，以及更完整的 human-in-the-loop
产品边界。这些是后续方向，不代表已经在运行时实现。

## 当前文档

- [`docs/architecture.md`](docs/architecture.md) — 当前 runtime、API boundary、persistence、
  Formal Play 与 provider architecture。
- [`docs/planning-context-v1.md`](docs/planning-context-v1.md) — canonical planning payload 与
  validation contract。
- [`docs/scenario-authoring.md`](docs/scenario-authoring.md) — Draft、Editor、validation、
  sandbox、publication 与 Version lifecycle。
- [`docs/archive/`](docs/archive/) — 历史设计与 migration 记录，不是当前 source of truth。

## License

见 [`LICENSE`](LICENSE)。
