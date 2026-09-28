"""Shared static semantics for resolving RELATED Node selectors.

The runtime rule engine and authoring reference scanner both consume this
helper so direction, relation type, and candidate-Fact filtering stay aligned.
The caller supplies only the authored topology and Fact keys; an empty anchor
set means there are no possible candidates, while an unknown anchor universe
must be handled conservatively by the caller.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set


def related_candidate_node_keys(
    *,
    anchor_node_keys: Iterable[str],
    relation_type_key: str,
    direction: str,
    relation_edges: Iterable[tuple[str, str, str]],
    node_fact_keys: Mapping[str, Set[str]],
    required_fact_key: str | None = None,
) -> tuple[str, ...]:
    """Return the Nodes a RELATED selector can resolve to for known anchors."""

    anchors = set(anchor_node_keys)
    if not anchors or not relation_type_key or direction not in {"SOURCE", "TARGET"}:
        return ()

    candidates: set[str] = set()
    for source_node_key, edge_type_key, target_node_key in relation_edges:
        if edge_type_key != relation_type_key:
            continue
        if direction == "SOURCE" and source_node_key in anchors:
            candidate = target_node_key
        elif direction == "TARGET" and target_node_key in anchors:
            candidate = source_node_key
        else:
            continue
        facts = node_fact_keys.get(candidate)
        if facts is None:
            continue
        if required_fact_key is not None and required_fact_key not in facts:
            continue
        candidates.add(candidate)

    return tuple(sorted(candidates))
