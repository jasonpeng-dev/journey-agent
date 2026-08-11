# 战略部下 Agent 架构

## 项目定位

Journey Agent 的主 Demo 现在定位为：

> 玩家是主公；Agent 是受任命、具有明确职责和有限权限的部下。玩家下达高层命令，军师制定方案，武将和内政官执行被分配的 Step，确定性后端负责规则与世界结算。

这不是“NPC 默认接管玩家的整个游戏目标”，也不是让一个 LLM 同时充当角色、任务系统、战斗裁判和奖励系统。

旧的 `starfire_outpost` NPC 工作流继续保留为回归场景；新的主场景是
`starfire_command`。

## 两类运行主体

### Officer Agent

Officer Agent 负责主观判断：

- 理解命令；
- 根据 Role、Personality、Doctrine、Authority 和 Memory 提出 Plan；
- 将 Step 分配给合适的部下；
- 在授权范围内选择并调用 Tool；
- 遇到可恢复失败时 Replan；
- 核验最终结果并向玩家汇报。

第一版没有运行三个独立模型。所有 Officer 共用同一个 Model Provider，但每次运行都会加载实际执行者的：

- Persona；
- Role；
- Doctrine；
- Authority limits；
- Tool permissions；
- 与当前玩家相关的 Memory。

因此它是“一个模型，多套角色运行上下文”，而不是三个并行自治进程。

### GameService

`GameService` 不是 Agent。它没有 Persona、Doctrine、Memory、Plan 或自主目标，也不调用 LLM。

它是确定性的领域规则与世界裁判，负责：

- 检查资源和世界前置条件；
- 原子扣除或释放粮食、金币和兵力；
- 创建并结算 WorldOperation；
- 计算胜负、伤亡和士气；
- 写入公开世界 Facts；
- 解锁节点；
- 拒绝不符合规则的动作；
- 保证结算幂等。

Officer 只能提出“做什么”，不能提交胜负、伤亡、奖励或世界状态补丁。结算结果只能由 `GameService` 根据当前可信状态产生。

## Plan 的归属与角色切换

一个 Plan 只有一个提出者，但可以有多个执行者。

在 Starfire 战略场景中：

- `AgentTask.owner_npc_id`：沈策，表示军令负责人；
- `ConversationSession.npc_id`：始终是沈策，表示玩家通过军师会话下令和恢复任务；
- `AgentPlan.created_by_npc_id`：沈策，表示 Plan 作者；
- `AgentStep.assigned_npc_id`：每个 Step 的实际责任人；
- `AgentRun.actor_npc_id`：这一次运行真正采用的 Officer Profile；
- `ToolContext.npc_id`：实际执行该 Step 的 Officer，而不是 Session NPC。

执行 Step 时不会把 Session 从沈策切换成韩烈或陆宁。Orchestrator 读取
`assigned_npc_id`，内部切换 Officer Profile、Prompt、Tool 权限和 Authority
策略，再记录实际 actor。

```text
玩家 ──沈策 Command Session──> Task
                              │
                              └── Plan vN（沈策提出）
                                  ├── Step 1 → 沈策
                                  ├── Step 2 → 韩烈
                                  ├── Step 3 → 韩烈
                                  ├── Step 4 → 陆宁
                                  └── Step 5 → 沈策核验并汇报
```

这样既保留一个清晰的军令负责人，也不会把每名部下伪装成独立的玩家会话。

## 三名部下的边界

| Officer | Role | 主要职责 | 可用战略 Tool | 第一版自主权限 |
|---|---|---|---|---|
| 沈策 | STRATEGIST | 分析、Plan、Replan、协调、最终核验 | `inspect_command_state`、`create_task_plan`、`replan_task` | 不直接发动军事、建设或贸易行动 |
| 韩烈 | GENERAL | 侦察、调兵、军事行动 | `inspect_command_state`、`start_recon_operation`、`start_military_operation` | 最多自主调动 200 人；激进行动需要请示 |
| 陆宁 | STEWARD | 粮草、村落支持、建设、商路 | `inspect_command_state`、`negotiate_village_support`、`start_outpost_repair`、`start_trade_route_test` | 最多自主使用 30 粮、40 金；完整重建需要请示 |

Role 和 Tool permissions 是能力边界；Authority 是“有能力后能否自主决定该参数规模”的边界。两层都由后端执行器检查。

角色 Profile 保存默认额度，玩家与该角色之间的 `OfficerAppointment` 可以保存
当前任命下的额度覆盖；两者合并为实际 Authority Policy。规划上下文、Plan
校验、玩家状态 API、AgentRun 和 ToolExecutor 使用同一份有效权限与任命版本。
执行器在真正行动前会加锁并刷新 NPC、Step 和 Appointment，因而撤销任命不会
被旧 Session 缓存绕过。未知、负数、类型错误或超过 V1 上限的策略会 fail-closed，
不会交给模型自行解释。

Doctrine 当前会影响模型提出方案的倾向，但不是唯一的安全裁判。第一版确定性
Authority Policy 硬性检查：

- 调兵、粮食和金币额度；
- `AGGRESSIVE` 侦察或军事策略；
- `FULL` 级别重建。

超限或高风险动作不会执行，也不会先改变世界状态，而是创建一个冻结了
Officer、Step、Tool 和完整参数的 Decision Request。

## 三种等待语义

| 状态 | 含义 | 谁使它恢复 |
|---|---|---|
| `REQUIRES_PLAYER_DECISION` | 部下提出超限或高风险的精确行动，请玩家批准或拒绝 | 玩家选择后，TaskService 只改变审批/Step 状态；批准动作仍须由 ToolExecutor 再执行 |
| `WAITING_FOR_PLAYER_ACTION` | 玩家必须在普通游戏系统中亲自完成某个动作 | 后端验证指定公开 Fact 后恢复；不能只靠客户端口头声明 |
| `WAITING_FOR_WORLD_EVENT` | 部下已启动行动，等待战斗、建设或贸易结算 | GameService/内部事件系统结算对应的精确 Operation |

旧 `WAITING_FOR_USER` 只服务旧 Starfire Encounter 回归流程，不再作为新设计的通用等待状态。

## Tool 与 GameService 的边界

新增 Tool 都是原子“行动请求”，不是剧情大按钮：

- `inspect_command_state`
- `start_recon_operation`
- `start_military_operation`
- `negotiate_village_support`
- `start_outpost_repair`
- `start_trade_route_test`

以下内容不是 Agent Tool 参数：

- 战斗结果；
- 伤亡和士气变化；
- Operation 是否成功；
- 节点是否解锁；
- 驿站和商路最终状态；
- 奖励或任意世界 Fact Patch。

异步 Tool 只返回 `operation_id` 和 `PENDING`。每个 Operation-start Step
必须紧接一个引用它的 World wait Step，防止一个 Plan 同时留下多个无法区分的
Pending Operation。恢复时按 source Step 的精确 `operation_id` 匹配，不使用
“最新事件”猜测。

## Starfire 战略示例

玩家命令：

> 恢复星火驿站，并重新开放北方商路。

Plan v1：

1. 沈策检查公开情报和资源；
2. 韩烈以 60 人谨慎侦察；
3. GameService 结算为部分成功；
4. 韩烈以 180 人尝试清理山谷；
5. GameService 因敌方补给线尚未切断而判定失败，并揭示补给线；
6. 当前 Plan 失败，未执行的修复和贸易 Step 被跳过。

Plan v2：

1. 沈策依据新情报制定替代方案；
2. 陆宁拟用 35 粮换取村落向导，超过 30 粮自主额度；
3. Task 进入 `REQUIRES_PLAYER_DECISION`；
4. 玩家批准后，ToolExecutor 只消费这一个精确 Approval；
5. 韩烈先切断补给，再清理山谷；
6. GameService 分别结算行动、伤亡和士气；
7. 陆宁启动驿站修复并等待建设结算；
8. 陆宁启动商路测试并等待贸易结算；
9. 沈策读取最终可信状态，核验并汇报结果。

最终成功不是由模型文字宣称，而是由后端同时验证：

- `valley_security = SAFE`
- `starfire_outpost_status = OPERATIONAL | RESTORED`
- `northern_trade_route_status = OPEN`

## 审计与恢复

Trace 同时记录：

- 军令 Task；
- Plan 作者、版本、Replan 原因和继承关系；
- 每个 Step 的 assignee、意图、约束和允许 Tool；
- 每次 AgentRun 的实际 actor、Profile 版本和 Authority Policy 版本；
- Authority 的 `ALLOW / REQUIRE_PLAYER_DECISION / DENY`；
- Tool 的参数、Before/After 状态和结果；
- Decision 的冻结动作、选择和消费时间；
- WorldOperation 的发起者、参数和确定性结算结果。

历史 0003 数据升级到 0004 时，会按旧 Task owner、Session NPC 和创建 Run
回填 Step assignee、Run actor 和 Plan author，避免旧 Trace 失去归属。

## 第一版明确限制

- 尚未实现通用“自动派单中心”。`starfire_command` 要求玩家通过已任命军师的 Session 下令；非 STRATEGIST 会收到 `COMMAND_OWNER_INVALID`。
- 尚未证明真实模型在开放世界中总能自主生成高质量 Plan。Mock Provider 使用稳定模板；Provider 模式已证明结构化生成、逐角色校验和执行链路。
- Doctrine/Personality 已进入 Profile、Prompt 和 Trace；V1 的确定性审批规则只覆盖已编码的资源额度和高风险动作类型。
- `WAITING_FOR_PLAYER_ACTION` 已有基于可信 Fact 的状态机，但主 Starfire 流程没有刻意插入玩家操作。
- Step 使用原子 `PENDING -> IN_PROGRESS` 认领来阻止重复执行，但尚未实现进程崩溃后的 lease/超时接管；也尚未加入 PostgreSQL 双 Session 并发压力测试。
- Authority override 当前没有公开修改 API；未来若支持动态授权，必须通过领域服务校验，并与 Appointment version 在同一事务中递增。
- Plan Run 记录方案作者的 Profile/Authority 版本；各执行 Run 和 Tool Trace 记录实际执行者版本，但尚未持久化一份包含所有协作 Officer 完整规划输入的不可变快照。
- API 当前只有领域对象归属校验，没有生产级登录身份与 `principal -> player` 绑定。Debug World Event resolver 仅在 development/test 可用，生产环境应由内部事件总线调用 GameService。
- 资源和经济模型是演示级确定性账本，不是完整战略经济模拟。

## 关键 HTTP 流程

```text
POST /api/v1/debug/strategic/reset
POST /api/v1/debug/strategic/commands

统一读取：
GET /api/v1/debug/strategic/snapshot?session_id=...

若 REQUIRES_PLAYER_DECISION：
POST /api/v1/debug/strategic/tasks/{task_id}/decisions/{decision_id}/resolve

若 WAITING_FOR_WORLD_EVENT（仅 Debug）：
POST /api/v1/debug/strategic/tasks/{task_id}/world-events/{operation_id}/resolve

底层回归与审计 API 继续保留：
POST /api/v1/tasks/{id}/advance
GET /api/v1/tasks/{id}
GET /api/v1/tasks/{id}/trace
GET /api/v1/players/{player_id}/state
```

Strategic facade 会在每次写操作后驱动现有 TaskOrchestrator，直到明确的
Decision、Player Action、World Event 或终态。它不接受客户端提交 outcome。
重复结算必须使用同一个幂等键；
同一幂等键不能绑定到不同 Tool、参数或 WorldOperation。
