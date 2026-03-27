import time
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Request
from infra_rag.api.schemas import AskRequest, AskResponse, AskV2Response, ErrorDetail
from infra_rag.clients import es_client, redis_client
from infra_rag.observability import METRICS, Timer
from infra_rag.agents import run_workflow, astream_workflow
from fastapi.responses import StreamingResponse
import json
import logging
from infra_rag.pii import redact_pii
from infra_rag.rate_limit import RATE_LIMITER
from infra_rag.audit import emit_audit_event
from infra_rag.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()
_LIVE_ENDPOINT_CACHE: dict[str, tuple[float, dict]] = {}


def _error_detail(message: str, context: dict | None = None) -> dict:
    return ErrorDetail(message=message, context=context).model_dump()


def _sanitize_stream_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ValueError):
        return "request_error", str(exc)
    message = str(exc).lower()
    if "timed out" in message:
        return "timeout", "Request timed out while retrieving infrastructure data"
    if "rate limit" in message:
        return "rate_limit", "Rate limit exceeded"
    return "internal_error", "Request failed while processing the query"


def _stream_event(event: dict, seq: int, final: bool = False) -> str:
    payload = {"seq": seq, "final": final, **event}
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest, http_request: Request):
    start_time = time.time()
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not RATE_LIMITER.allow(client_ip):
        METRICS.inc("api.ask.rate_limit")
        raise HTTPException(status_code=429, detail=_error_detail("Rate limit exceeded"))

    try:
        redacted_query = redact_pii(request.query)
        with Timer("api.ask.total"):
            result = await run_workflow(redacted_query)

        processing_time = int((time.time() - start_time) * 1000)
        result.processing_time_ms = processing_time
        logger.info(
            "event=api_ask_success path=/ask domain=%s cache=%s latency_ms=%s",
            result.domain.value if hasattr(result.domain, "value") else str(result.domain),
            result.from_cache,
            processing_time,
        )

        METRICS.inc("api.ask.count")
        if result.from_cache:
            METRICS.inc("api.ask.cache_hit")
        emit_audit_event({
            "endpoint": "/ask",
            "query": redacted_query,
            "domain": result.domain.value if hasattr(result.domain, "value") else str(result.domain),
            "from_cache": result.from_cache,
            "latency_ms": processing_time,
        })
        return AskResponse(
            answer=result.answer,
            domain=result.domain.value if hasattr(result.domain, "value") else str(result.domain),
            baseline_comparison=result.baseline_comparison,
            citations=result.citations,
            query_path=result.query_path.value,
            reflections=result.reflections,
            processing_time_ms=result.processing_time_ms,
            from_cache=result.from_cache,
        )
    except ValueError as e:
        logger.warning("event=api_ask_invalid path=/ask error=%s", e)
        raise HTTPException(status_code=400, detail=_error_detail(str(e)))
    except Exception as e:
        logger.exception("event=api_ask_failure path=/ask")
        raise HTTPException(status_code=500, detail=_error_detail("Internal server error"))


@router.post("/ask/stream")
async def ask_stream(request: AskRequest, http_request: Request):
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not RATE_LIMITER.allow(client_ip):
        METRICS.inc("api.ask_stream.rate_limit")
        raise HTTPException(status_code=429, detail=_error_detail("Rate limit exceeded"))
    redacted_query = redact_pii(request.query)

    async def event_generator():
        seq = 0
        emitted_final = False
        try:
            async for event in astream_workflow(redacted_query):
                seq += 1
                is_final = bool(event.get("final_response")) or bool(event.get("error")) or event.get("type") == "cache_hit"
                if event.get("error"):
                    payload = {"type": "error", "error": {"code": "workflow_error", "message": str(event["error"]), "context": None}}
                else:
                    payload = event
                if is_final:
                    emitted_final = True
                yield _stream_event(payload, seq=seq, final=is_final)
        except Exception as e:
            logger.exception("event=api_stream_failure path=/ask/stream")
            seq += 1
            code, message = _sanitize_stream_error(e)
            emitted_final = True
            METRICS.inc("api.ask_stream.failure")
            yield _stream_event({"type": "error", "error": {"code": code, "message": message, "context": None}}, seq=seq, final=True)
        finally:
            if not emitted_final:
                seq += 1
                METRICS.inc("api.ask_stream.incomplete")
                yield _stream_event(
                    {"type": "error", "error": {"code": "incomplete_stream", "message": "Stream ended without a final response", "context": None}},
                    seq=seq,
                    final=True,
                )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/v2/ask", response_model=AskV2Response)
async def ask_v2(request: AskRequest, http_request: Request):
    start_time = time.time()
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not RATE_LIMITER.allow(client_ip):
        METRICS.inc("api.ask_v2.rate_limit")
        raise HTTPException(status_code=429, detail=_error_detail("Rate limit exceeded"))

    try:
        redacted_query = redact_pii(request.query)
        with Timer("api.ask_v2.total"):
            result = await run_workflow(redacted_query)
        latency_ms = int((time.time() - start_time) * 1000)
        result.processing_time_ms = latency_ms
        logger.info(
            "event=api_ask_success path=/v2/ask domain=%s cache=%s latency_ms=%s",
            result.domain.value if hasattr(result.domain, "value") else str(result.domain),
            result.from_cache,
            latency_ms,
        )
        METRICS.inc("api.ask_v2.count")
        if result.from_cache:
            METRICS.inc("api.ask_v2.cache_hit")
        emit_audit_event({
            "endpoint": "/v2/ask",
            "query": redacted_query,
            "domain": result.domain.value if hasattr(result.domain, "value") else str(result.domain),
            "from_cache": result.from_cache,
            "latency_ms": latency_ms,
            "groundedness_score": result.groundedness_score,
            "correctness_score": result.correctness_score,
            "cost_usd": result.cost_usd,
        })
        return AskV2Response(
            answer=result.answer,
            domain=result.domain.value if hasattr(result.domain, "value") else str(result.domain),
            citations=[{"source": c, "timestamp": None} for c in result.citations],
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
        logger.warning("event=api_ask_invalid path=/v2/ask error=%s", e)
        raise HTTPException(status_code=400, detail=_error_detail(str(e)))
    except Exception as e:
        logger.exception("event=api_ask_failure path=/v2/ask")
        raise HTTPException(status_code=500, detail=_error_detail("Internal server error"))


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
    from infra_rag.clients import prom_client, grafana_client

    es_ok = False
    redis_ok = False
    prom_ok = False
    grafana_ok = False
    llm_ok = bool(
        (settings.llm.provider == "openai" and settings.llm.openai_api_key)
        or (settings.llm.provider == "anthropic" and settings.llm.anthropic_api_key)
        or (settings.llm.provider == "groq" and settings.llm.groq_api_key)
    )
    try:
        es_client.client.info()
        es_ok = True
    except Exception:
        pass
    try:
        redis_client.client.ping()
        redis_ok = True
    except Exception:
        pass
    try:
        prom_ok = prom_client.is_available()
    except Exception:
        pass
    try:
        grafana_ok = grafana_client.is_available()
    except Exception:
        pass

    core_ok = redis_ok and llm_ok and (es_ok or prom_ok)
    status = "ready" if core_ok else "degraded"
    return {
        "status": status,
        "elasticsearch": es_ok,
        "redis": redis_ok,
        "prometheus": prom_ok,
        "grafana": grafana_ok,
        "llm_configured": llm_ok,
    }


@router.get("/metrics")
async def metrics():
    return {"metrics": METRICS.snapshot(), "text": METRICS.to_text()}


@router.get("/api/stats")
async def dashboard_stats():
    """Infrastructure dashboard statistics from Elasticsearch."""
    stats = {}
    now = datetime.utcnow()
    hour_ago = (now - timedelta(hours=1)).isoformat()
    half_hour_ago = (now - timedelta(minutes=30)).isoformat()

    def safe_esql(index: str, query: str) -> list[dict]:
        try:
            time_range = {"start": "2020-01-01T00:00:00", "end": "2030-01-01T00:00:00"}
            result = es_client.execute_esql(query, time_range)
            cols = [c["name"] for c in result.get("columns", [])]
            return [dict(zip(cols, r)) for r in result.get("values", [])]
        except Exception as e:
            logger.warning(f"Stats query failed for {index}: {e}")
            return []

    # Metrics index stats
    metrics_index = settings.elasticsearch.metrics_index
    try:
        host_metrics = safe_esql(metrics_index, f"""
FROM "{metrics_index}"
| WHERE @timestamp >= "{hour_ago}"
| STATS avg_cpu = AVG(cpu.usage_pct),
        max_cpu = MAX(cpu.usage_pct),
        avg_memory = AVG(memory.usage_pct),
        max_memory = MAX(memory.usage_pct),
        avg_disk = AVG(disk.usage_pct),
        hosts = COUNT_DISTINCT(host.name)
""")
        stats["infrastructure"] = host_metrics[0] if host_metrics else {}
    except Exception:
        stats["infrastructure"] = {}

    # Log level distribution
    logs_index = settings.elasticsearch.logs_index
    try:
        log_levels = safe_esql(logs_index, f"""
FROM "{logs_index}"
| WHERE @timestamp >= "{hour_ago}"
| STATS count = COUNT() BY log.level
| SORT count DESC
""")
        stats["log_levels"] = log_levels
    except Exception:
        stats["log_levels"] = []

    # Active alerts
    try:
        from infra_rag.clients import prom_client
        firing = [a for a in prom_client.get_alerts() if a.get("state") == "firing"]
        stats["active_alerts"] = [
            {
                "alertname": a.get("labels", {}).get("alertname", "unknown"),
                "severity": a.get("labels", {}).get("severity", "warning"),
                "count": 1,
            }
            for a in firing[:10]
        ]
    except Exception:
        stats["active_alerts"] = []

    # Top services by error count
    try:
        error_services = safe_esql(logs_index, f"""
FROM "{logs_index}"
| WHERE @timestamp >= "{hour_ago}" AND log.level IN ("ERROR", "FATAL")
| STATS errors = COUNT() BY service.name
| SORT errors DESC
| LIMIT 10
""")
        stats["error_services"] = error_services
    except Exception:
        stats["error_services"] = []

    try:
        recent_events = safe_esql(logs_index, f"""
FROM "{logs_index}"
| WHERE @timestamp >= "{half_hour_ago}" AND log.level IN ("ERROR", "WARN", "FATAL")
| SORT @timestamp DESC
| LIMIT 12
| KEEP @timestamp, service.name, host.name, log.level, message
""")
        stats["recent_events"] = recent_events
    except Exception:
        stats["recent_events"] = []

    try:
        snapshot = METRICS.snapshot()
        latency = snapshot.get("timings", {}).get("api.ask.total", {})
        stats["request_latency"] = {
            "count": latency.get("count", 0),
            "avg_ms": round(latency.get("avg_ms", 0.0), 1) if latency else 0.0,
            "p95_ms": round(latency.get("p95_ms", 0.0), 1) if latency else 0.0,
            "recent_ms": [round(v, 1) for v in METRICS.recent_timing_values("api.ask.total", limit=24)],
        }
    except Exception:
        stats["request_latency"] = {"count": 0, "avg_ms": 0.0, "p95_ms": 0.0, "recent_ms": []}

    stats["last_updated"] = datetime.utcnow().isoformat() + "Z"
    return stats


@router.get("/api/live-metrics")
async def live_metrics():
    """Real-time infrastructure metrics from Prometheus for the dashboard KPI bar."""
    from infra_rag.clients import prom_client
    from infra_rag.clients.prometheus import (
        promql_cpu, promql_memory, promql_disk,
        promql_network_rx, promql_network_tx,
    )
    cache_key = "live_metrics"
    cached = _LIVE_ENDPOINT_CACHE.get(cache_key)
    now_ts = time.time()
    if cached and (now_ts - cached[0]) < 10:
        return cached[1]

    data: dict = {
        "kpi": {"cpu": None, "memory": None, "disk": None, "network_in": None, "alerts": 0},
        "hosts": [],
        "sparklines": {"cpu": [], "memory": [], "disk": []},
        "targets": {"total": 0, "healthy": 0},
        "alerts": {"firing": [], "total": 0},
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    cpu_data = None
    mem_data = None
    disk_data = None
    rx_data = None

    # ── KPI: Current aggregate values ──
    try:
        cpu_data = prom_client.query_instant(promql_cpu())
        if cpu_data.get("result"):
            vals = [float(r["value"][1]) for r in cpu_data["result"] if r["value"][1] != "NaN"]
            data["kpi"]["cpu"] = round(sum(vals) / len(vals), 1) if vals else None
    except Exception:
        pass

    try:
        mem_data = prom_client.query_instant(promql_memory())
        if mem_data.get("result"):
            vals = [float(r["value"][1]) for r in mem_data["result"] if r["value"][1] != "NaN"]
            data["kpi"]["memory"] = round(sum(vals) / len(vals), 1) if vals else None
    except Exception:
        pass

    try:
        disk_data = prom_client.query_instant(promql_disk())
        if disk_data.get("result"):
            vals = [float(r["value"][1]) for r in disk_data["result"] if r["value"][1] != "NaN"]
            data["kpi"]["disk"] = round(sum(vals) / len(vals), 1) if vals else None
    except Exception:
        pass

    try:
        rx_data = prom_client.query_instant(promql_network_rx())
        if rx_data.get("result"):
            vals = [float(r["value"][1]) for r in rx_data["result"] if r["value"][1] != "NaN"]
            data["kpi"]["network_in"] = round(sum(vals) / (1024 * 1024), 1) if vals else None
    except Exception:
        pass

    # ── Per-host breakdown ──
    try:
        cpu_per = cpu_data or prom_client.query_instant(promql_cpu())
        mem_per = mem_data or prom_client.query_instant(promql_memory())
        disk_per = disk_data or prom_client.query_instant(promql_disk())
        rx_per = rx_data or prom_client.query_instant(promql_network_rx())
        tx_per = prom_client.query_instant(promql_network_tx())

        host_map: dict = {}
        for r in (cpu_per or {}).get("result", []):
            inst = r["metric"].get("instance", "unknown")
            host_map.setdefault(inst, {"name": inst, "cpu": None, "memory": None, "disk": None, "network_in": None, "network_out": None})
            host_map[inst]["cpu"] = round(float(r["value"][1]), 1) if r["value"][1] != "NaN" else None

        for r in (mem_per or {}).get("result", []):
            inst = r["metric"].get("instance", "unknown")
            host_map.setdefault(inst, {"name": inst, "cpu": None, "memory": None, "disk": None, "network_in": None, "network_out": None})
            host_map[inst]["memory"] = round(float(r["value"][1]), 1) if r["value"][1] != "NaN" else None

        for r in (disk_per or {}).get("result", []):
            inst = r["metric"].get("instance", "unknown")
            host_map.setdefault(inst, {"name": inst, "cpu": None, "memory": None, "disk": None, "network_in": None, "network_out": None})
            host_map[inst]["disk"] = round(float(r["value"][1]), 1) if r["value"][1] != "NaN" else None

        for r in (rx_per or {}).get("result", []):
            inst = r["metric"].get("instance", "unknown")
            host_map.setdefault(inst, {"name": inst, "cpu": None, "memory": None, "disk": None, "network_in": None, "network_out": None})
            host_map[inst]["network_in"] = round(float(r["value"][1]) / (1024 * 1024), 1) if r["value"][1] != "NaN" else None

        for r in (tx_per or {}).get("result", []):
            inst = r["metric"].get("instance", "unknown")
            host_map.setdefault(inst, {"name": inst, "cpu": None, "memory": None, "disk": None, "network_in": None, "network_out": None})
            host_map[inst]["network_out"] = round(float(r["value"][1]) / (1024 * 1024), 1) if r["value"][1] != "NaN" else None

        for h in host_map.values():
            cpu_v = h.get("cpu") or 0
            mem_v = h.get("memory") or 0
            if cpu_v > 85 or mem_v > 90:
                h["status"] = "critical"
            elif cpu_v > 70 or mem_v > 80:
                h["status"] = "warning"
            else:
                h["status"] = "ok"

        data["hosts"] = sorted(host_map.values(), key=lambda x: x.get("cpu") or 0, reverse=True)
    except Exception:
        pass

    # ── Sparkline data (last 1h, 12 points = 5min intervals) ──
    try:
        from datetime import timedelta as _td
        now = datetime.utcnow()
        start = now - _td(hours=1)

        # Use avg without by(instance) to get a single aggregate series
        cpu_spark_q = 'avg(100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100))'
        cpu_range = {"result": []}
        for series in cpu_range.get("result", []):
            data["sparklines"]["cpu"] = [
                round(float(v[1]), 1) if v[1] != "NaN" else 0
                for v in series.get("values", [])
            ]

        mem_spark_q = "avg((1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100)"
        mem_range = {"result": []}
        for series in mem_range.get("result", []):
            data["sparklines"]["memory"] = [
                round(float(v[1]), 1) if v[1] != "NaN" else 0
                for v in series.get("values", [])
            ]

        disk_spark_q = 'avg((1 - (node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})) * 100)'
        disk_range = {"result": []}
        for series in disk_range.get("result", []):
            data["sparklines"]["disk"] = [
                round(float(v[1]), 1) if v[1] != "NaN" else 0
                for v in series.get("values", [])
            ]
    except Exception:
        pass

    # ── Targets health ──
    try:
        targets = prom_client.get_targets()
        data["targets"]["total"] = len(targets)
        data["targets"]["healthy"] = sum(1 for t in targets if t.get("health") == "up")
    except Exception:
        pass

    # ── Firing alerts ──
    try:
        alerts = prom_client.get_alerts()
        firing = [a for a in alerts if a.get("state") == "firing"]
        data["alerts"]["firing"] = [
            {
                "name": a.get("labels", {}).get("alertname", "unknown"),
                "severity": a.get("labels", {}).get("severity", "warning"),
                "instance": a.get("labels", {}).get("instance", ""),
            }
            for a in firing[:10]
        ]
        data["alerts"]["total"] = len(firing)
    except Exception:
        pass

    _LIVE_ENDPOINT_CACHE[cache_key] = (now_ts, data)
    return data


@router.get("/api/live-metrics/history")
async def live_metrics_history(range: str = "1h", instance: str | None = None):
    """Time-series data for Chart.js line/area charts."""
    from infra_rag.clients import prom_client
    from infra_rag.clients.prometheus import promql_cpu, promql_memory, promql_disk, promql_network_rx, promql_network_tx

    ranges = {"1h": (3600, "60s"), "6h": (21600, "300s"), "24h": (86400, "900s"), "7d": (604800, "3600s")}
    secs, step = ranges.get(range, (3600, "60s"))
    cache_key = f"history:{range}:{instance or '*'}"
    cached = _LIVE_ENDPOINT_CACHE.get(cache_key)
    now_ts = time.time()
    if cached and (now_ts - cached[0]) < 20:
        return cached[1]

    from datetime import timedelta as _td
    now = datetime.utcnow()
    start = now - _td(seconds=secs)

    data: dict = {"labels": [], "cpu": {}, "memory": {}, "disk": {}, "network_in": [], "network_out": []}

    # Limit to top N instances for chart readability
    max_series = 10

    try:
        cpu_range = prom_client.query_range(promql_cpu(instance), start, now, step=step)
        results = cpu_range.get("result", [])[:max_series]
        for series in results:
            inst = series["metric"].get("instance", "unknown")
            vals = []
            labels = []
            for v in series.get("values", []):
                labels.append(datetime.utcfromtimestamp(v[0]).strftime("%H:%M"))
                vals.append(round(float(v[1]), 1) if v[1] != "NaN" else 0)
            data["cpu"][inst] = vals
            if not data["labels"]:
                data["labels"] = labels
    except Exception as e:
        logger.warning(f"History CPU query failed: {e}")

    try:
        mem_range = prom_client.query_range(promql_memory(instance), start, now, step=step)
        results = mem_range.get("result", [])[:max_series]
        for series in results:
            inst = series["metric"].get("instance", "unknown")
            data["memory"][inst] = [
                round(float(v[1]), 1) if v[1] != "NaN" else 0
                for v in series.get("values", [])
            ]
    except Exception as e:
        logger.warning(f"History memory query failed: {e}")

    try:
        disk_range = prom_client.query_range(promql_disk(instance), start, now, step=step)
        results = disk_range.get("result", [])[:max_series]
        for series in results:
            inst = series["metric"].get("instance", "unknown")
            data["disk"][inst] = [
                round(float(v[1]), 1) if v[1] != "NaN" else 0
                for v in series.get("values", [])
            ]
    except Exception as e:
        logger.warning(f"History disk query failed: {e}")

    try:
        rx_range = prom_client.query_range(f'sum({promql_network_rx(instance)})', start, now, step=step)
        for series in rx_range.get("result", [])[:1]:
            data["network_in"] = [
                round(float(v[1]) / (1024 * 1024), 1) if v[1] != "NaN" else 0
                for v in series.get("values", [])
            ]
            if not data["labels"]:
                data["labels"] = [datetime.utcfromtimestamp(v[0]).strftime("%H:%M") for v in series.get("values", [])]
    except Exception as e:
        logger.warning(f"History network-in query failed: {e}")

    try:
        tx_range = prom_client.query_range(f'sum({promql_network_tx(instance)})', start, now, step=step)
        for series in tx_range.get("result", [])[:1]:
            data["network_out"] = [
                round(float(v[1]) / (1024 * 1024), 1) if v[1] != "NaN" else 0
                for v in series.get("values", [])
            ]
            if not data["labels"]:
                data["labels"] = [datetime.utcfromtimestamp(v[0]).strftime("%H:%M") for v in series.get("values", [])]
    except Exception as e:
        logger.warning(f"History network-out query failed: {e}")

    _LIVE_ENDPOINT_CACHE[cache_key] = (now_ts, data)
    return data
