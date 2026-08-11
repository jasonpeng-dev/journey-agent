"""Small enums and scalar types for the world domain."""

from enum import StrEnum

type FactValue = str | int | bool


class WorldNodeType(StrEnum):
    """A user-facing category for an independently addressable world object."""

    HEADQUARTERS = "HEADQUARTERS"
    LOCATION = "LOCATION"
    SETTLEMENT = "SETTLEMENT"
    FACILITY = "FACILITY"
    ROUTE = "ROUTE"


class AccessState(StrEnum):
    """Whether a node can currently be accessed or targeted."""

    LOCKED = "LOCKED"
    AVAILABLE = "AVAILABLE"


class Visibility(StrEnum):
    """Whether a player and their agents currently know an object or fact."""

    HIDDEN = "HIDDEN"
    KNOWN = "KNOWN"


class RelationType(StrEnum):
    """A semantic link interpreted by a scenario ruleset."""

    CONNECTED_TO = "CONNECTED_TO"
    REVEALS = "REVEALS"
    UNLOCKS = "UNLOCKS"
    SUPPORTS = "SUPPORTS"
    BLOCKS = "BLOCKS"
    ENABLES = "ENABLES"


class FactValueType(StrEnum):
    """The supported scalar shape of a fact's truth value."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"
    ENUM = "ENUM"
