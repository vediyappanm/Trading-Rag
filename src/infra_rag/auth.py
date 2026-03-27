import base64
import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from infra_rag.config import settings
from infra_rag.observability import METRICS

logger = logging.getLogger(__name__)

_EXEMPT_PATHS = {"/health", "/health/ready"}


def is_auth_enabled() -> bool:
    return settings.api.auth_enabled


def is_exempt_path(path: str) -> bool:
    return path in _EXEMPT_PATHS


def _decode_basic_token(token: str) -> tuple[str, str] | None:
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:
        return None
    if ":" not in decoded:
        return None
    username, password = decoded.split(":", 1)
    return username, password


def is_authorized_header(header_value: str | None) -> bool:
    if not is_auth_enabled():
        return True
    if not header_value or not header_value.startswith("Basic "):
        return False

    decoded = _decode_basic_token(header_value[6:])
    if decoded is None:
        return False

    username, password = decoded
    return hmac.compare_digest(username, settings.api.auth_username) and hmac.compare_digest(
        password,
        settings.api.auth_password,
    )


def unauthorized_response() -> JSONResponse:
    METRICS.inc("auth.failure")
    return JSONResponse(
        status_code=401,
        content={
            "error": {
                "code": "unauthorized",
                "message": "Authentication required",
                "context": None,
            }
        },
        headers={"WWW-Authenticate": 'Basic realm="InfraWatch"'},
    )


async def enforce_basic_auth(request: Request):
    if not is_auth_enabled() or is_exempt_path(request.url.path):
        return None
    if is_authorized_header(request.headers.get("Authorization")):
        return None

    logger.warning("event=auth_failure path=%s client=%s", request.url.path, request.client.host if request.client else "unknown")
    return unauthorized_response()
