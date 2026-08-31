"""Runtime helpers for frozen Formal Goal contracts."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domain.formal_goal import FormalGoalContractV1
from app.domain.runtime_scope import RuntimeScope
from app.services.objective_requirements import truth_requirement_satisfied


@dataclass(frozen=True, slots=True)
class FormalGoalRequirementEvaluation:
    identity: str
    value: object
    satisfied: bool


@dataclass(frozen=True, slots=True)
class FormalGoalEvaluation:
    completed: bool
    requirements: tuple[FormalGoalRequirementEvaluation, ...]


class FormalGoalCompletionEvaluator:
    """Evaluate every frozen typed requirement against authoritative Truth."""

    def __init__(self, db: Session, scope: RuntimeScope) -> None:
        self.db = db
        self.scope = scope

    def evaluate(self, contract: FormalGoalContractV1) -> FormalGoalEvaluation:
        evaluations: list[FormalGoalRequirementEvaluation] = []
        for item in contract.completion_requirements:
            value, satisfied = truth_requirement_satisfied(
                self.db,
                self.scope,
                item.requirement,
            )
            evaluations.append(
                FormalGoalRequirementEvaluation(
                    identity=item.identity,
                    value=value,
                    satisfied=satisfied,
                )
            )
        return FormalGoalEvaluation(
            completed=bool(evaluations) and all(item.satisfied for item in evaluations),
            requirements=tuple(evaluations),
        )


__all__ = [
    "FormalGoalCompletionEvaluator",
    "FormalGoalEvaluation",
    "FormalGoalRequirementEvaluation",
]
