import time
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from trading_rag.api.schemas import AskRequest, AskResponse, AskV2Response, ErrorDetail
from trading_rag.clients import es_client, redis_client
from trading_rag.observability import METRICS, Timer
from trading_rag.agents import run_workflow, astream_workflow
from fastapi.responses import StreamingResponse
import json
from trading_rag.pii import redact_pii
from trading_rag.rate_limit import RATE_LIMITER
from trading_rag.audit import emit_audit_event


router = APIRouter()


@router.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest, http_request: Request):
    start_time = time.time()
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not RATE_LIMITER.allow(client_ip):
        raise HTTPException(status_code=429, detail=ErrorDetail(message="Rate limit exceeded").model_dump())
    
    try:
        redacted_query = redact_pii(request.query)
        with Timer("api.ask.total"):
            result = await run_workflow(redacted_query)
        
        processing_time = int((time.time() - start_time) * 1000)
        result.processing_time_ms = processing_time
        
        METRICS.inc("api.ask.count")
        if result.from_cache:
            METRICS.inc("api.ask.cache_hit")
        emit_audit_event({
            "endpoint": "/ask",
            "query": redacted_query,
            "from_cache": result.from_cache,
            "latency_ms": processing_time,
        })
        return AskResponse(
            answer=result.answer,
            baseline_comparison=result.baseline_comparison,
            citations=result.citations,
            query_path=result.query_path.value,
            reflections=result.reflections,
            processing_time_ms=result.processing_time_ms,
            from_cache=result.from_cache,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=ErrorDetail(message=str(e)).model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=ErrorDetail(message="Internal server error", context={"error": str(e)}).model_dump())


@router.post("/ask/stream")
async def ask_stream(request: AskRequest, http_request: Request):
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not RATE_LIMITER.allow(client_ip):
        raise HTTPException(status_code=429, detail=ErrorDetail(message="Rate limit exceeded").model_dump())
    redacted_query = redact_pii(request.query)
    async def event_generator():
        try:
            async for event in astream_workflow(redacted_query):
                # Format event for SSE
                # Each event represents a node completion or cache hit
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/v2/ask", response_model=AskV2Response)
async def ask_v2(request: AskRequest, http_request: Request):
    start_time = time.time()
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not RATE_LIMITER.allow(client_ip):
        raise HTTPException(status_code=429, detail=ErrorDetail(message="Rate limit exceeded").model_dump())

    try:
        redacted_query = redact_pii(request.query)
        with Timer("api.ask_v2.total"):
            result = await run_workflow(redacted_query)
        latency_ms = int((time.time() - start_time) * 1000)
        result.processing_time_ms = latency_ms
        METRICS.inc("api.ask_v2.count")
        if result.from_cache:
            METRICS.inc("api.ask_v2.cache_hit")
        emit_audit_event({
            "endpoint": "/v2/ask",
            "query": redacted_query,
            "from_cache": result.from_cache,
            "latency_ms": latency_ms,
            "groundedness_score": result.groundedness_score,
            "correctness_score": result.correctness_score,
            "cost_usd": result.cost_usd,
        })
        return AskV2Response(
            answer=result.answer,
            citations=[{"log_id": c, "timestamp": None} for c in result.citations],
            confidence=_confidence_label(result.groundedness_score, result.correctness_score),
            groundedness_score=result.groundedness_score,
            correctness_score=result.correctness_score,
            citation_score=result.citation_score,
            retrieval_stats=result.retrieval_stats,
            data_freshness=result.data_freshness,
            cached_at=result.cached_at,
            latency_ms=latency_ms,
            from_cache=result.from_cache,
            cost_usd=result.cost_usd,
            cost_limit_hit=result.cost_limit_hit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=ErrorDetail(message=str(e)).model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=ErrorDetail(message="Internal server error", context={"error": str(e)}).model_dump())


def _confidence_label(groundedness: float | None, correctness: float | None) -> str:
    if groundedness is None or correctness is None:
        return "LOW"
    if groundedness >= 0.90 and correctness >= 0.90:
        return "HIGH"
    if groundedness >= 0.75 and correctness >= 0.75:
        return "MEDIUM"
    return "LOW"


@router.get("/health")
async def health():
    return {"status": "healthy"}


@router.get("/health/ready")
async def readiness():
    es_ok = False
    redis_ok = False
    try:
        es_client.client.info()
        es_ok = True
    except Exception:
        es_ok = False
    try:
        redis_client.client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    status = "ready" if es_ok and redis_ok else "degraded"
    return {"status": status, "elasticsearch": es_ok, "redis": redis_ok}


@router.get("/metrics")
async def metrics():
    return {"metrics": METRICS.snapshot(), "text": METRICS.to_text()}


@router.get("/api/stats")
async def dashboard_stats():
    """Real-time dashboard statistics from Elasticsearch for the Finspot Rag UI."""
    index = es_client.get_execution_logs_index()
    time_range = {"start": "2020-01-01T00:00:00", "end": "2030-01-01T00:00:00"}
    _STATUS_MAP = {48: "FILLED", 65: "OPEN", 110: "NEW", 67: "CANCELLED",
                   56: "REJECTED", 98: "AMO", 50: "PARTIAL", 52: "REPLACED"}

    def run_esql(query: str) -> dict:
        try:
            return es_client.execute_esql(query, time_range)
        except Exception as e:
            logger.warning(f"Stats sub-query failed: {e}")
            return {"columns": [], "values": []}

    def to_rows(result: dict) -> list[dict]:
        cols = [c["name"] for c in result.get("columns", [])]
        return [dict(zip(cols, r)) for r in result.get("values", [])]

    try:
        # 1. Overall totals
        t_rows = to_rows(run_esql(
            f'FROM "{index}" | WHERE msg_type == "ordupd"'
            f' | STATS total_orders = COUNT(),'
            f' fill_rate = AVG(CASE(OrdStatus == 48, 1.0, 0.0)),'
            f' cancel_rate = AVG(CASE(OrdStatus == 67, 1.0, 0.0)),'
            f' buy_orders = SUM(CASE(TransType == "B", 1, 0)),'
            f' sell_orders = SUM(CASE(TransType == "S", 1, 0)),'
            f' total_qty = SUM(QtyToFill)'
        ))
        totals = t_rows[0] if t_rows else {}

        # 2. Exchange breakdown
        exchanges = to_rows(run_esql(
            f'FROM "{index}" | WHERE msg_type == "ordupd"'
            f' | STATS count = COUNT() BY ExchSeg'
            f' | SORT count DESC | LIMIT 10'
        ))

        # 3. Status distribution
        raw_statuses = to_rows(run_esql(
            f'FROM "{index}" | WHERE msg_type == "ordupd"'
            f' | STATS count = COUNT() BY OrdStatus'
            f' | SORT count DESC'
        ))
        statuses = [
            {**r, "label": _STATUS_MAP.get(r.get("OrdStatus"), "UNKNOWN")}
            for r in raw_statuses
        ]

        # 4. Top 10 symbols
        top_symbols = to_rows(run_esql(
            f'FROM "{index}" | WHERE msg_type == "ordupd" AND ticker IS NOT NULL'
            f' | STATS count = COUNT() BY ticker'
            f' | SORT count DESC | LIMIT 10'
        ))

        # 5. Hourly distribution
        hourly_rows = to_rows(run_esql(
            f'FROM "{index}" | WHERE msg_type == "ordupd"'
            f' | STATS count = COUNT() BY hour = DATE_EXTRACT("HOUR_OF_DAY", @timestamp)'
            f' | SORT hour ASC'
        ))
        hourly = [0] * 24
        for r in hourly_rows:
            h = int(r.get("hour") or 0)
            if 0 <= h < 24:
                hourly[h] = int(r.get("count") or 0)

        # 6. Broker breakdown (top 10)
        brokers = to_rows(run_esql(
            f'FROM "{index}" | WHERE msg_type == "ordupd" AND BrokerId IS NOT NULL'
            f' | STATS count = COUNT() BY BrokerId'
            f' | SORT count DESC | LIMIT 10'
        ))

        return {
            "totals": totals,
            "exchanges": exchanges,
            "statuses": statuses,
            "top_symbols": top_symbols,
            "hourly": hourly,
            "brokers": brokers,
            "last_updated": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": "Stats query failed", "error": str(e)},
        )
