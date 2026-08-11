# Journey Agent：战略军令系统

这是一个可审计、有状态的游戏 Agent 后端 Demo。玩家以“主公”身份下达高层军令；军师沈策生成并调整方案，武将韩烈和内政官陆宁执行各自职责内的步骤；确定性后端负责权限校验、资源扣除、世界事件结算和状态持久化。

当前仓库只保留 `starfire_command` 主场景：修复星火前哨，并重新打通北方商路。旧版西游、通用 NPC、Quest 和 Encounter 工作流已经移除。

## 已实现能力

- 自然语言军令 → Task → Plan → 多角色 Step → Tool Calling
- 一个 Model Provider 配合不同 Officer Profile、Role、Doctrine、Memory 和 Tool 权限模拟不同部下
- 参数级 Authority Policy：低风险授权内行为自动执行，高成本行为请求玩家审批
- `REQUIRES_PLAYER_DECISION`、`WAITING_FOR_PLAYER_ACTION`、`WAITING_FOR_WORLD_EVENT` 三类暂停语义
- Task、Plan、Step、审批、世界行动和执行 Trace 的数据库持久化
- 行动失败或已验证世界状态变化后的安全 Replanning
- GameService 确定性结算兵力、粮草、金币、士气、前哨和商路状态
- ToolExecutor 对参数、角色、任命关系、权限、业务规则、事务、幂等和 Before/After 状态进行审计
- 明亮中文 Strategic Command Console 调试页面

这不是三个并行运行的独立 LLM Agent。当前实现是一个模型入口，在每次规划或执行时动态注入相应角色上下文。GameService 也不是 Agent，而是无人格、无自主目标的确定性规则裁判。

## 核心流程

```text
玩家下达军令
  → 沈策根据已知世界状态与部下权限生成 Plan
  → 后端校验完整方案
  → 韩烈 / 陆宁按分配的 Step 调用受限 Tool
  → GameService 结算世界行动
  → 失败或前置条件变化时 Replan
  → 越权或高成本动作请求主公决策
  → 沈策核验最终世界状态并汇报
```

Mock 模式使用固定、可复现的模型输出，适合回归测试。DeepSeek 模式会真实生成 Plan 和 Replan，结果具有非确定性，但必须通过同一套 Schema、权限、状态和安全校验后才能执行。

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

这是首次公开版本的单一初始迁移。若本地还保存着清理前的旧数据库，请新建数据库或删除本地开发数据库后重新执行迁移。

## 调试页面测试流程

1. 打开 `/debug`，点击“重置星火前哨场景”。
2. 保持默认军令，点击“下达军令”。
3. 页面会自动执行，直到等待游戏规则服务结算。
4. 点击“结算世界事件”。第一次清剿会确定性失败并暴露敌军补给线。
5. 系统生成 Replan，随后由不同部下继续执行。
6. 出现高成本资源请求时，由主公选择批准或拒绝。
7. 继续结算后续世界事件，直到任务完成。
8. 在方案历史、行动汇报和开发者审计中检查 Plan 版本、角色切换、Tool Trace 与 Before/After 状态。

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
```

## 验证

```powershell
uv run ruff check .
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

- 33 个自动化测试
- 14 个 Mock Evaluation 场景
- DeepSeek 真实 API 已验证模型 Planning、失败后 Replanning、跨角色执行、审批、世界结算与最终完成链路

Mock 评测是确定性的回归基线；DeepSeek 评测是有成本且非确定性的真实模型测试，二者不能用同一通过率含义解释。

## 目录结构

```text
app/
  agent/          规划、角色权限、Provider、TaskOrchestrator
  api/            健康检查
  debug/          Strategic Command Console API 与快照聚合
  domain/         战略领域枚举
  infrastructure/ 数据库与日志
  services/       GameService、TaskService、战略种子数据
  tools/          Tool catalog、handler、executor、registry
  web/            当前中文调试页面
docs/             当前战略部下架构说明
evals/            Mock 与真实 DeepSeek 战略评测
migrations/       战略专用初始数据库迁移
tests/            战略专用自动化测试
```

## 安全边界

- 模型只能调用注册表中暴露的 Tool。
- Officer 必须已被玩家任命，并且角色、Tool 权限和参数级 Authority 全部通过。
- 高成本动作的批准只对冻结的角色、Tool 和参数有效，不能被复用为其他操作。
- 世界事件结果由 GameService 生成，客户端不能提交胜负结果。
- 可恢复的业务失败可以触发 Replan；权限和身份类安全失败直接阻断。
- 每次 Tool 执行均记录参数、授权结论、业务校验、结果、耗时和 Before/After 状态。

详细设计见 [docs/strategic-officer-architecture.md](docs/strategic-officer-architecture.md)。

## License

MIT
