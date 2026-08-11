from __future__ import annotations

from typing import Any


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.retryable = retryable


class NotFoundError(AppError):
    def __init__(self, resource: str, resource_id: object) -> None:
        super().__init__(
            f"{resource.upper()}_NOT_FOUND",
            f"{resource} was not found",
            status_code=404,
            details={"id": str(resource_id)},
        )


class ConflictError(AppError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=409, details=details)


class AuthorizationError(AppError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=403, details=details)
