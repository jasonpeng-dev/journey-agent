"""Frozen, exact-Version Objective scope contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


class ObjectiveScopeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ObjectiveScope:
    objective_keys: tuple[str, ...]
    catalog_version: str

    def __post_init__(self) -> None:
        normalized = tuple(sorted(set(self.objective_keys)))
        if not normalized or any(not key.strip() for key in normalized):
            raise ObjectiveScopeError("Objective scope must contain at least one valid key")
        if normalized != self.objective_keys:
            raise ObjectiveScopeError("Objective scope keys must be unique and canonical")
        if not self.catalog_version.strip():
            raise ObjectiveScopeError("Objective scope requires an exact catalog version")

    @classmethod
    def create(cls, keys: list[str] | tuple[str, ...], catalog_version: str) -> ObjectiveScope:
        return cls(tuple(sorted(set(keys))), catalog_version)

    @property
    def content_hash(self) -> str:
        payload = json.dumps(
            {"catalog_version": self.catalog_version, "objective_keys": self.objective_keys},
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()
