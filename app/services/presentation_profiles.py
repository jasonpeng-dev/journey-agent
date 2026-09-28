"""Persistence service for Scenario-wide PresentationProfile policy."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.presentation import (
    default_presentation_profile_document,
    validate_presentation_profile,
)
from app.infrastructure.db.models import (
    Scenario,
    ScenarioPresentationProfile,
    ScenarioPresentationProfileRevision,
)


class PresentationProfileLifecycleError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class PresentationProfileService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_current(self, scenario_id: UUID) -> ScenarioPresentationProfile:
        self._scenario(scenario_id)
        profile = self.db.get(ScenarioPresentationProfile, scenario_id)
        if profile is not None:
            return profile
        document = default_presentation_profile_document()
        profile = ScenarioPresentationProfile(
            scenario_id=scenario_id,
            revision=1,
            profile_document=deepcopy(document),
        )
        self.db.add(profile)
        self.db.add(
            ScenarioPresentationProfileRevision(
                scenario_id=scenario_id,
                revision=1,
                profile_document=deepcopy(document),
            )
        )
        self.db.flush()
        return profile

    def list_revisions(self, scenario_id: UUID) -> tuple[ScenarioPresentationProfileRevision, ...]:
        self._scenario(scenario_id)
        return tuple(
            self.db.scalars(
                select(ScenarioPresentationProfileRevision)
                .where(ScenarioPresentationProfileRevision.scenario_id == scenario_id)
                .order_by(ScenarioPresentationProfileRevision.revision.desc())
            ).all()
        )

    def replace(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        profile_document: dict[str, Any],
    ) -> ScenarioPresentationProfile:
        self._require_mutable(scenario_id)
        try:
            validated = validate_presentation_profile(profile_document)
        except ValidationError as exc:
            raise PresentationProfileLifecycleError(
                "SCENARIO_PRESENTATION_PROFILE_INVALID",
                "The PresentationProfile document is not a supported bounded policy",
                details={"errors": exc.errors()},
            ) from exc
        current = self.get_current(scenario_id)
        if current.revision != expected_revision:
            raise PresentationProfileLifecycleError(
                "SCENARIO_PRESENTATION_PROFILE_CONFLICT",
                "The PresentationProfile revision changed before save",
                details={
                    "current_revision": current.revision,
                    "expected_revision": expected_revision,
                },
            )
        next_revision = current.revision + 1
        document = validated.model_dump(mode="json", exclude_none=True)
        current.revision = next_revision
        current.profile_document = deepcopy(document)
        self.db.add(
            ScenarioPresentationProfileRevision(
                scenario_id=scenario_id,
                revision=next_revision,
                profile_document=deepcopy(document),
            )
        )
        self.db.flush()
        return current

    def restore(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        revision: int,
    ) -> ScenarioPresentationProfile:
        self._require_mutable(scenario_id)
        current = self.get_current(scenario_id)
        if current.revision != expected_revision:
            raise PresentationProfileLifecycleError(
                "SCENARIO_PRESENTATION_PROFILE_CONFLICT",
                "The PresentationProfile revision changed before restore",
                details={
                    "current_revision": current.revision,
                    "expected_revision": expected_revision,
                },
            )
        source = self.db.scalar(
            select(ScenarioPresentationProfileRevision).where(
                ScenarioPresentationProfileRevision.scenario_id == scenario_id,
                ScenarioPresentationProfileRevision.revision == revision,
            )
        )
        if source is None:
            raise PresentationProfileLifecycleError(
                "SCENARIO_PRESENTATION_PROFILE_REVISION_NOT_FOUND",
                "The requested PresentationProfile revision does not exist",
            )
        try:
            validated = validate_presentation_profile(source.profile_document)
        except ValidationError as exc:
            raise PresentationProfileLifecycleError(
                "SCENARIO_PRESENTATION_PROFILE_INVALID",
                "The stored PresentationProfile revision is no longer supported",
                details={"errors": exc.errors()},
            ) from exc
        next_revision = current.revision + 1
        document = validated.model_dump(mode="json", exclude_none=True)
        current.revision = next_revision
        current.profile_document = deepcopy(document)
        self.db.add(
            ScenarioPresentationProfileRevision(
                scenario_id=scenario_id,
                revision=next_revision,
                profile_document=deepcopy(document),
            )
        )
        self.db.flush()
        return current

    def _scenario(self, scenario_id: UUID) -> Scenario:
        scenario = self.db.get(Scenario, scenario_id)
        if scenario is None:
            raise PresentationProfileLifecycleError(
                "SCENARIO_NOT_FOUND",
                "The Scenario does not exist",
            )
        return scenario

    def _require_mutable(self, scenario_id: UUID) -> Scenario:
        scenario = self._scenario(scenario_id)
        if scenario.status == "ARCHIVED":
            raise PresentationProfileLifecycleError(
                "SCENARIO_ARCHIVED",
                "An archived Scenario cannot modify its PresentationProfile",
            )
        return scenario
