"""Authoring-time completeness guidance over a mutable Scenario working copy.

This is intentionally separate from publish validation.  It consumes the raw
working document, reuses the publish validator when it can, and reports
contextual next steps without requiring the whole document to parse first.
The editor therefore remains useful while an author is repairing an invalid
draft.
"""

# Chinese guidance intentionally uses full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.domain.scenario_v2 import normalize_resource_source_hint_document
from app.scenarios.authoring import ReferenceEdge, reference_index
from app.scenarios.validation import ScenarioDefinitionValidator, ScenarioValidationIssue

CompletenessLevel = Literal[
    "COMPLETE",
    "INCOMPLETE_REQUIRED",
    "VALID_BUT_UNCONFIGURED",
    "OPTIONAL_ENHANCEMENT",
    "LEGACY_FALLBACK",
]
DependencyKind = Literal[
    "HARD_REQUIRED",
    "PUBLISH_REQUIRED",
    "RUNTIME_REQUIRED",
    "SEMANTIC_REQUIRED",
    "RECOMMENDED",
    "OPTIONAL",
    "DERIVED",
    "LEGACY",
    "NONE",
]


@dataclass(frozen=True, slots=True)
class CompletenessItem:
    key: str
    title: str
    level: CompletenessLevel
    dependency_kind: DependencyKind
    message: str
    path: str
    locator: dict[str, str | None] | None = None
    action: Literal["OPEN", "CREATE", "CONFIGURE", "NONE"] = "OPEN"
    reference_locator: dict[str, str | None] | None = None
    reference_owner: str | None = None


@dataclass(frozen=True, slots=True)
class CompletenessResult:
    items: tuple[CompletenessItem, ...]
    validation_issue_count: int
    reference_edge_count: int
    validation_issues: tuple[ScenarioValidationIssue, ...] = ()

    @property
    def required_missing(self) -> int:
        return sum(item.level == "INCOMPLETE_REQUIRED" for item in self.items)

    @property
    def recommended_missing(self) -> int:
        return sum(item.level == "OPTIONAL_ENHANCEMENT" for item in self.items)


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _objects(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _locator(object_kind: str, object_key: object, field_path: str | None) -> dict[str, str | None]:
    return {
        "object_kind": object_kind,
        "object_key": object_key if isinstance(object_key, str) and object_key else None,
        "field_path": field_path,
    }


def _add(
    items: list[CompletenessItem],
    *,
    key: str,
    title: str,
    level: CompletenessLevel,
    dependency_kind: DependencyKind,
    message: str,
    path: str,
    locator: dict[str, str | None] | None = None,
    action: Literal["OPEN", "CREATE", "CONFIGURE", "NONE"] = "OPEN",
    reference_locator: dict[str, str | None] | None = None,
    reference_owner: str | None = None,
) -> None:
    items.append(
        CompletenessItem(
            key=key,
            title=title,
            level=level,
            dependency_kind=dependency_kind,
            message=message,
            path=path,
            locator=locator,
            action=action,
            reference_locator=reference_locator,
            reference_owner=reference_owner,
        )
    )


def _target_exists(document: dict[str, Any], edge: ReferenceEdge) -> bool:
    target = edge.target
    if target.object_kind == "node":
        nodes = _objects(_object(document.get("world")).get("nodes"))
        if target.object_key is None:
            fact_key = (target.field_path or "").removeprefix("facts.")
            return any(
                isinstance(node.get("facts"), list)
                and any(
                    isinstance(fact, dict) and fact.get("key") == fact_key for fact in node["facts"]
                )
                for node in nodes
            )
        if "facts." in (target.field_path or ""):
            node = next((item for item in nodes if item.get("key") == target.object_key), None)
            return isinstance(node, dict) and any(
                isinstance(fact, dict)
                and fact.get("key") == (target.field_path or "").split("facts.")[-1]
                for fact in _objects(node.get("facts"))
            )
        return any(node.get("key") == target.object_key for node in nodes)
    collections: dict[str, tuple[str, ...]] = {
        "node_type": ("world", "node_types"),
        "relation_type": ("world", "relation_types"),
        "relation": ("world", "relations"),
        "resource": ("world", "resources"),
        "role": ("actors", "roles"),
        "actor": ("actors", "actor_profiles"),
        "interaction": ("interactions",),
        "action": ("actions",),
        "rule": ("rules",),
        "derived_state": ("derived_states",),
    }
    path = collections.get(target.object_kind)
    if path is not None:
        value: object = document
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        # Older/current generic documents may intentionally omit the optional
        # RelationType catalog and use relation_type_key as a semantic string.
        # The runtime validator treats that shape as a compatibility fallback,
        # so it is not a dangling reference for authoring readiness.
        if target.object_kind == "relation_type" and not isinstance(value, list):
            return True
        return any(
            isinstance(item, dict) and item.get("key") == target.object_key
            for item in _objects(value)
        )
    if target.object_kind == "initialization" and target.field_path:
        root = _object(document.get("initialization"))
        collection, _, identity = target.field_path.partition(".")
        if collection == "resource_pools":
            return any(
                isinstance(item, dict) and item.get("pool_key") == identity
                for item in _objects(root.get(collection))
            )
        if collection == "resource_initial_states":
            resource_key, _, scope = identity.partition(":")
            return any(
                isinstance(item, dict)
                and item.get("resource_key") == resource_key
                and (item.get("scope_node_key") or "") == scope
                for item in _objects(root.get(collection))
            )
        if collection == "region_resource_knowledge":
            return any(
                isinstance(item, dict) and item.get("region_key") == identity
                for item in _objects(root.get(collection))
            )
        return False
    if target.object_kind == "planning" and target.field_path:
        collection, _, identity = target.field_path.partition(".")
        return collection == "recovery_hints" and any(
            isinstance(item, dict) and item.get("failure_code") == identity
            for item in _objects(_object(document.get("planning")).get(collection))
        )
    nested_kinds = {
        "action_parameter",
        "action_outcome",
        "action_binding",
        "action_target_role",
        "action_target_contract",
        "action_authority_limit",
        "action_authority_approval",
        "actor_doctrine",
        "actor_authority_limit",
        "actor_authority_approval",
    }
    if target.object_kind in nested_kinds:
        # Nested locators are scoped by their owning parent key.  The
        # reference index emits the parent key as the first component.
        if not target.object_key or ":" not in target.object_key:
            return False
        parent_key, nested_key = target.object_key.split(":", 1)
        parent_kind = "actor" if target.object_kind.startswith("actor_") else "action"
        parent_values: object = (
            _object(document.get("actors")).get("actor_profiles")
            if parent_kind == "actor"
            else document.get("actions")
        )
        parent = next(
            (item for item in _objects(parent_values) if item.get("key") == parent_key),
            None,
        )
        if parent is None:
            return False
        collection_by_kind = {
            "action_parameter": "parameters",
            "action_outcome": "expected_outcomes",
            "action_binding": "operation_bindings",
            "action_target_role": "target_actor_roles",
            "action_target_contract": "target_contracts",
            "action_authority_limit": "authority_policy",
            "action_authority_approval": "authority_policy",
            "actor_doctrine": "doctrine",
            "actor_authority_limit": "authority_policy",
            "actor_authority_approval": "authority_policy",
        }
        nested_value: object = parent.get(collection_by_kind[target.object_kind])
        if target.object_kind in {
            "action_authority_limit",
            "actor_authority_limit",
            "action_authority_approval",
            "actor_authority_approval",
        }:
            nested_value = _object(nested_value).get(
                "autonomous_limits"
                if target.object_kind.endswith("limit")
                else "approval_required_values"
            )
        identity_field = "code" if target.object_kind == "action_outcome" else "key"
        if target.object_kind in {"action_binding", "action_target_role", "action_target_contract"}:
            identity_field = "role" if target.object_kind == "action_binding" else "target_key"
        return any(
            isinstance(item, dict)
            and (
                item.get(identity_field) == nested_key
                or (
                    target.object_kind == "action_target_role"
                    and f"{item.get('target_key')}:{item.get('required_actor_role_key')}"
                    == nested_key
                )
            )
            for item in _objects(nested_value)
        )
    return True


def _unhandled_runtime_failure_codes(document: dict[str, Any]) -> list[str]:
    """Return reachable, retryable runtime failures without authored guidance.

    PREFLIGHT failures are intentionally excluded.  Their conditions are
    projected into planner constraints before execution and, if state changes
    between planning and execution, the generic agent replans with the failure
    code/message plus the latest world state.  Requiring a one-to-one
    ``recovery_hints`` entry for those failures would duplicate that generic
    recovery contract.

    A retryable RESOLVE failure is different: it can occur after an action was
    legitimately selected and executed.  A matching hint is therefore useful
    author guidance, but remains an optional enhancement rather than a schema,
    publish, or runtime requirement.
    """

    actions = {
        _text(item.get("key"))
        for item in _objects(document.get("actions"))
        if _text(item.get("key"))
    }
    actors = _objects(_object(document.get("actors")).get("actor_profiles"))
    allowed_actions = {
        _text(action_key)
        for actor in actors
        for action_key in actor.get("allowed_action_keys", [])
        if _text(action_key)
    }
    hinted_codes = {
        _text(item.get("failure_code"))
        for item in _objects(_object(document.get("planning")).get("recovery_hints"))
        if _text(item.get("failure_code"))
    }
    result: set[str] = set()
    for rule in _objects(document.get("rules")):
        action_key = _text(rule.get("action_key"))
        if (
            rule.get("trigger", "ACTION") != "ACTION"
            or rule.get("phase") != "RESOLVE"
            or action_key not in actions
            or (actors and action_key not in allowed_actions)
        ):
            continue
        for effect in _objects(rule.get("effects")):
            failure_code = _text(effect.get("failure_code"))
            if (
                effect.get("kind") == "EMIT_FAILURE"
                and effect.get("retryable") is True
                and failure_code
                and failure_code not in hinted_codes
            ):
                result.add(failure_code)
    return sorted(result)


def evaluate_completeness(document: dict[str, Any]) -> CompletenessResult:
    normalized = normalize_resource_source_hint_document(document)
    if not isinstance(normalized, dict):
        raise TypeError("Scenario completeness document must be an object")
    document = normalized
    items: list[CompletenessItem] = []
    metadata = _object(document.get("metadata"))
    world = _object(document.get("world"))
    actors = _object(document.get("actors"))
    initialization = _object(document.get("initialization"))
    node_types = _objects(world.get("node_types"))
    nodes = _objects(world.get("nodes"))
    resources = _objects(world.get("resources"))
    roles = _objects(actors.get("roles"))
    actor_profiles = _objects(actors.get("actor_profiles"))
    interactions = _objects(document.get("interactions"))
    actions = _objects(document.get("actions"))
    rules = _objects(document.get("rules"))
    derived_states = _objects(document.get("derived_states"))

    if not _text(metadata.get("key")) or not _text(metadata.get("name")):
        _add(
            items,
            key="metadata.identity",
            title="完成场景基础定义",
            level="INCOMPLETE_REQUIRED",
            dependency_kind="HARD_REQUIRED",
            message="场景稳定键和显示名称是继续配置与保存的基础。",
            path="metadata",
            locator=_locator("metadata", None, ""),
            action="CONFIGURE",
        )
    else:
        _add(
            items,
            key="metadata.identity",
            title="场景基础定义",
            level="COMPLETE",
            dependency_kind="NONE",
            message="场景稳定键和显示名称已配置。",
            path="metadata",
            locator=_locator("metadata", None, None),
            action="OPEN",
        )

    node_type_keys = {_text(item.get("key")) for item in node_types}
    for node in nodes:
        node_key = _text(node.get("key"))
        node_type_key = _text(node.get("node_type_key"))
        if not node_type_key or node_type_key not in node_type_keys:
            _add(
                items,
                key=f"node:{node_key}:node-type",
                title=f"配置节点「{_text(node.get('name')) or node_key}」的节点类型",
                level="INCOMPLETE_REQUIRED",
                dependency_kind="HARD_REQUIRED",
                message="节点必须关联一个现有节点类型。",
                path=f"world.nodes.{node_key}.node_type_key",
                locator=_locator("node", node_key, "node_type_key"),
                action="CREATE",
            )
        else:
            _add(
                items,
                key=f"node:{node_key}:node-type",
                title=f"节点「{_text(node.get('name')) or node_key}」的节点类型",
                level="COMPLETE",
                dependency_kind="HARD_REQUIRED",
                message="节点类型已关联。",
                path=f"world.nodes.{node_key}.node_type_key",
                locator=_locator("node", node_key, "node_type_key"),
                action="OPEN",
            )

    actor_keys = {_text(item.get("key")) for item in actor_profiles}
    role_keys = {_text(item.get("key")) for item in roles}
    action_keys = {_text(item.get("key")) for item in actions}
    node_keys = {_text(item.get("key")) for item in nodes}
    interaction_keys = {_text(item.get("key")) for item in interactions}
    for actor in actor_profiles:
        actor_key = _text(actor.get("key"))
        if _text(actor.get("role_key")) not in role_keys:
            _add(
                items,
                key=f"actor:{actor_key}:role",
                title=f"配置参与者「{_text(actor.get('name')) or actor_key}」的角色",
                level="INCOMPLETE_REQUIRED",
                dependency_kind="HARD_REQUIRED",
                message="参与者需要一个现有角色才能参与行动。",
                path=f"actors.actor_profiles.{actor_key}.role_key",
                locator=_locator("actor", actor_key, "role_key"),
                action="CREATE",
            )
        if _text(actor.get("initial_node_key")) not in node_keys:
            _add(
                items,
                key=f"actor:{actor_key}:initial-node",
                title=f"配置参与者「{_text(actor.get('name')) or actor_key}」的开局位置",
                level="INCOMPLETE_REQUIRED",
                dependency_kind="RUNTIME_REQUIRED",
                message="开局位置由初始化工作区配置，必须指向现有节点。",
                path=f"actors.actor_profiles.{actor_key}.initial_node_key",
                locator=_locator("actor", actor_key, "initial_node_key"),
                action="CONFIGURE",
            )
        missing_actions = [
            key for key in actor.get("allowed_action_keys", []) if key not in action_keys
        ]
        if missing_actions:
            _add(
                items,
                key=f"actor:{actor_key}:allowed-actions",
                title=f"修复参与者「{_text(actor.get('name')) or actor_key}」的行动引用",
                level="INCOMPLETE_REQUIRED",
                dependency_kind="SEMANTIC_REQUIRED",
                message=(
                    f"存在无法解析的行动引用：{', '.join(str(item) for item in missing_actions)}。"
                ),
                path=f"actors.actor_profiles.{actor_key}.allowed_action_keys",
                locator=_locator("actor", actor_key, "allowed_action_keys"),
                action="OPEN",
            )

    for action in actions:
        action_key = _text(action.get("key"))
        interaction_key = _text(action.get("required_interaction_key"))
        if interaction_key not in interaction_keys:
            _add(
                items,
                key=f"action:{action_key}:interaction",
                title=f"为行动「{_text(action.get('name')) or action_key}」选择交互能力",
                level="INCOMPLETE_REQUIRED",
                dependency_kind="HARD_REQUIRED",
                message="行动必须关联一个现有交互能力。",
                path=f"actions.{action_key}.required_interaction_key",
                locator=_locator("action", action_key, "required_interaction_key"),
                action="CREATE",
            )
        matching_rules = [
            rule
            for rule in rules
            if _text(rule.get("action_key")) == action_key and rule.get("phase") == "RESOLVE"
        ]
        if not matching_rules:
            _add(
                items,
                key=f"action:{action_key}:resolve-rule",
                title=f"为行动「{_text(action.get('name')) or action_key}」配置结算规则",
                level="INCOMPLETE_REQUIRED",
                dependency_kind="PUBLISH_REQUIRED",
                message="可发布行动需要一个 RESOLVE 规则；可从行动上下文创建规则。",
                path=f"actions.{action_key}",
                locator=_locator("action", action_key, None),
                action="CREATE",
            )
        outcomes = _objects(action.get("expected_outcomes"))
        if not outcomes:
            _add(
                items,
                key=f"action:{action_key}:outcomes",
                title=f"为行动「{_text(action.get('name')) or action_key}」添加预期结果",
                level="INCOMPLETE_REQUIRED",
                dependency_kind="PUBLISH_REQUIRED",
                message="行动至少需要一个可识别的预期结果。",
                path=f"actions.{action_key}.expected_outcomes",
                locator=_locator("action", action_key, "expected_outcomes"),
                action="OPEN",
            )

    for rule in rules:
        rule_key = _text(rule.get("key"))
        if (
            rule.get("trigger", "ACTION") == "ACTION"
            and _text(rule.get("action_key")) not in action_keys
        ):
            _add(
                items,
                key=f"rule:{rule_key}:action",
                title=f"为规则「{_text(rule.get('name')) or rule_key}」选择行动",
                level="INCOMPLETE_REQUIRED",
                dependency_kind="HARD_REQUIRED",
                message="行动触发规则必须指向现有行动。",
                path=f"rules.{rule_key}.action_key",
                locator=_locator("rule", rule_key, "action_key"),
                action="OPEN",
            )
        if not _objects(rule.get("effects")):
            _add(
                items,
                key=f"rule:{rule_key}:effects",
                title=f"为规则「{_text(rule.get('name')) or rule_key}」添加效果",
                level="INCOMPLETE_REQUIRED",
                dependency_kind="SEMANTIC_REQUIRED",
                message="规则没有效果，运行时不会产生可观察变化。",
                path=f"rules.{rule_key}.effects",
                locator=_locator("rule", rule_key, "effects"),
                action="OPEN",
            )

    for state in derived_states:
        state_key = _text(state.get("key"))
        if not _objects(state.get("dependencies")):
            _add(
                items,
                key=f"derived:{state_key}:dependencies",
                title=f"为派生状态「{_text(state.get('name')) or state_key}」添加依赖",
                level="INCOMPLETE_REQUIRED",
                dependency_kind="DERIVED",
                message="派生状态至少需要一个事实、资源或其他派生状态依赖。",
                path=f"derived_states.{state_key}.dependencies",
                locator=_locator("derived_state", state_key, "dependencies"),
                action="OPEN",
            )

    if nodes and _text(initialization.get("start_node_key")) not in node_keys:
        _add(
            items,
            key="initialization.start-node",
            title="配置开局起始节点",
            level="INCOMPLETE_REQUIRED",
            dependency_kind="RUNTIME_REQUIRED",
            message="初始化工作区需要一个现有节点作为世界入口。",
            path="initialization.start_node_key",
            locator=_locator("initialization", None, "start_node_key"),
            action="CONFIGURE",
        )
    if actor_profiles and _text(initialization.get("primary_actor_key")) not in actor_keys:
        _add(
            items,
            key="initialization.primary-actor",
            title="配置主要参与者",
            level="INCOMPLETE_REQUIRED",
            dependency_kind="RUNTIME_REQUIRED",
            message="初始化工作区需要指定默认主要参与者；这不限制其他参与者存在。",
            path="initialization.primary_actor_key",
            locator=_locator("initialization", None, "primary_actor_key"),
            action="CONFIGURE",
        )

    initialization_pools = _objects(initialization.get("resource_pools"))
    pooled_resources = {_text(item.get("resource_key")) for item in initialization_pools}
    for resource in resources:
        resource_key = _text(resource.get("key"))
        if resource_key and not isinstance(resource.get("source_hint"), dict):
            _add(
                items,
                key=f"resource:{resource_key}:public-source",
                title=f"为资源「{_text(resource.get('name')) or resource_key}」补充公共来源知识",
                level="OPTIONAL_ENHANCEMENT",
                dependency_kind="RECOMMENDED",
                message="资源可以独立有效；来源提示用于帮助玩家发现它。",
                path=f"world.resources.{resource_key}.source_hint",
                locator=_locator("resource", resource_key, "source_hint"),
                action="CONFIGURE",
            )
        if resource_key and resource_key not in pooled_resources:
            _add(
                items,
                key=f"resource:{resource_key}:pool",
                title=f"为资源「{_text(resource.get('name')) or resource_key}」配置资源池",
                level="VALID_BUT_UNCONFIGURED",
                dependency_kind="RECOMMENDED",
                message="资源定义可以独立有效；资源池用于显式库存与数量操作。",
                path="initialization.resource_pools",
                locator=_locator("initialization", None, "resource_pools"),
                action="CREATE",
            )

    try:
        edges = reference_index(document)
    except Exception:
        edges = ()
    for failure_code in _unhandled_runtime_failure_codes(document):
        _add(
            items,
            key=f"recovery-hint:{failure_code}",
            title=f"为运行中失败「{failure_code}」补充恢复策略",
            level="OPTIONAL_ENHANCEMENT",
            dependency_kind="RECOMMENDED",
            message="该失败可能在行动结算后触发重新规划；专用提示可以补充通用恢复上下文。",
            path="planning.recovery_hints",
            locator=_locator("planning", None, f"recovery_hints.{failure_code}"),
            action="CREATE",
        )
    broken_edges = [
        edge
        for edge in edges
        if edge.target.object_kind != "planning" and not _target_exists(document, edge)
    ]
    for edge in broken_edges:
        source = edge.source
        target = edge.target
        source_identity = ":".join(
            (source.object_kind, source.object_key or "", source.field_path or "")
        )
        target_identity = ":".join(
            (target.object_kind, target.object_key or "", target.field_path or "")
        )
        _add(
            items,
            key=f"references.dangling:{source_identity}:{target_identity}",
            title="修复无法解析的引用",
            level="INCOMPLETE_REQUIRED",
            dependency_kind="SEMANTIC_REQUIRED",
            message="此字段引用的对象不存在或无法解析，请修正引用或移除它。",
            path=source.field_path or "references",
            locator={
                "object_kind": source.object_kind,
                "object_key": source.object_key,
                "field_path": source.field_path,
            },
            action="OPEN",
            reference_locator={
                "object_kind": target.object_kind,
                "object_key": target.object_key,
                "field_path": target.field_path,
            },
            reference_owner=target.object_kind,
        )

    try:
        validation = ScenarioDefinitionValidator().validate(document)
        validation_issue_count = len(validation.issues)
        validation_issues = validation.issues
    except Exception:  # authoring guidance must survive malformed drafts
        validation_issue_count = 1
        validation_issues = ()
        validation = None
        validation_issues = (
            ScenarioValidationIssue(
                code="SCENARIO_DOCUMENT_VALIDATION_UNAVAILABLE",
                path="schema_version",
                message="当前工作副本无法完成结构检查。",
            ),
        )
    if validation_issue_count and not any(
        item.key == "scenario-definition.validation" for item in items
    ):
        issue = validation_issues[0] if validation_issues else None
        _add(
            items,
            key="scenario-definition.validation",
            title="修复场景结构或发布约束",
            level="INCOMPLETE_REQUIRED",
            dependency_kind="HARD_REQUIRED",
            message=(
                issue.message
                if issue is not None
                else "当前工作副本无法完成结构检查，请检查场景结构。"
            ),
            path=issue.path if issue is not None else "schema_version",
            action="OPEN",
        )

    return CompletenessResult(
        items=tuple(items),
        validation_issue_count=validation_issue_count,
        reference_edge_count=len(edges),
        validation_issues=tuple(validation_issues),
    )


__all__ = [
    "CompletenessItem",
    "CompletenessResult",
    "evaluate_completeness",
]
