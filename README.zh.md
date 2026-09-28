# Journey Agent

[English](README.md) ｜ 中文说明

Journey Agent 是一个通用、数据驱动的场景运行时，提供 Web 场景编辑器、
自然语言目标、LLM 规划、确定性校验与执行、明确的 Truth/Knowledge
分离，以及可审计的 Formal PLAY。

Scenario 内容是数据。可复用的运行时代码解释 Scenario，而不是把特定场景的
玩法硬编码进程序。

## 项目能力

- 在 Web 场景编辑器中编写并校验 Scenario 内容。
- 发布不可变的 ScenarioVersion，并让 Game 精确绑定某个 Version。
- 让玩家用自然语言描述想要达成的目标。
- 解析 Goal、规划实现方式、逐步校验并执行。
- 将真实世界状态与玩家当前知道的内容分开记录。
- 当新 Knowledge 改变可用信息时重新规划。

主要产品入口包括场景库、编辑器和 Games。

## 工作方式

~~~text
Scenario
  -> Draft / Publish
  -> immutable ScenarioVersion
  -> Game
  -> 自然语言 Goal
  -> Agent Planning
  -> 确定性校验
  -> Runtime
  -> Truth / Knowledge
  -> Replan or Complete
~~~

作者编辑 Scenario 数据并发布不可变 Version。每个 Game 都精确绑定一个已发布
Version。玩家表达要完成的 WHAT，Planner 选择 HOW，校验器和运行时共同保证
执行遵守 Scenario contract。

Scenario 可以导入和导出为可移植的 .scenario.json artifact。导入会创建新的
Scenario identity，不会覆盖已有 Scenario。

## 快速启动

从干净的 checkout 开始：

~~~text
git clone https://github.com/jasonpeng-dev/journey-agent.git
cd journey-agent
docker compose up --build -d
~~~

默认 Mock mode 不需要 .env。打开：

http://localhost:8000

全新的 Docker 安装会自动运行 migrations。Scenario 和 Game 库初始为空；
启动过程不会自动 seed 或 import Scenario 内容。

常用命令：

~~~text
docker compose logs -f
docker compose down
~~~

## 使用 OpenAI

默认 profile 是 Mock，不需要 .env，不需要 API key，也不会发起模型网络请求。

启用 OpenAI：

1. 在仓库根目录创建未跟踪的 .env。
2. 只从 [.env.example](.env.example) 复制 OPENAI 区块。
3. 在 .env 中填写 MODEL_API_KEY。
4. 按平常方式启动 backend 或 Docker Compose。

公开示例使用 GPT-5.6 Luna 处理 Goal 语义工作，使用 GPT-5.6 Terra 进行规划。
用户可以在 .env 中修改配置的模型名称。

## 官方示例

仓库中跟踪的官方 artifact：

~~~text
scenarios/examples/linjiang_infrastructure_recovery.scenario.json
~~~

使用 CLI 显式导入：

~~~text
docker compose exec api uv run journey scenario import /app/scenarios/examples/linjiang_infrastructure_recovery.scenario.json
~~~

同一个 artifact 也可以从 Web 场景库导入。

## 本地开发

支持的工具链：Python 3.12、Node 22 和 uv。

在仓库根目录启动 backend：

~~~text
uv sync --python 3.12 --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
~~~

没有 .env 时，内置默认值使用 Mock 和 ./journey_dev.db。

在第二个终端启动 frontend：

~~~text
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 4173
~~~

打开 http://127.0.0.1:4173。Vite 会把 API 请求代理到 backend。

## 验证

Backend：

~~~text
uv run pytest
uv run ruff check .
uv run mypy app
~~~

在 frontend 目录运行：

~~~text
npm run lint
npm run typecheck
npm test
npm run build
npm run e2e
~~~

CI 和 provider 测试使用确定性的 mock 或 mocked HTTP response，不需要真实的
OpenAI 调用。

## 文档

- [架构](docs/architecture.md) —— 系统边界和整体拓扑。
- [Scenario authoring](docs/scenario-authoring.md) —— 编辑器、Draft、Version、
  portability、校验和删除规则。
- [Custom Goals](docs/custom-goals.md) —— Goal 语义、WHAT/HOW 和必需 Goal slot。
- [Agent planning](docs/agent-planning-v2.md) —— 规划、校验、REPAIR、REPLAN
  和 provider policy。
- [Game lifecycle](docs/game-lifecycle.md) —— Version 绑定、Archive、Checkpoint、
  Fork 和历史。

docs/archive/ 仅包含历史材料，不是当前实现的权威来源。
