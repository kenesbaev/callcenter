from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: list[dict[str, Any]] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    body = ErrorEnvelope(
        error=ErrorBody(
            code=exc.code,
            message=exc.message,
            correlation_id=correlation_id,
            details=exc.details,
        )
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    details = [
        {
            "location": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    body = ErrorEnvelope(
        error=ErrorBody(
            code="validation_error",
            message="Request validation failed",
            correlation_id=correlation_id,
            details=details,
        )
    )
    return JSONResponse(status_code=422, content=body.model_dump(mode="json"))
