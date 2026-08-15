"""Pure generic authority evaluation over exact-Version policy data."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import AuthorityOutcome
from app.domain.scenario_v2 import (
    ActionDefinitionV2,
    AuthorityPolicyV2,
    ScenarioDefinitionV2,
    StrictScalar,
    normalize_action_parameters,
)
from app.infrastructure.db.models import GameInstanceActor


@dataclass(frozen=True, slots=True)
class GenericAuthorityDecision:
    outcome: AuthorityOutcome
    reason_code: str
    details: dict[str, object]


def actor_binding_matches(definition: ScenarioDefinitionV2, actor: GameInstanceActor) -> bool:
    profile = next(
        (item for item in definition.actors.actor_profiles if item.key == actor.actor_key), None
    )
    if profile is None:
        return False
    role = next((item for item in definition.actors.roles if item.key == profile.role_key), None)
    if role is None:
        return False
    return bool(
        actor.role_key == profile.role_key
        and actor.name == profile.name
        and actor.persona == profile.persona
        and actor.doctrine == {item.key: item.value for item in profile.doctrine}
        and actor.allowed_action_keys == list(profile.allowed_action_keys)
        and actor.authority_policy == profile.authority_policy.model_dump(mode="json")
        and actor.capabilities == [item.value for item in role.capabilities]
        and actor.is_primary == (actor.actor_key == definition.initialization.primary_actor_key)
    )


def evaluate_authority(
    actor: GameInstanceActor,
    action: ActionDefinitionV2,
    parameters: dict[str, StrictScalar],
) -> GenericAuthorityDecision:
    try:
        parameters = normalize_action_parameters(action, parameters)
    except ValueError:
        return _deny("ACTION_PARAMETERS_INVALID")
    if actor.status != "ACTIVE":
        return _deny("ACTOR_INACTIVE")
    if action.key not in actor.allowed_action_keys:
        return _deny("ACTION_NOT_ALLOWED")
    actor_capabilities = set(actor.capabilities)
    required = {capability.value for capability in action.allowed_actor_capabilities}
    if not required.issubset(actor_capabilities):
        return _deny("ACTOR_CAPABILITY_MISSING", required=sorted(required))

    try:
        policies = [
            AuthorityPolicyV2.model_validate(actor.authority_policy),
            action.authority_policy,
        ]
    except ValueError:
        return _deny("AUTHORITY_POLICY_INVALID")
    approval_reasons: list[dict[str, object]] = []
    for policy in policies:
        for limit in policy.autonomous_limits:
            value = parameters.get(limit.parameter_key)
            if limit.parameter_key not in parameters:
                continue
            if not isinstance(value, int) or isinstance(value, bool):
                return _deny("AUTHORITY_PARAMETER_INVALID", parameter_key=limit.parameter_key)
            if value > limit.maximum:
                approval_reasons.append(
                    {"parameter_key": limit.parameter_key, "value": value, "maximum": limit.maximum}
                )
        for approval in policy.approval_required_values:
            if approval.parameter_key not in parameters:
                continue
            value = parameters.get(approval.parameter_key)
            if value in approval.values:
                approval_reasons.append(
                    {"parameter_key": approval.parameter_key, "value": value, "approval": True}
                )
    if approval_reasons:
        return GenericAuthorityDecision(
            AuthorityOutcome.REQUIRE_PLAYER_DECISION,
            "ACTION_APPROVAL_REQUIRED",
            {"reasons": approval_reasons},
        )
    return GenericAuthorityDecision(AuthorityOutcome.ALLOW, "WITHIN_AUTHORITY", {})


def _deny(code: str, **details: object) -> GenericAuthorityDecision:
    return GenericAuthorityDecision(AuthorityOutcome.DENY, code, details)
