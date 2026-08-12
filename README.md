# Journey Agent：战略军令系统

这是一个可审计、有状态的游戏 Agent 后端 Demo。玩家以“主公”身份下达高层军令；军师沈策生成并调整方案，武将韩烈和内政官陆宁执行各自职责内的步骤；确定性后端负责权限校验、资源扣除、世界事件结算和状态持久化。

当前仓库内置并持久化了第一个正式场景 `starfire_command`：修复星火前哨，并重新打通北方商路。场景系统已经支持 Draft、校验、发布、不可变版本和多 GameInstance；Scenario Editor、通用创作 API 和多实例可视化管理界面尚未实现。旧版西游、通用 NPC、Quest 和 Encounter 工作流已经移除。

## 已实现能力

- 自然语言军令 → Task → Plan → 多角色 Step → Tool Calling
- 一个 Model Provider 配合不同 Officer Profile、Role、Doctrine、Memory 和 Tool 权限模拟不同部下
- 参数级 Authority Policy：低风险授权内行为自动执行，高成本行为请求玩家审批
- `REQUIRES_PLAYER_DECISION`、`WAITING_FOR_PLAYER_ACTION`、`WAITING_FOR_WORLD_EVENT` 三类暂停语义
- Task、Plan、Step、审批、世界行动和执行 Trace 的数据库持久化
- 行动失败或已验证世界状态变化后的安全 Replanning
- GameService 确定性结算兵力、粮草、金币、士气、前哨和商路状态
- World Truth 与玩家/Agent Knowledge 分层持久化；隐藏节点和事实不会进入模型上下文
- ToolExecutor 对参数、角色、任命关系、权限、业务规则、事务、幂等和 Before/After 状态进行审计
- 完整 Scenario Definition 持久化：Node、Fact、Relation、Interaction、Resource、Objective 和 Behavior Binding
- `ScenarioDraft → Validate → Publish → immutable ScenarioVersion` 创作与发布生命周期
- Published Version 保存 canonical snapshot、content hash、schema version 和精确 BehaviorBundle version
- `Player → GameInstance → ScenarioVersion` 运行时 ownership；同一玩家可以同时运行多局或多个版本
- Truth、Knowledge、Access、Resources、Task、Session、Memory、Decision 和 WorldOperation 全部按 GameInstance 隔离
- 从明确 ScenarioVersion 单事务、幂等地初始化 Runtime；缺失 Instance/Version binding 时 fail closed
- fresh database、legacy database backfill、数据库重启恢复、多实例与版本隔离验证
- 明亮中文 Strategic Command Console 调试页面

这不是三个并行运行的独立 LLM Agent。当前实现是一个模型入口，在每次规划或执行时动态注入相应角色上下文。GameService 也不是 Agent，而是无人格、无自主目标的确定性规则裁判。

## 核心流程

```text
Scenario Draft → Validate → Publish immutable ScenarioVersion
  → Player 选择明确 ScenarioVersion 创建 GameInstance
  → 单事务初始化 Truth / Knowledge / Access / Resources / Session
  → 玩家下达军令
  → Planner + ScenarioPlanningPolicy 基于 Known World 生成 Plan
  → PlanValidator 校验 Schema、角色、权限、动态目标和步骤契约
  → TaskOrchestrator 按 Step 切换沈策 / 韩烈 / 陆宁
  → ToolExecutor 重新校验参数、授权、当前 Knowledge / Access 与业务前置条件
  → GameService + StarfireRuleset 确定性结算并更新 Truth / Knowledge
  → 失败或执行时状态变化触发 Replan
  → ScenarioObjectiveEvaluator 基于 Truth 判断目标是否完成
  → 沈策汇报结果
```

Mock 模式使用固定、可复现的模型输出，适合回归测试。DeepSeek 模式会真实生成 Plan 和 Replan，结果具有非确定性，但必须通过同一套 Schema、权限、状态和安全校验后才能执行。

世界规则和 Objective 读取完整 Truth；Planner、Replan 和执行角色只读取 Known World。调试页默认展示玩家视图，开发者可显式打开只读 Observer 投影查看 Truth、Knowledge 与 Access，不能通过该视图修改世界。

每个 Runtime 必须从 `game_instance_id` 解析固定的 `scenario_version_id`。系统不会通过“当前发布版本”或 `scenario_key` 推断 Runtime 版本，也不会把 `player_id` 单独视为一局游戏。已运行的 v1 GameInstance 不会因为 Draft 修改或 v2 发布而漂移。

## Scenario 与 Runtime ownership

```text
Player（身份 / 长期账号信息）
  └─ GameInstance（一局游戏，Runtime 唯一 scope root）
       ├─ ScenarioVersion（不可变 Definition + BehaviorBundle binding）
       ├─ Truth / Knowledge / Access / Resources / Location
       ├─ ConversationSession / AgentTask / Plan / Step
       └─ Memory / Decision / WorldOperation / Tool execution graph
```

- `ScenarioDraft` 可修改，不参与 Runtime。
- 每次有实际内容变化的 Publish 生成新的不可变 `ScenarioVersion`；无变化不会制造垃圾版本。
- Objective definitions、requirements、prerequisites 和 subsumption 只来自 version snapshot。
- BehaviorBundle 只提供精确版本的 executable policy/evaluator，不持有第二份 Objective Catalog。
- GameInstance 一旦建立，Player 和 ScenarioVersion binding 均不可修改。
- Message、Run、Plan、Step 和 ToolExecution 通过 Session/Task 父链继承 Instance ownership，不重复保存无意义的 scope 字段。

## Starfire 场景边界

通用 Agent 主流程从 GameInstance 加载精确的 persisted ScenarioVersion snapshot，再与同版本 BehaviorBundle executable implementation 组合；Planner、TaskOrchestrator 或 ToolExecutor 不维护另一份 Starfire Objective 内容：

- `definition.py`：纯 World Definition，声明 Node、Fact、Relation、Resource、Interaction 及初始 Visibility / Access。
- `planning_policy.py`：规划阶段允许的工具、顺序约束、动态目标和恢复指导。
- `ruleset.py`：无数据库写入的确定性前置条件与结算结果。
- `app/scenarios/starfire/scenario.py`：组合完整 Scenario Definition 与 BehaviorBundle reference。
- `app/scenarios/documents.py` / `serialization.py`：持久化文档 schema、canonical serialization 和 content hash。
- `app/scenarios/persistence.py` / `app/services/scenarios.py`：Draft persistence、校验和原子 Publish。
- `app/scenarios/versions.py`：仅按明确 ID 加载并校验 immutable ScenarioVersion；没有 latest lookup。
- `app/scenarios/runtime_binding.py`：组合 versioned snapshot 与 exact executable behavior。
- `app/scenarios/starfire/objectives.py`：BehaviorBundle 中的 executable evaluator；Objective definitions 来自 snapshot。
- `app/scenarios/starfire/compatibility.py`：旧节点名、旧参数和旧 flat facts 的有限兼容层；新 Plan 只使用 canonical target。
- `app/services/runtime_initialization.py`：从明确 ScenarioVersion 幂等创建完整 GameInstance Runtime。
- `app/services/runtime_recovery.py`：从 `game_instance_id` 恢复并验证完整 Runtime graph。

静态 Registry 只保留 pre-C8 数据库兼容和内置 executable behavior 组合用途；C8 Runtime 不依赖静态 `STARFIRE_WORLD` 或 current/latest version 查找 Definition。

## 技术栈

- Python 3.12、FastAPI
- SQLAlchemy 2、Alembic
- SQLite（默认本地开发）与 PostgreSQL 16（Docker Compose）
- OpenAI-compatible tool calling（已使用 DeepSeek 验证）
- Pytest、Ruff、Mypy
- 原生 HTML / CSS / JavaScript 调试控制台

## 快速启动

### Docker Compose

```powershell
$env:POSTGRES_PASSWORD = Read-Host "PostgreSQL password"
docker compose up --build
```

打开：

- 战略军令控制台：<http://127.0.0.1:8000/debug>
- Swagger：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- 数据库就绪检查：<http://127.0.0.1:8000/ready>

### 本地开发

```powershell
uv sync --python 3.12 --extra dev
Copy-Item .env.example .env
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`.env.example` 默认使用仓库根目录下的 `journey_dev.db`，无需安装或配置本地 PostgreSQL；Docker Compose 会自动覆盖为 PostgreSQL 连接。

Alembic head 为 `c80000000001`。全新数据库会建立完整 Scenario/GameInstance schema；Phase B legacy database 可以直接升级：迁移会按 canonical content hash 建立 Starfire ScenarioVersion，为旧 Player 创建确定性的 `legacy-default` GameInstance，回填 Runtime graph、验证 ownership 后收紧约束。重要数据仍建议在升级前备份。

## 调试页面测试流程

1. 打开 `/debug`，点击“重置星火前哨场景”。
2. 保持默认军令，点击“下达军令”。
3. 页面会自动执行，直到等待游戏规则服务结算。
4. 点击“结算世界事件”。第一次清剿会确定性失败并暴露敌军补给线。
5. 系统生成 Replan，随后由不同部下继续执行。
6. 出现高成本资源请求时，由主公选择批准或拒绝。
7. 继续结算后续世界事件，直到任务完成。
8. 在方案历史、行动汇报和开发者审计中检查 Plan 版本、角色切换、Tool Trace 与 Before/After 状态。

每次“重置”现在都会创建一个新的 GameInstance，并返回明确的 `game_instance_id`、`scenario_version_id` 和 `session_id`。页面当前只在 localStorage 中保存一个活动 Session；旧 Instance 不会被删除，仍可通过对应 Session API 恢复。多 Instance 列表、切换器和并排视图属于后续 UI 阶段。

### 手动验证多实例隔离

在服务运行时，可以用两个 Debug Runtime 快速验证：

```powershell
$A = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/debug/strategic/reset" `
  -ContentType "application/json" -Body "{}"
$B = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/debug/strategic/reset" `
  -ContentType "application/json" -Body "{}"

$A | Select-Object game_instance_id, scenario_version_id, session_id
$B | Select-Object game_instance_id, scenario_version_id, session_id
```

预期两者 `game_instance_id` 和 `session_id` 不同，但可以绑定同一个 `scenario_version_id`。只向 A 下达军令后，A 会出现 Task/Plan/WorldOperation，B 的 Task 仍为 `null`，资源与 Knowledge 也不会变化。完整隔离与重启恢复测试见：

```powershell
uv run pytest tests/integration/test_runtime_isolation_recovery.py -v
uv run pytest tests/integration/test_task_scope_migration.py -v
```

## 使用 DeepSeek

不要把真实密钥写入 `.env.example` 或提交到 Git。仅在本地环境变量或私有 `.env` 中配置：

```powershell
$env:MODEL_PROVIDER = "openai_compatible"
$env:MODEL_BASE_URL = "https://api.deepseek.com"
$env:MODEL_NAME = "deepseek-v4-flash"
$env:MODEL_API_KEY = "你的本地密钥"
$env:MODEL_TIMEOUT_SECONDS = "60"
```

启动服务后，页面右上角会显示当前 Provider 和模型。真实模型只负责提出结构化方案和 Tool Call；它不能直接访问数据库，也不能直接修改世界状态。

真实模型端到端评测：

```powershell
uv run journey-agent eval real-strategic --attempts 1 --output eval-results-real-strategic
uv run journey-agent eval real-strategic --attempts 1 --profile restore-only `
  --output eval-results-real-strategic-restore
```

真实 Provider 评测会把测试所需的 Goal、Known World、Objective Scope、Officer/Tool constraints、Relations 和 execution/replan context 发送到配置的模型端点。审计层禁止发送 API Key、credentials 和初始隐藏 Truth；运行前仍应确认所使用端点的数据政策与成本。

## 验证

```powershell
uv run ruff check app evals tests
uv run ruff format --check app evals tests
uv run mypy app evals
uv run pytest
uv run journey-agent eval run --output eval-results
Get-Content -Encoding UTF8 -Raw app/web/app.js | node --input-type=module --check
Get-Content -Encoding UTF8 -Raw app/web/api.js | node --input-type=module --check
Get-Content -Encoding UTF8 -Raw app/web/polling.js | node --input-type=module --check
Get-Content -Encoding UTF8 -Raw app/web/render.js | node --input-type=module --check
```

当前战略专用测试基线：

- `270 passed` 的完整 Pytest 自动化回归套件（单元、契约与集成测试）
- 14 个 Mock Evaluation 场景
- DeepSeek restore-only Real LLM E2E 已验证 versioned GameInstance 下的 Planning、世界结算、失败知识、补给线 countermeasure、Replanning 与 backend scoped objective completion

Mock 评测是确定性的回归基线；DeepSeek 评测是有成本且非确定性的真实模型测试，二者不能用同一通过率含义解释。

## 目录结构

```text
app/
  agent/          规划、角色权限、Provider、TaskOrchestrator
  api/            健康检查
  debug/          Strategic Command Console API 与快照聚合
  domain/         World、ScenarioVersion、RuntimeScope 等 Domain contract
  infrastructure/ 数据库与日志
  scenarios/      持久化文档、版本装载、Runtime binding、Starfire Behavior
  services/       Scenario lifecycle、GameInstance、初始化、恢复、Game/Task 服务
  tools/          Tool catalog、handler、executor、registry
  web/            当前中文调试页面
docs/             当前战略部下架构说明
evals/            Mock 与真实 DeepSeek 战略评测
migrations/       Phase B legacy → ScenarioVersion/GameInstance 的可升级迁移链
tests/            战略专用自动化测试
```

## 安全边界

- 模型只能调用注册表中暴露的 Tool。
- Officer 必须已被玩家任命，并且角色、Tool 权限和参数级 Authority 全部通过。
- 高成本动作的批准只对冻结的角色、Tool 和参数有效，不能被复用为其他操作。
- 世界事件结果由 GameService 生成，客户端不能提交胜负结果。
- 可恢复的业务失败可以触发 Replan；权限和身份类安全失败直接阻断。
- 每次 Tool 执行均记录参数、授权结论、业务校验、结果、耗时和 Before/After 状态。
- Runtime 必须绑定明确的 GameInstance 和 ScenarioVersion；缺失或 ownership 不一致时 fail closed。
- Operation idempotency 以 GameInstance 为边界，同一 key 可以安全地出现在不同 Instance。
- Published ScenarioVersion 在 ORM 和数据库层均拒绝更新或删除。
- Draft 修改与新版本发布不会影响正在运行的旧 GameInstance。

详细设计见 [战略部下架构](docs/strategic-officer-architecture.md)、[Runtime Scope Contract](docs/phase-c-runtime-scope-contract.md)、[C5 Runtime Migration Boundary](docs/phase-c5-runtime-migration-boundary.md) 和 [C8 Migration/Hardening](docs/phase-c8-migration-and-runtime-hardening.md)。

## License

MIT
