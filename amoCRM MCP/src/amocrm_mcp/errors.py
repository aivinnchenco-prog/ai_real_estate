"""Normalized MCP / amoCRM errors."""

from __future__ import annotations

from dataclasses import dataclass


class AmoMcpError(Exception):
    code: str = "AMO_API_ERROR"

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None):
        super().__init__(message)
        if code:
            self.code = code
        self.status = status


class ReadOnlyViolation(AmoMcpError):
    code = "READ_ONLY_VIOLATION"


class AuthError(AmoMcpError):
    code = "AUTH_ERROR"


class NotFoundError(AmoMcpError):
    code = "NOT_FOUND"


class PermissionDeniedError(AmoMcpError):
    code = "PERMISSION_DENIED"


class RateLimitedError(AmoMcpError):
    code = "RATE_LIMITED"


class InvalidArgumentError(AmoMcpError):
    code = "INVALID_ARGUMENT"


@dataclass
class ErrorPayload:
    code: str
    message: str
    status: int | None = None

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "status": self.status}}
