# Journey Agent

[English](README.md) · [中文](README.zh.md)

Journey Agent 是一个通用、数据驱动的 LLM Agent runtime。它把 Planner、Validator、Runtime 和 Knowledge projection 分开：模型只根据公开的 canonical PlannerInput 选择行动，后端验证并执行，运行结果再形成下一轮可见知识。

## 当前能力

- Generic Planner / Validator / Runtime 分层，不为单一场景写规划逻辑。
- PlannerInput V2 以 dependency closure 为边界，保持 sparse、bounded，并由 canonical action、actor、target 和 Known World 数据统一表达。
- Truth 与 Knowledge 分离；Planner 和 Validator 不读取 Hidden Truth，UNKNOWN 不等于 false、zero 或 unavailable。
- Planner 可自主选择 Action、Actor、Target、参数和顺序；Validator 只判断已提交方案是否违反已知确定约束。
- PlanSegment 支持 sequential self-validation、bounded REPAIR、INFORMATION_BOUNDARY 和 REPLAN。
- Travel 只改变 Actor 位置；transport_resource 才负责跨 Region 搬运资源。
- Formal PLAY 使用一次请求完成一个 planning cycle，内部可执行 INITIAL/REPLAN 与 bounded REPAIR；只有 Validator 通过后才创建正式 AgentPlan 并进入 Runtime。

## 架构概览

```text
Scenario / Published Version
            │
            ▼
ObjectiveScope + Dependency Closure
            │
            ▼
Canonical PlannerInput V2
            │
            ▼
Planner Provider ──► PlanSegment
            │
            ▼
Sequential Validator
       │              │
       │ reject       │ accept
       ▼              ▼
 bounded REPAIR    AgentPlan / AgentStep
                          │
                          ▼
                    Runtime execution
                          │
                          ▼
               public Knowledge update / REPLAN
```

PlannerInput V2 是 Provider-facing 的唯一权威语义。旧的 PlanningContext、candidate catalog 和兼容字段只用于迁移或内部适配，不能产生第二套 Action/Actor/Target 资格。

## 文档入口

按以下顺序阅读：

1. [文档索引](docs/README.md)
2. [架构总览](docs/architecture.md)
3. [Agent Planning V2](docs/agent-planning-v2.md)
4. [场景作者指南](docs/scenario-authoring.md)

旧版 PlanningContext V1 设计已归档，仅用于迁移考古：[archive/planning-context-v1.md](docs/archive/planning-context-v1.md)。

## Provider 配置

Provider 通过环境变量配置。常用设置包括：

```text
MODEL_PROVIDER=deepseek
MODEL_NAME=deepseek-v4-flash
MODEL_THINKING_MODE=disabled
MODEL_REASONING_EFFORT=low
MODEL_MAX_OUTPUT_TOKENS=null
MODEL_TIMEOUT_SECONDS=null
MODEL_TOTAL_TIMEOUT_SECONDS=null
```

请按当前部署环境填写 API key、endpoint 和其他凭据；不要把凭据提交到仓库。thinking、reasoning effort、可选输出上限和 deadline 都由 Settings 传入正式 Provider 路径。

## 快速启动

### Docker Compose

```text
docker compose up --build
```

默认服务端口和健康检查请以 `docker-compose.yml` 及部署文档为准。

### 本地后端

```text
cd app
uvicorn main:app --reload
```

### 本地前端

```text
cd frontend
npm install
npm run dev
```

## 验证

后端测试和静态检查：

```text
pytest
ruff check app tests
mypy app
```

前端检查：

```text
cd frontend
npm run typecheck
npm run test
npm run lint
```

## 仓库结构

- `app/agent/`：Planner、Provider contract、PlanningContext 和 planning lifecycle。
- `app/services/`：Generic game、Knowledge projection、Formal PLAY 和 persistence orchestration。
- `app/engine/`：Runtime rule engine、locality 和 action execution。
- `app/domain/`：Scenario 与公开领域模型。
- `frontend/`：Player UI 和 PLAY query/mutation flows。
- `tests/`：后端 unit/integration/regression tests。
- `docs/`：当前架构、PlannerInput V2、场景编写和历史归档。

Journey Agent 的目标是让新的场景通过公开数据和 contract 驱动运行时，而不是在 runtime 中增加 scenario-specific 分支。
