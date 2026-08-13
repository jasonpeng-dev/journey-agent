"""Built-in Starfire v2 publication compatibility name."""

from sqlalchemy.orm import Session

from app.infrastructure.db.models import ScenarioVersion
from app.scenarios.builtin import STARFIRE_V2, require_builtin_v2_version


def require_builtin_starfire_version(db: Session) -> ScenarioVersion:
    return require_builtin_v2_version(db, STARFIRE_V2)


__all__ = ["require_builtin_starfire_version"]
