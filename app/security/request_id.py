import logging
import re
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def normalize_request_id(value: str | None) -> str:
    if value and _REQUEST_ID_RE.match(value.strip()):
        return value.strip()
    return str(uuid.uuid4())


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = normalize_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", normalize_request_id(None))
