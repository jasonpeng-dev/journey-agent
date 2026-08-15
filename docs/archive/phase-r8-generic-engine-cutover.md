# Phase R8 — Generic Engine Cutover

Phase R 的正式运行架构只接受 `ScenarioDefinition` schema v2。

## Frozen runtime path

```text
exact ScenarioVersion ID
  -> verified immutable v2 snapshot
  -> GameInstance-bound RuntimeScope
  -> Generic Agent / Action Service
  -> pure Declarative Rule Engine
  -> Generic RuleOutcome
  -> Instance-scoped Outcome Applicator
```

Runtime 没有 current/latest lookup、scenario-key lookup 或 player-only lookup。Actor、Role、Action、
Rule、Objective、planning metadata 和初始状态均来自 GameInstance 绑定的 exact Version。

## Dual-scenario proof

`starfire_command` 与 `medical_emergency` 是两个内容和规则不同的普通 v2 YAML Scenario。
`tests/integration/test_dual_scenario_v2_e2e.py` 在同一 Player 下同时运行两局，证明各自完成
Goal/Plan/Action/Rule/State/Replan/Objective 链路，状态互不影响，并能在数据库重启后按原 Version
恢复。

## Removed compatibility surface

- v1 document decoder/domain union；
- BehaviorBundle/Runtime Registry；
- `STARFIRE_WORLD`、`StarfireRuleset` 和专用 Objective/Planning/Fallback；
- Starfire Tool handlers、旧 GameService/TaskOrchestrator；
- NPC seed 和 player-scoped runtime projections；
- legacy strategic debug API/evaluation path。

迁移 `r80000000001` 删除 v1 runtime tables/columns，并收紧 Actor、Action 与 Instance ownership。
因为 v1 Runtime 已不再是受支持产品，缺少 v2 Actor ownership 的旧 runtime graph 会在 cutover 中
删除。升级生产数据库前必须备份。
