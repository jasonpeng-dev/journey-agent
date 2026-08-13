# Journey Agent — Generic Game Engine

Journey Agent 是一个以不可变 ScenarioVersion 驱动的、可审计的游戏 Agent 后端。

当前架构遵循一条核心原则：

> Source code defines the generic engine; ScenarioVersion defines the game.

Python 只实现通用的规则解释、状态应用、Action 生命周期、Agent 规划/重规划、事务与
RuntimeScope 安全。地图、人物、角色、权限、Actions、Rules、Objectives、初始状态和数值
全部属于持久化的 `ScenarioDefinition v2`。

## 当前架构

```text
ScenarioDraft
  -> Validate
  -> immutable ScenarioVersion (schema v2)
  -> GameInstance
  -> Instance-owned Runtime
     Truth / Knowledge / Access / Resources / Actors
     Session / Task / Plan / Step / WorldOperation / MemoryEvent
```

- `RuntimeScope = game_instance_id + player_id + scenario_version_id`。
- Runtime 只按明确的 ScenarioVersion ID 加载定义，绝不回退到 latest。
- GameInstance 的 Player/Version binding 建立后不可漂移。
- Rule Engine 是 deterministic、纯计算的解释器，不访问数据库，也不执行动态脚本。
- `GenericGameService` 负责把 RuleOutcome 原子应用到一个 GameInstance。
- 所有 Scenario Action 都走同一个 `execute_action` 通用入口；新增 Action 不需要新增 Python handler。
- Planner 只读取当前 Instance 的 Knowledge；ObjectiveEvaluator 使用 exact Version 的 Objective snapshot。

## 内置验证 Scenario

- `starfire_command`：战略地图、侦察、异步战斗、补给破袭、修复与贸易。
- `medical_emergency`：诊所、医生/护士、诊断前置条件、药品消耗与病人稳定。

二者均为普通 YAML `ScenarioDefinition v2` 数据，由同一 Generic Engine 同时运行；没有各自的
Ruleset、Tool handler、GoalResolver、PlanningPolicy 或 gameplay Python。

## Legacy cutover

Phase R 已完成 v2-only cutover。应用层不再支持 v1 ScenarioVersion 或 v1 Runtime，并已移除：

- `STARFIRE_WORLD` / `StarfireRuleset`；
- Starfire 专用 planning、fallback、goal resolution 与 objective catalog；
- 专用 Tool catalog/handler/executor 和旧 `GameService`；
- NPC seed、player-scoped runtime projection 与 legacy debug runtime API；
- v1 document decoder 与 BehaviorBundle runtime binding。

Alembic head 为 `r80000000001`。升级到该版本会删除不能满足 v2 Actor/Version ownership 的旧
Runtime graph；生产数据库升级前必须备份。

## 启动

```powershell
uv sync --python 3.12 --extra dev
Copy-Item .env.example .env
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

可用入口：

- Swagger：<http://127.0.0.1:8000/docs>
- Health：<http://127.0.0.1:8000/health>
- Readiness：<http://127.0.0.1:8000/ready>

Scenario Editor 与新的 Instance-aware 前端尚未实现，将在 Phase D 开始。

## 验证

```powershell
uv run pytest
uv run ruff check app evals tests
uv run ruff format --check app evals tests
uv run mypy app evals
uv run alembic upgrade head
```

关键 E2E：

```powershell
uv run pytest tests/integration/test_starfire_v2_generic_runtime.py -v
uv run pytest tests/integration/test_dual_scenario_v2_e2e.py -v
```

Dual-Scenario E2E 在同一数据库、同一 Player 下创建 Starfire 与 Medical 两个 Instance，验证：

- Goal → Plan → Action → Rule → World State → Knowledge/Replan → Objective Completion；
- Instance 和资源状态隔离；
- exact-version binding；
- 数据库重启后的 Actor、Task、Memory 与 Objective recovery。

## 目录

```text
app/domain/       RuntimeScope 与 ScenarioDefinition v2 contracts
app/engine/       deterministic declarative Rule Engine
app/scenarios/    v2 persistence、validation、version loading 与内置 YAML 数据
app/services/     Scenario lifecycle、GameInstance、初始化、恢复、Action/Outcome 应用
app/agent/        generic goal resolution、planning、validation、replan、evaluation
app/tools/        单一通用 execute_action 边界
migrations/       数据库演进与 v2 cutover
tests/            generic engine、Starfire v2 与 dual-scenario E2E
```

## License

MIT
