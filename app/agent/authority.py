from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.enums import AuthorityOutcome
from app.infrastructure.db.models import NPC

AUTHORITY_LIMIT_CAPS = {
    "max_troops": 300,
    "max_food": 100,
    "max_gold": 80,
    "max_intelligence_gold": 80,
}


@dataclass(frozen=True)
class AuthorityDecision:
    outcome: AuthorityOutcome
    reason_code: str
    summary: str
    details: dict[str, Any]


def effective_authority_limits(
    officer: NPC,
    authority_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge the officer profile with this player's appointment policy."""

    return {
        **(officer.authority_limits or {}),
        **(authority_overrides or {}),
    }


def evaluate_authority(
    officer: NPC,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    authority_overrides: dict[str, Any] | None = None,
    policy_version: int | None = None,
) -> AuthorityDecision:
    """Evaluate parameter-level autonomy without changing game state.

    Role/capability checks remain in ToolExecutor. This policy only determines
    whether a capable officer may autonomously choose the requested magnitude.
    """

    effective_policy_version = (
        policy_version if policy_version is not None else officer.profile_version
    )
    policy_errors = authority_policy_errors(
        officer.authority_limits,
        authority_overrides,
    )
    if policy_errors:
        return AuthorityDecision(
            outcome=AuthorityOutcome.DENY,
            reason_code="AUTHORITY_POLICY_INVALID",
            summary="该部下的权限策略无效, 无法授权行动。",
            details={
                "officer_id": str(officer.id),
                "officer_key": officer.key,
                "tool_name": tool_name,
                "policy_errors": policy_errors,
                "policy_version": effective_policy_version,
            },
        )
    limits = effective_authority_limits(officer, authority_overrides)
    checks: list[tuple[str, int, int]] = []
    risk_flags: list[str] = []
    if tool_name in {"start_recon_operation", "start_military_operation"}:
        requested = _integer(arguments.get("troop_count"))
        maximum = _integer(limits.get("max_troops"))
        checks.append(("troop_count", requested, maximum))
        if arguments.get("approach") == "AGGRESSIVE" or arguments.get("strategy") == "AGGRESSIVE":
            risk_flags.append("AGGRESSIVE_OPERATION")
    elif tool_name == "negotiate_village_support":
        requested = _integer(arguments.get("food_offer"))
        maximum = _integer(limits.get("max_food"))
        checks.append(("food_offer", requested, maximum))
    elif tool_name == "start_outpost_repair":
        checks.extend(
            [
                (
                    "food_commitment",
                    _integer(arguments.get("food_commitment")),
                    _integer(limits.get("max_food")),
                ),
                (
                    "gold_commitment",
                    _integer(arguments.get("gold_commitment")),
                    _integer(limits.get("max_gold")),
                ),
            ]
        )
        if arguments.get("repair_level") == "FULL":
            risk_flags.append("FULL_RECONSTRUCTION")

    exceeded = [
        {"field": field, "requested": requested, "limit": maximum}
        for field, requested, maximum in checks
        if requested > maximum
    ]
    if exceeded or risk_flags:
        first = exceeded[0] if exceeded else None
        summary = (
            (
                f"{_officer_name(officer)}请求将“{_field_name(str(first['field']))}”"
                f"设为 {first['requested']}, 超过自主权限上限 {first['limit']}。"
            )
            if first is not None
            else (
                f"{_officer_name(officer)}提出超出自主决策范围的高风险行动"
                f"({', '.join(risk_flags)})。"
            )
        )
        return AuthorityDecision(
            outcome=AuthorityOutcome.REQUIRE_PLAYER_DECISION,
            reason_code=(
                "AUTHORITY_LIMIT_EXCEEDED" if exceeded else "HIGH_RISK_ACTION_REQUIRES_APPROVAL"
            ),
            summary=summary,
            details={
                "officer_id": str(officer.id),
                "officer_key": officer.key,
                "tool_name": tool_name,
                "exceeded_limits": exceeded,
                "risk_flags": risk_flags,
                "policy_version": effective_policy_version,
            },
        )
    return AuthorityDecision(
        outcome=AuthorityOutcome.ALLOW,
        reason_code="WITHIN_AUTHORITY",
        summary="该行动处于部下获授的自主权限范围内。",
        details={
            "officer_id": str(officer.id),
            "officer_key": officer.key,
            "tool_name": tool_name,
            "checked_limits": [
                {"field": field, "requested": requested, "limit": maximum}
                for field, requested, maximum in checks
            ],
            "policy_version": effective_policy_version,
        },
    )


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _officer_name(officer: NPC) -> str:
    return {
        "shen_ce": "沈策",
        "han_lie": "韩烈",
        "lu_ning": "陆宁",
    }.get(officer.key, officer.name)


def _field_name(field: str) -> str:
    return {
        "troop_count": "投入兵力",
        "food_offer": "粮草提议",
        "food_commitment": "粮草投入",
        "gold_commitment": "金钱投入",
    }.get(field, field)


def authority_policy_errors(
    base_limits: object,
    authority_overrides: object,
) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = []
    for source, values in (
        ("PROFILE", base_limits),
        ("APPOINTMENT", authority_overrides),
    ):
        if values is None:
            continue
        if not isinstance(values, dict):
            errors.append({"source": source, "reason": "NOT_AN_OBJECT"})
            continue
        for field, value in values.items():
            maximum = AUTHORITY_LIMIT_CAPS.get(field)
            if maximum is None:
                errors.append(
                    {
                        "source": source,
                        "field": field,
                        "reason": "UNKNOWN_LIMIT",
                    }
                )
            elif (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or value > maximum
            ):
                errors.append(
                    {
                        "source": source,
                        "field": field,
                        "reason": "OUT_OF_RANGE",
                        "minimum": 0,
                        "maximum": maximum,
                    }
                )
    return errors
