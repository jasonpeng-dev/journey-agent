"""Atomic materialization of an independent GameInstance from an archive."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import GameInstanceStatus
from app.domain.resources import (
    resource_identity,
    resource_pool_initial_states,
    valid_resource_state_identity,
)
from app.domain.scenario_v2 import ScenarioDefinitionV2, relation_identity
from app.infrastructure.db.models import (
    ConversationSession,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceRelationKnowledge,
    GameInstanceResourceState,
)
from app.scenarios.versions import ScenarioVersionRepository


class GameForkError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ForkedRuntime:
    instance: GameInstance
    session: ConversationSession
    created: bool


class GameForkService:
    """Create a fresh runtime graph from one immutable Archived source."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def materialize(
        self,
        *,
        source_game_instance_id: UUID,
        player_id: UUID,
        creation_key: str,
    ) -> ForkedRuntime:
        if not creation_key.strip():
            raise GameForkError(
                "FORK_CREATION_KEY_REQUIRED",
                "Fork creation requires a non-empty idempotency key",
            )
        source = self.db.scalar(
            select(GameInstance)
            .where(
                GameInstance.id == source_game_instance_id,
                GameInstance.player_id == player_id,
            )
            .with_for_update()
        )
        if source is None:
            raise GameForkError("GAME_INSTANCE_NOT_FOUND", "The source Game does not exist")
        if source.status != GameInstanceStatus.ARCHIVED:
            raise GameForkError(
                "FORK_SOURCE_NOT_ARCHIVED",
                "Only an Archived GameInstance can be forked",
            )

        existing = self._existing(player_id, creation_key)
        if existing is not None:
            return self._replay(existing, source)

        version = ScenarioVersionRepository(self.db).load(source.scenario_version_id)
        definition = version.definition
        if not isinstance(definition, ScenarioDefinitionV2):
            raise GameForkError(
                "FORK_SCENARIO_VERSION_INVALID",
                "Fork requires a v2 ScenarioVersion runtime definition",
            )
        source_rows = self._validate_source(source, definition)
        try:
            with self.db.begin_nested():
                target = GameInstance(
                    player_id=source.player_id,
                    scenario_version_id=source.scenario_version_id,
                    forked_from_game_instance_id=source.id,
                    status=GameInstanceStatus.PENDING_INITIALIZATION,
                    current_node_key=None,
                    creation_key=creation_key,
                    runtime_revision=0,
                )
                self.db.add(target)
                self.db.flush()
                self._copy_nodes(target, source_rows[0])
                self._copy_facts(target, source_rows[1])
                self._copy_resources(target, source_rows[2], definition)
                self._copy_region_knowledge(target, source_rows[3])
                self._copy_relation_knowledge(target, source_rows[4], definition)
                primary_key = self._copy_actors(target, source_rows[5], definition)
                primary_actor = next(
                    actor for actor in source_rows[5] if actor.actor_key == primary_key
                )
                target.current_node_key = primary_actor.current_node_key
                session = ConversationSession(
                    player_id=target.player_id,
                    game_instance_id=target.id,
                    actor_key=primary_key,
                )
                self.db.add(session)
                target.status = GameInstanceStatus.ACTIVE
                target.runtime_revision = 1
                self.db.flush()
        except IntegrityError as exc:
            self.db.expire_all()
            concurrent = self._existing(player_id, creation_key)
            if concurrent is not None:
                source = self.db.get(GameInstance, source.id)
                if source is not None:
                    return self._replay(concurrent, source)
            raise GameForkError(
                "FORK_MATERIALIZATION_FAILED",
                "The Fork runtime graph could not be materialized",
            ) from exc
        except Exception:
            self.db.expire_all()
            raise
        return ForkedRuntime(instance=target, session=session, created=True)

    def _existing(self, player_id: UUID, creation_key: str) -> GameInstance | None:
        return self.db.scalar(
            select(GameInstance)
            .where(
                GameInstance.player_id == player_id,
                GameInstance.creation_key == creation_key,
            )
            .with_for_update()
        )

    def _replay(self, target: GameInstance, source: GameInstance) -> ForkedRuntime:
        if (
            target.forked_from_game_instance_id != source.id
            or target.scenario_version_id != source.scenario_version_id
        ):
            raise GameForkError(
                "FORK_CREATION_KEY_REUSED",
                "The creation key is already bound to another Fork source",
            )
        if target.status != GameInstanceStatus.ACTIVE:
            raise GameForkError(
                "FORK_INITIALIZATION_INCOMPLETE",
                "The idempotent Fork target is not fully initialized",
            )
        session = self.db.scalar(
            select(ConversationSession).where(ConversationSession.game_instance_id == target.id)
        )
        if session is None:
            raise GameForkError(
                "FORK_INITIALIZATION_INCOMPLETE",
                "The idempotent Fork target has no ConversationSession",
            )
        return ForkedRuntime(instance=target, session=session, created=False)

    def _validate_source(
        self, source: GameInstance, definition: ScenarioDefinitionV2
    ) -> tuple[
        tuple[GameInstanceNodeState, ...],
        tuple[GameInstanceFactState, ...],
        tuple[GameInstanceResourceState, ...],
        tuple[GameInstanceRegionResourceKnowledge, ...],
        tuple[GameInstanceRelationKnowledge, ...],
        tuple[GameInstanceActor, ...],
    ]:
        required_tables = (
            "game_instance_region_resource_knowledge",
            "game_instance_relation_knowledge",
        )
        if any(not inspect(self.db.connection()).has_table(table) for table in required_tables):
            raise GameForkError(
                "FORK_RUNTIME_SCHEMA_REQUIRED",
                "The database must be upgraded before a Game can be forked",
            )
        nodes = tuple(
            self.db.scalars(
                select(GameInstanceNodeState)
                .where(GameInstanceNodeState.game_instance_id == source.id)
                .order_by(GameInstanceNodeState.node_key)
            )
        )
        facts = tuple(
            self.db.scalars(
                select(GameInstanceFactState)
                .where(GameInstanceFactState.game_instance_id == source.id)
                .order_by(GameInstanceFactState.node_key, GameInstanceFactState.fact_key)
            )
        )
        resources = tuple(
            self.db.scalars(
                select(GameInstanceResourceState)
                .where(GameInstanceResourceState.game_instance_id == source.id)
                .order_by(GameInstanceResourceState.resource_identity)
            )
        )
        regions = tuple(
            self.db.scalars(
                select(GameInstanceRegionResourceKnowledge)
                .where(GameInstanceRegionResourceKnowledge.game_instance_id == source.id)
                .order_by(GameInstanceRegionResourceKnowledge.region_key)
            )
        )
        relations = tuple(
            self.db.scalars(
                select(GameInstanceRelationKnowledge)
                .where(GameInstanceRelationKnowledge.game_instance_id == source.id)
                .order_by(GameInstanceRelationKnowledge.relation_key)
            )
        )
        actors = tuple(
            self.db.scalars(
                select(GameInstanceActor)
                .where(GameInstanceActor.game_instance_id == source.id)
                .order_by(GameInstanceActor.actor_key)
            )
        )
        node_definitions = {item.key: item for item in definition.world.nodes}
        fact_keys = {(node.key, fact.key) for node in definition.world.nodes for fact in node.facts}
        expected_regions = {
            node.key
            for node in definition.world.nodes
            if definition.metadata.locality.enabled
            and node.node_type_key == definition.metadata.locality.region_node_type_key
        }
        expected_relations = {relation_identity(item) for item in definition.world.relations}
        expected_actor_keys = {item.key for item in definition.actors.actor_profiles}
        if (
            {item.node_key for item in nodes} != set(node_definitions)
            or {(item.node_key, item.fact_key) for item in facts} != fact_keys
            or {item.region_key for item in regions} != expected_regions
            or {item.relation_key for item in relations} != expected_relations
            or {item.actor_key for item in actors} != expected_actor_keys
        ):
            raise GameForkError(
                "FORK_SOURCE_RUNTIME_INVALID",
                "The Archived source runtime graph does not match its ScenarioVersion",
            )
        node_keys = set(node_definitions)
        if any(actor.current_node_key not in node_keys for actor in actors):
            raise GameForkError(
                "FORK_SOURCE_RUNTIME_INVALID",
                "An Archived actor points to a node absent from its ScenarioVersion",
            )
        if any(resource.reserved_value != 0 for resource in resources):
            raise GameForkError(
                "FORK_SOURCE_RESERVATION_ACTIVE",
                "A Fork source cannot contain reserved resources",
            )
        if (
            len(
                [
                    item
                    for item in actors
                    if item.actor_key == definition.initialization.primary_actor_key
                ]
            )
            != 1
        ):
            raise GameForkError(
                "FORK_SOURCE_RUNTIME_INVALID",
                "The Archived source has no unique primary actor",
            )
        return nodes, facts, resources, regions, relations, actors

    def _copy_nodes(self, target: GameInstance, rows: tuple[GameInstanceNodeState, ...]) -> None:
        for row in rows:
            target_state = GameInstanceNodeState(
                game_instance_id=target.id,
                node_key=row.node_key,
                status=row.status,
                visibility=row.visibility,
                version=1,
            )
            self.db.add(target_state)

    def _copy_facts(self, target: GameInstance, rows: tuple[GameInstanceFactState, ...]) -> None:
        for row in rows:
            self.db.add(
                GameInstanceFactState(
                    game_instance_id=target.id,
                    node_key=row.node_key,
                    fact_key=row.fact_key,
                    truth_value=deepcopy(row.truth_value),
                    visibility=row.visibility,
                    version=1,
                )
            )

    def _copy_resources(
        self,
        target: GameInstance,
        rows: tuple[GameInstanceResourceState, ...],
        definition: ScenarioDefinitionV2,
    ) -> None:
        pools = resource_pool_initial_states(definition)
        for row in rows:
            if row.resource_identity != resource_identity(
                row.resource_key, row.scope_node_key, row.pool_key
            ) or not valid_resource_state_identity(
                definition, row.resource_key, row.scope_node_key, row.pool_key
            ):
                raise GameForkError(
                    "FORK_SOURCE_RUNTIME_INVALID",
                    "The Archived source contains an invalid Resource identity",
                )
            static_pool = next(
                (
                    item
                    for item in pools
                    if item.resource_key == row.resource_key
                    and item.pool_key == row.pool_key
                    and item.region_key == row.scope_node_key
                ),
                None,
            )
            if static_pool is None:
                static_pool = next(
                    (
                        item
                        for item in pools
                        if item.resource_key == row.resource_key
                        and item.pool_key == row.pool_key
                        and item.region_key is None
                    ),
                    None,
                )
            if static_pool is None and row.pool_key != "default":
                raise GameForkError(
                    "FORK_SOURCE_RUNTIME_INVALID",
                    "The Archived source contains an unknown Resource Pool",
                )
            self.db.add(
                GameInstanceResourceState(
                    game_instance_id=target.id,
                    resource_identity=resource_identity(
                        row.resource_key, row.scope_node_key, row.pool_key
                    ),
                    resource_key=row.resource_key,
                    scope_node_key=row.scope_node_key,
                    pool_key=row.pool_key,
                    facility_key=static_pool.facility_key if static_pool is not None else None,
                    value=row.value,
                    reserved_value=0,
                    visibility=row.visibility,
                    availability=row.availability,
                    survey_discoverable=(
                        static_pool.survey_discoverable if static_pool is not None else False
                    ),
                    availability_requirement=(
                        static_pool.availability_requirement.model_dump(mode="json")
                        if static_pool is not None
                        and static_pool.availability_requirement is not None
                        else None
                    ),
                    version=1,
                )
            )

    def _copy_region_knowledge(
        self,
        target: GameInstance,
        rows: tuple[GameInstanceRegionResourceKnowledge, ...],
    ) -> None:
        for row in rows:
            self.db.add(
                GameInstanceRegionResourceKnowledge(
                    game_instance_id=target.id,
                    region_key=row.region_key,
                    resource_inventory_visibility=row.resource_inventory_visibility,
                    resource_survey_completed=row.resource_survey_completed,
                    version=1,
                )
            )

    def _copy_relation_knowledge(
        self,
        target: GameInstance,
        rows: tuple[GameInstanceRelationKnowledge, ...],
        definition: ScenarioDefinitionV2,
    ) -> None:
        relation_keys = {relation_identity(item) for item in definition.world.relations}
        for row in rows:
            if row.relation_key not in relation_keys:
                raise GameForkError(
                    "FORK_SOURCE_RUNTIME_INVALID",
                    "The Archived source contains an unknown Relation Knowledge key",
                )
            self.db.add(
                GameInstanceRelationKnowledge(
                    game_instance_id=target.id,
                    relation_key=row.relation_key,
                    visibility=row.visibility,
                    version=1,
                )
            )

    def _copy_actors(
        self,
        target: GameInstance,
        rows: tuple[GameInstanceActor, ...],
        definition: ScenarioDefinitionV2,
    ) -> str:
        profiles = {item.key: item for item in definition.actors.actor_profiles}
        roles = {item.key: item for item in definition.actors.roles}
        primary_key = definition.initialization.primary_actor_key
        for row in rows:
            profile = profiles[row.actor_key]
            role = roles.get(profile.role_key)
            if role is None:
                raise GameForkError(
                    "FORK_SCENARIO_VERSION_INVALID",
                    "An Actor profile references a missing Role",
                )
            expected_doctrine = {item.key: item.value for item in profile.doctrine}
            expected_authority = profile.authority_policy.model_dump(mode="json")
            expected_capabilities = [item.value for item in role.capabilities]
            if (
                row.role_key != profile.role_key
                or row.name != profile.name
                or row.persona != profile.persona
                or row.doctrine != expected_doctrine
                or row.allowed_action_keys != list(profile.allowed_action_keys)
                or row.authority_policy != expected_authority
                or row.capabilities != expected_capabilities
            ):
                raise GameForkError(
                    "FORK_SOURCE_RUNTIME_INVALID",
                    "The Archived Actor static metadata does not match its ScenarioVersion",
                )
            self.db.add(
                GameInstanceActor(
                    game_instance_id=target.id,
                    actor_key=row.actor_key,
                    role_key=profile.role_key,
                    name=profile.name,
                    persona=profile.persona,
                    doctrine=deepcopy(expected_doctrine),
                    current_node_key=row.current_node_key,
                    allowed_action_keys=list(profile.allowed_action_keys),
                    authority_policy=deepcopy(expected_authority),
                    capabilities=list(expected_capabilities),
                    command_reachability=row.command_reachability,
                    is_primary=row.actor_key == primary_key,
                    status=row.status,
                    version=1,
                )
            )
        return primary_key


__all__ = ["ForkedRuntime", "GameForkError", "GameForkService"]
