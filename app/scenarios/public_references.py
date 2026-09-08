"""Compiled public-reference lookup for one immutable ScenarioVersion."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

from app.domain.scenario_v2 import PublicReferenceTypeV2, ScenarioDefinitionV2


class PublicReferenceSource(StrEnum):
    CANONICAL_KEY = "CANONICAL_KEY"
    CANONICAL_NAME = "CANONICAL_NAME"
    AUTHORED_REFERENCE = "AUTHORED_REFERENCE"


@dataclass(frozen=True, slots=True)
class PublicReferenceIdentity:
    ref_type: PublicReferenceTypeV2
    ref_key: str


@dataclass(frozen=True, slots=True)
class PublicReferenceEntry:
    term: str
    normalized_term: str
    identity: PublicReferenceIdentity
    source: PublicReferenceSource


@dataclass(frozen=True, slots=True)
class PublicReferenceTermMatch:
    term: str
    normalized_term: str
    identities: tuple[PublicReferenceIdentity, ...]
    sources: tuple[PublicReferenceSource, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.identities) > 1


@dataclass(frozen=True, slots=True)
class PublicReferenceLookup:
    matches: tuple[PublicReferenceTermMatch, ...]

    @property
    def identities(self) -> tuple[PublicReferenceIdentity, ...]:
        unique = {
            (identity.ref_type, identity.ref_key): identity
            for match in self.matches
            for identity in match.identities
        }
        ordered = sorted(unique, key=lambda item: (item[0].value, item[1]))
        return tuple(unique[key] for key in ordered)

    @property
    def ambiguous_matches(self) -> tuple[PublicReferenceTermMatch, ...]:
        return tuple(match for match in self.matches if match.ambiguous)


def normalize_public_reference_term(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _term_spans(text: str, term: str) -> tuple[tuple[int, int], ...]:
    if not term:
        return ()
    if any(ord(character) > 127 for character in term):
        return tuple((match.start(), match.end()) for match in re.finditer(re.escape(term), text))
    pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
    return tuple((match.start(), match.end()) for match in re.finditer(pattern, text))


class PublicReferenceIndex:
    """Exact evidence index; absence never decides semantic no-match."""

    def __init__(self, entries: Iterable[PublicReferenceEntry]):
        by_term: dict[str, dict[tuple[PublicReferenceTypeV2, str], PublicReferenceEntry]] = (
            defaultdict(dict)
        )
        for entry in entries:
            identity = (entry.identity.ref_type, entry.identity.ref_key)
            current = by_term[entry.normalized_term].get(identity)
            if current is None or entry.source < current.source:
                by_term[entry.normalized_term][identity] = entry
        self._by_term = {}
        for term, values in by_term.items():
            ordered = sorted(values, key=lambda item: (item[0].value, item[1]))
            self._by_term[term] = tuple(values[key] for key in ordered)

    def terms_for(self, ref_type: PublicReferenceTypeV2, ref_key: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    entry.term
                    for entries in self._by_term.values()
                    for entry in entries
                    if entry.identity == PublicReferenceIdentity(ref_type, ref_key)
                    and entry.source == PublicReferenceSource.AUTHORED_REFERENCE
                },
                key=normalize_public_reference_term,
            )
        )

    def lookup(
        self,
        text: str,
        *,
        ref_types: Collection[PublicReferenceTypeV2] | None = None,
        allowed_identities: Collection[PublicReferenceIdentity] | None = None,
    ) -> PublicReferenceLookup:
        normalized_text = normalize_public_reference_term(text)
        allowed_type_set = set(ref_types) if ref_types is not None else None
        allowed_identity_set = set(allowed_identities) if allowed_identities is not None else None
        hits: list[tuple[str, tuple[int, int], tuple[PublicReferenceEntry, ...]]] = []
        for term, entries in self._by_term.items():
            filtered = tuple(
                entry
                for entry in entries
                if (allowed_type_set is None or entry.identity.ref_type in allowed_type_set)
                and (allowed_identity_set is None or entry.identity in allowed_identity_set)
            )
            if not filtered:
                continue
            hits.extend((term, span, filtered) for span in _term_spans(normalized_text, term))

        # Prefer the most specific authored expression at an overlapping span.
        selected = [
            hit
            for hit in hits
            if not any(
                other[1][0] <= hit[1][0]
                and other[1][1] >= hit[1][1]
                and other[1] != hit[1]
                for other in hits
            )
        ]
        grouped: dict[str, dict[tuple[PublicReferenceTypeV2, str], PublicReferenceEntry]] = (
            defaultdict(dict)
        )
        display_terms: dict[str, str] = {}
        for term, _span, entries in selected:
            display_terms.setdefault(term, entries[0].term)
            for entry in entries:
                identity = entry.identity
                grouped[term][(identity.ref_type, identity.ref_key)] = entry
        matches = tuple(
            PublicReferenceTermMatch(
                term=display_terms[term],
                normalized_term=term,
                identities=tuple(
                    grouped[term][key].identity
                    for key in sorted(grouped[term], key=lambda item: (item[0].value, item[1]))
                ),
                sources=tuple(
                    grouped[term][key].source
                    for key in sorted(grouped[term], key=lambda item: (item[0].value, item[1]))
                ),
            )
            for term in sorted(grouped)
        )
        return PublicReferenceLookup(matches=matches)


class PublicReferenceIndexBuilder:
    @staticmethod
    @lru_cache(maxsize=64)
    def build(definition: ScenarioDefinitionV2) -> PublicReferenceIndex:
        entries: list[PublicReferenceEntry] = []
        region_type = (
            definition.metadata.locality.region_node_type_key
            if definition.metadata.locality.enabled
            else None
        )

        def add(
            term: str,
            ref_type: PublicReferenceTypeV2,
            ref_key: str,
            source: PublicReferenceSource,
        ) -> None:
            entries.append(
                PublicReferenceEntry(
                    term=term,
                    normalized_term=normalize_public_reference_term(term),
                    identity=PublicReferenceIdentity(ref_type, ref_key),
                    source=source,
                )
            )

        canonical: list[tuple[PublicReferenceTypeV2, str, str]] = []
        canonical.extend(
            (
                PublicReferenceTypeV2.REGION
                if node.node_type_key == region_type
                else PublicReferenceTypeV2.NODE,
                node.key,
                node.name,
            )
            for node in definition.world.nodes
        )
        canonical.extend(
            (PublicReferenceTypeV2.RESOURCE, item.key, item.name)
            for item in definition.world.resources
        )
        canonical.extend(
            (PublicReferenceTypeV2.DERIVED_STATE, item.key, item.name)
            for item in definition.derived_states
            if item.goal_addressable
        )
        canonical.extend(
            (PublicReferenceTypeV2.ACTION, item.key, item.name) for item in definition.actions
        )
        canonical.extend(
            (PublicReferenceTypeV2.ACTOR, item.key, item.name)
            for item in definition.actors.actor_profiles
        )
        for ref_type, key, name in canonical:
            add(key, ref_type, key, PublicReferenceSource.CANONICAL_KEY)
            add(name, ref_type, key, PublicReferenceSource.CANONICAL_NAME)
        for state in definition.derived_states:
            if state.goal_addressable:
                for term in (*state.goal_aliases, *state.goal_examples):
                    add(
                        term,
                        PublicReferenceTypeV2.DERIVED_STATE,
                        state.key,
                        PublicReferenceSource.AUTHORED_REFERENCE,
                    )
        for reference in definition.public_references:
            add(
                reference.term,
                reference.ref_type,
                reference.ref_key,
                PublicReferenceSource.AUTHORED_REFERENCE,
            )
        return PublicReferenceIndex(entries)


__all__ = [
    "PublicReferenceIdentity",
    "PublicReferenceIndex",
    "PublicReferenceIndexBuilder",
    "PublicReferenceLookup",
    "PublicReferenceSource",
    "PublicReferenceTermMatch",
    "normalize_public_reference_term",
]
