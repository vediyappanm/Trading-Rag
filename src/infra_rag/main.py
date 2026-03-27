import logging
import os
import uuid
import time
import contextvars
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env EARLY — before any LangChain/LangGraph imports pick up env vars
from dotenv import load_dotenv
_BASE = Path(__file__).resolve().parent.parent.parent
load_dotenv(_BASE / ".env", override=False)

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from infra_rag.config import settings
from infra_rag.api import router
from infra_rag.clients import es_client, redis_client, prom_client, grafana_client
from infra_rag.auth import enforce_basic_auth
from infra_rag.observability import METRICS

# Explicitly find index.html
BASE_DIR = _BASE
INDEX_FILE = BASE_DIR / "index.html"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Suppress noisy health check and transport logs
logging.getLogger("elastic_transport.transport").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def _configure_langsmith():
    """Enable LangSmith tracing if API key is set."""
    api_key = os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY")
    if api_key:
        # Set both old (LANGCHAIN_*) and new (LANGSMITH_*) env vars
        for prefix in ("LANGCHAIN", "LANGSMITH"):
            os.environ.setdefault(f"{prefix}_TRACING_V2", "true")
            os.environ.setdefault(f"{prefix}_API_KEY", api_key)
            os.environ.setdefault(f"{prefix}_PROJECT", "infrawatch-rag")
            os.environ.setdefault(f"{prefix}_ENDPOINT", "https://api.smith.langchain.com")
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        logger.info(
            "LangSmith tracing enabled — project=%s",
            os.environ.get("LANGSMITH_PROJECT"),
        )
    else:
        logger.info("LangSmith tracing disabled (no API key)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_langsmith()
    logger.info(f"Lifecycle starting. Base dir: {BASE_DIR}")
    logger.info(f"Index file: {INDEX_FILE} (exists: {INDEX_FILE.exists()})")
    yield

app = FastAPI(title="InfraWatch RAG", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _SuppressHealthLogs(logging.Filter):
    """Filter out /health* access log lines to reduce noise."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/health" not in msg


logging.getLogger("uvicorn.access").addFilter(_SuppressHealthLogs())


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth_response = await enforce_basic_auth(request)
        if auth_response is not None:
            return auth_response
        return await call_next(request)


def _error_payload(code: str, message: str, context: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "context": context}}


@app.exception_handler(HTTPException)
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: Exception):
    status_code = getattr(exc, "status_code", 500)
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and "message" in detail:
        message = detail["message"]
        context = detail.get("context")
    else:
        message = str(detail) if detail else "Request failed"
        context = None

    code = "http_error" if status_code >= 500 else "request_error"
    METRICS.inc(f"api.error.{status_code}")
    return JSONResponse(status_code=status_code, content=_error_payload(code, message, context))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("event=unhandled_exception path=%s", request.url.path)
    METRICS.inc("api.error.unhandled")
    return JSONResponse(
        status_code=500,
        content=_error_payload("internal_error", "Internal server error"),
    )

@app.get("/")
async def root():
    logger.info("Root path hit")
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE)
    return HTMLResponse(f"<h1>index.html not found at {INDEX_FILE}</h1>")

app.include_router(router)
app.add_middleware(AuthMiddleware)

@app.get("/debug-paths")
async def debug_paths():
    return {
        "file": str(__file__),
        "base_dir": str(BASE_DIR),
        "index_file": str(INDEX_FILE),
        "exists": INDEX_FILE.exists(),
        "cwd": os.getcwd() if hasattr(os, "cwd") else "unknown"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
