import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import uuid
import time
import contextvars

from trading_rag.config import settings
from trading_rag.api import router
from trading_rag.clients import es_client, redis_client

# Project root: 2 levels up from src/trading_rag/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INDEX_HTML = PROJECT_ROOT / "index.html"

logging.basicConfig(
    level=getattr(logging, settings.api.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.info(f"Project root: {PROJECT_ROOT}")
logger.info(f"index.html path: {INDEX_HTML}, exists: {INDEX_HTML.exists()}")

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Trading RAG Agent...")

    try:
        es_client.client.info()
        logger.info("Elasticsearch connection established")
    except Exception as e:
        logger.warning(f"Elasticsearch connection failed: {e}")

    try:
        redis_client.client.ping()
        logger.info("Redis connection established")
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")

    logger.info("Trading RAG Agent initialized successfully")

    yield

    logger.info("Shutting down Trading RAG Agent...")
    es_client.close()
    redis_client.close()
    logger.info("Connections closed")


app = FastAPI(
    title="Trading RAG Agent",
    description="Multi-agent RAG system for trading log analysis",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.api.enable_gzip:
    app.add_middleware(GZipMiddleware, minimum_size=settings.api.gzip_min_size)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request_id_ctx.set(request_id)
        start = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)
        response.headers["X-Request-Id"] = request_id
        response.headers["Server-Timing"] = f"total;dur={duration_ms}"
        logger.info(
            "request complete",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


app.add_middleware(RequestContextMiddleware)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    if INDEX_HTML.exists():
        return HTMLResponse(content=INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>UI not found. Put index.html in project root.</h1>",
        status_code=200,
    )


app.include_router(router)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"message": "Internal server error", "context": {"error": str(exc)}},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "trading_rag.main:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=True,
    )
