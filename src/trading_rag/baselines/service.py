from datetime import datetime
from typing import Any
import hashlib

from trading_rag.clients import es_client, redis_client
from trading_rag.models import BaselineStats


def get_baseline(symbol: str | None, hour: int) -> BaselineStats | None:
    cached = redis_client.get_baseline(symbol, hour)
    if cached:
        # If 'source' is in cached, it will be overridden if passed as keyword
        # To be safe, remove it from dict first if we want to force "redis"
        cached.pop("source", None)
        return BaselineStats(**cached, source="redis")
    
    return fetch_baseline_from_es(symbol, hour)


def fetch_baseline_from_es(symbol: str | None, hour: int) -> BaselineStats | None:
    start_hour = datetime.utcnow().replace(hour=hour, minute=0, second=0, microsecond=0)
    end_hour = start_hour.replace(minute=59, second=59)
    
    symbol_filter = f'AND symbol == "{symbol}"' if symbol else ""
    
    esql = f"""
    FROM "{es_client.get_execution_logs_index()}"
    | WHERE @timestamp >= "{start_hour.isoformat()}" AND @timestamp <= "{end_hour.isoformat()}" {symbol_filter}
    | STATS avg_latency = AVG(latency_ms),
            avg_volume = AVG(volume),
            error_rate = AVG(CASE(status == "error", 1.0, 0.0)),
            p95_latency = PERCENTILE(latency_ms, 95)
    | LIMIT 1
    """
    
    try:
        result = es_client.execute_esql(esql, {"start": start_hour.isoformat(), "end": end_hour.isoformat()})
        
        if result.get("values") and len(result["values"]) > 0:
            row = result["values"][0]
            if len(row) >= 4:
                baseline_data = {
                    "symbol": symbol,
                    "hour": hour,
                    "avg_latency_ms": row[0],
                    "avg_volume": row[1],
                    "error_rate": row[2],
                    "p95_latency_ms": row[3],
                    "source": "elasticsearch"
                }
                baseline = BaselineStats(**baseline_data)
                
                redis_client.set_baseline(symbol, hour, baseline.model_dump())
                return baseline
    except Exception:
        pass
    
    return None


def compute_baseline(symbol: str | None, hour: int) -> BaselineStats | None:
    baseline = fetch_baseline_from_es(symbol, hour)
    if baseline:
        redis_client.set_baseline(symbol, hour, baseline.model_dump())
    return baseline


def get_or_compute_baseline(symbol: str | None, hour: int | None = None) -> BaselineStats | None:
    if hour is None:
        hour = datetime.utcnow().hour
    
    baseline = get_baseline(symbol, hour)
    if baseline is None:
        baseline = compute_baseline(symbol, hour)
    
    return baseline


def get_default_baseline() -> BaselineStats:
    return BaselineStats(
        symbol=None,
        hour=datetime.utcnow().hour,
        avg_latency_ms=100.0,
        avg_volume=1000.0,
        error_rate=0.01,
        p95_latency_ms=200.0,
        source="default",
    )
