from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import get_settings
from security.request_id import get_request_id


def http_error_response(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "request_id": get_request_id(request)},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        settings = get_settings()
        detail = "validation error"
        if settings.api_expose_error_details or settings.environment == "development":
            detail = str(exc.errors())
        return _error_response(request, 422, detail)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        settings = get_settings()
        detail = "internal server error"
        if settings.api_expose_error_details or settings.environment == "development":
            detail = str(exc)
        return _error_response(request, 500, detail)


def _error_response(request: Request, status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": detail, "request_id": get_request_id(request)},
    )
