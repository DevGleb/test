"""Unified {"error": {"code", "message", "details"}} responses for every endpoint."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers

    def body(self) -> dict[str, Any]:
        return error_body(self.code, self.message, self.details)


def unauthorized(message: str = "Missing or invalid API key") -> AppError:
    return AppError(401, "unauthorized", message)


def validation_error(errors: list[dict[str, Any]]) -> AppError:
    clean = [
        {
            "loc": [str(part) for part in e.get("loc", ())],
            "msg": e.get("msg", ""),
            "type": e["type"],
        }
        for e in errors
    ]
    if any(e["type"] == "json_invalid" for e in clean):
        return AppError(400, "malformed_json", "Request body is not valid JSON", {"errors": clean})
    return AppError(422, "validation_error", "Request validation failed", {"errors": clean})


_HTTP_CODES = {
    404: "not_found",
    405: "method_not_allowed",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        err = validation_error(list(exc.errors()))
        return JSONResponse(err.body(), status_code=err.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return JSONResponse(
            error_body(code, str(exc.detail)), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(error_body("internal_error", "Internal server error"), status_code=500)
