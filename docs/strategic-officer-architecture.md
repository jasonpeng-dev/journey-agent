# 战略部下 Agent 架构

## 1. 项目边界

当前系统只服务 `starfire_command` 战略经营 Demo：玩家是主公，沈策、韩烈和陆宁是已被任命且权限有限的部下。Agent 负责理解命令、提出方案、选择受限工具和汇报；确定性后端负责授权、业务规则、资源、事务和世界结算。

Plan 的所有者是统筹军师沈策，但每个 Step 都有独立的 `assigned_npc_id`。TaskOrchestrator 推进同一个 Plan 时，会按当前 Step 切换实际角色上下文，因此“一个 Plan 中角色不断切换”并不需要多个并行 Agent 进程。

## 2. 运行主体

### Officer Agent

第一版共用同一个 Model Provider。每次模型调用根据规划者或执行者动态注入：

- Role 与 Persona
- Doctrine 与风险偏好
- 当前任命关系和 Authority limits
- 该角色允许调用的 Tools
- 与当前玩家和任务相关的 Memory
- 已验证、且该角色有权看到的世界状态

模型不能访问数据库，也不能直接提交世界状态补丁。

### GameService

GameService 不是 Agent。它没有 Persona、Memory、Plan 或自主目标，也不调用模型。它负责：

- 检查资源和世界前置条件
- 原子扣除或释放兵力、粮草与金币
- 创建并结算侦察、军事、建设和商路测试
- 更新已验证世界事实与节点状态
- 对重复结算和幂等键冲突 fail closed

GameService 对世界状态提供两种明确投影：Ruleset 与 Objective 使用 authoritative Truth；Planner、Replan 和 Officer context 使用已过滤的 Knowledge。节点 Visibility、Fact Visibility 和节点 Access 分别持久化，任一层的变化都不会隐式改写另外两层。

### Scenario Binding

通用执行层通过 `ScenarioBinding` 读取场景能力。Starfire 的职责按以下边界拆分：

- World Definition：Node、Fact、Relation、Resource、Interaction、初始 Visibility 与 Access。
- Planning Policy：可规划工具、步骤契约、阶段顺序、动态目标与恢复提示。
- Ruleset：纯确定性校验和结算结果，不直接写数据库。
- Objective Evaluator：以完整 Truth 判断任务完成条件。
- Persistence Adapter：Definition 与现有 SQLAlchemy 模型之间的映射。
- Compatibility Adapter：只为历史 target、tool argument 和 flat fact 提供迁移期兼容。

`app/scenarios/registry.py` 当前仍是内置场景的静态只读注册表。这里没有 Scenario Editor、Game Instance 或动态插件机制；这些不属于本阶段范围。

### TaskOrchestrator

TaskOrchestrator 是应用层状态机，不扮演游戏角色。它负责：

- 创建并恢复 Task
- 请求模型生成 Plan 或 Replan
- 调用 PlanValidator 接纳或拒绝完整方案
- 找到下一个 Step 并装载对应 Officer 上下文
- 在真实暂停点停止推进
- 将可恢复失败路由到 Replan，将安全失败路由到 BLOCKED

## 3. 角色边界

| Officer | Role | 主要职责 | 可用执行工具 | 默认自主权限 |
|---|---|---|---|---|
| 沈策 | STRATEGIST | 分析、Planning、Replanning、最终核验 | `inspect_command_state`，以及 Plan/Replan 提交工具 | 不直接执行军事或资源动作 |
| 韩烈 | GENERAL | 侦察、清剿、断补给线 | `start_recon_operation`、`start_military_operation` | 最多自主调动 200 兵 |
| 陆宁 | STEWARD | 村落协商、前哨建设、商路测试 | `negotiate_village_support`、`start_outpost_repair`、`start_trade_route_test` | 最多自主使用 30 粮、40 金 |

Officer 必须同时满足：已任命、Role 允许、permission profile 允许、Authority policy 有效。缺少任一条件都不能执行。

## 4. Task / Plan / Step / Tool

正式调用链为：

```text
Goal
  → Planner + ScenarioPlanningPolicy
  → PlanValidator
  → TaskOrchestrator
  → ToolExecutor
  → GameService + Scenario Ruleset
  → Truth / Knowledge persistence
  → Replan（需要时）
  → Scenario Objective Evaluator
```

- `AgentTask`：玩家下达的一条长期军令。
- `AgentPlan`：沈策为当前军令提出的一版可执行方案。
- `AgentStep`：分配给具体 Officer 的最小执行或等待单元。
- `ToolExecutor`：所有 Tool 的统一参数、身份、权限、业务、事务和审计边界。
- `WorldOperation`：已发起、等待 GameService 结算的确定性世界行动。
- `PlayerDecisionRequest`：冻结 Officer、Tool、参数和策略快照的玩家审批请求。

PlanValidator 会在写入 Plan 前验证整个结构，包括：

- Schema 和步骤上限
- Officer 任命与角色工具边界
- Tool 参数和后端控制的幂等键
- Expected outcome 的可验证字段
- 世界行动与等待步骤必须相邻配对
- 关键阶段顺序和最终沈策核验
- Replan 不得重复已经验证完成的效果

## 5. 三类暂停

- `REQUIRES_PLAYER_DECISION`：动作本身可执行，但成本或风险超过 Officer 的自主授权。
- `WAITING_FOR_PLAYER_ACTION`：必须由玩家在游戏中亲自完成某项可验证行为。
- `WAITING_FOR_WORLD_EVENT`：Officer 已经发起行动，等待 GameService 结算。

暂停状态不能互相代替。批准资源动作不会直接执行 Tool；它只将被冻结的 Step 恢复为可执行，随后仍需通过 ToolExecutor 再次核验并消费审批。

## 6. 权限决策

ToolExecutor 的执行顺序为：

```text
Schema validation
  → Task / Plan / Step / actor binding
  → active appointment check
  → Role and permission profile
  → parameter-level authority
  → deterministic business preflight
  → transaction and idempotency
  → handler execution
  → expected-outcome verification
  → Before/After trace
```

典型结果：

- 韩烈调动不超过 200 兵：自动执行。
- 陆宁使用不超过 30 粮、40 金：自动执行。
- 陆宁申请 35 粮：生成主公审批。
- 非武将调用军事工具：拒绝，不允许通过 Replan 绕过。
- 任命已撤销或 Authority override 非法：拒绝并记录策略版本。

## 7. Replanning

当前确定性剧情会让第一次山谷清剿失败，并暴露敌军补给线。失败后的 Replan 应：

1. 读取当前已验证资源与世界事实；
2. 不重复已经完成的侦察；
3. 获取必要村落支持；
4. 先切断敌军补给，再重新清剿；
5. 继续未完成的前哨修复与商路测试；
6. 由沈策完成最终核验。

若模型连续提交无效恢复方案，系统可对特定可恢复失败启用 state-aware deterministic recovery fallback。Fallback 仍然必须通过同一个 PlanValidator 和 ToolExecutor，不拥有额外权限。

## 8. Context Engineering

Planning context 只包含：

- 当前 Task、Goal 和 Plan 历史
- 当前玩家已验证的资源与世界状态
- 有效任命的 Officer profiles
- 每个 Officer 的有效 Authority 与相关 Memory
- 当前允许的 Tool schema
- 固定 expected outcome 和 world-wait contract
- 当前失败码及对应恢复指导

已完成的战略阶段会从 Replan 可用工具中隐藏。动态 Tool target 也会先按当前节点 Knowledge 过滤；隐藏世界真相在构造模型 Context 前已经移除，只供开发环境的只读 Observer 投影审计。

## 9. 持久化与审计

数据库保留 Task、Plan、Step、Run、ToolExecution、WorldOperation、PlayerDecisionRequest、Conversation 和 Memory。Tool Trace 记录：

- 模型、轮次、Token 使用和终止原因
- 结构化提案与校验错误
- 实际 Officer、Profile 版本和 Authority policy 版本
- Tool 参数、授权结果、业务规则结果
- Before/After 状态、耗时、错误码和结果

这使任务可跨 Session 恢复，也能解释“谁基于什么权限做了什么、后端最终如何结算”。

## 10. 当前验证范围

- 完整 Pytest 单元、契约和集成回归套件
- 14 个确定性 Mock Evaluation 场景
- DeepSeek OpenAI-compatible API 的真实 Planning 与端到端流程验证
- Strategic Command Console 人工流程验证

真实模型输出仍是非确定性的。后端验证和确定性世界结算保证的是“无效或越权方案不会执行”，不是保证每次模型调用都能一次生成最优方案。
